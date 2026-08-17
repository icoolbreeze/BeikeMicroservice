"""房源信息验证工具：上传证件 → 提取业务件号/产权证号 → 蓉e办实时验证 → 生成查询结果图。

流程：
    1. VL 模型（OpenRouter，默认 nvidia/nemotron-nano-12b-v2-vl:free）从产权证照片中
       提取「业务件号」与「证件编码（产权证号）」两个字段；
    2. Playwright 驱动本机浏览器打开住建蓉e办「房源信息验证」页面，
       填入两个字段并点击「查询」；
    3. 等待查询结果表格出现后截图，生成查询验证图片。

用法：
    python scripts/house_verify.py <产权证图片>
    python scripts/house_verify.py <产权证图片> --headed   # 显示浏览器窗口（调试用）

输出（outputs/house_verify_<时间戳>/，已加入 .gitignore，禁止提交入库）：
    - extracted.json     提取的业务件号与证件编码
    - result_full.png    查询结果完整页面截图
    - result_panel.png   查询结果区域截图（若区域定位成功）
    - page_snapshot.html 结果页 HTML 快照（调试用）

注意：
    - 仅用于本公司业务范围内的房源核验，查询目标为政府公开验证服务；
    - 输入图片含个人敏感信息，仅用于本地核验，请勿外传或提交版本库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_archive import ROOT, call_vl_model, load_api_key, load_nvidia_api_key  # noqa: E402

try:  # 后台运行时确保 print 实时输出
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass

DEFAULT_URL = (
    "https://blmp.cdzjryb.com/fplc_daas_portal/#/integratedQueryNew"
    "?prevPageTitle=%E4%BD%8F%E5%BB%BA%E8%93%89e%E5%8A%9E&code=50"
)

EXTRACT_PROMPT = (
    "请从这张不动产权证照片中提取两个字段，以严格 JSON 输出（不要解释、不要 markdown）：\n"
    "{\n"
    '  "业务件号": "附记中的业务号（常见格式：\n'
    "    新版：2025112701F90697 等「数字F数字」，F 前为办理日期，位数随年代不同（如 2016092042F1234）；\n"
    "    旧版：权1234 等「权」开头的编号）\",\n"
    '  "证件编码": "产权证号（常见格式：\n'
    "    新版：川（2025）成都市不动产权第0373259号；\n"
    "    旧版：监证1234567 等「监证」开头的编号）\"\n"
    "}\n"
    "注意：业务件号请从附记中取，不要取「登记编号」「权证号」等其他编号；"
    "无法辨认的字段填 null。"
)

LOCAL_OCR_PROMPT = "OCR:"

OMNI_PROMPT = (
    "你是不动产登记证件识别专家。请仔细识读这张不动产权证照片，"
    "以严格 JSON 输出（不要解释、不要 markdown）：\n"
    "{\n"
    '  "业务件号": "位于附记栏的业务号",\n'
    '  "证件编码": "位于证件首页右上或上方的产权证号"\n'
    "}\n"
    "两个字段的常见格式（新旧证件差异大，务必按实际文字抄录）：\n"
    "- 业务件号：新版为「数字F数字」（F 前为办理日期，如 2025112701F90697、"
    "2016092042F1234，日期位数随年代变化）；旧版为「权」开头的编号（如 权1234）。\n"
    "- 证件编码：新版为「省（年份）城市不动产权第N号」（如 川（2025）成都市不动产权第0373259号）；"
    "旧版为「监证」开头的编号（如 监证1234567）。\n"
    "注意：业务件号从附记中取，不要与「登记编号」「权证号」「不动产单元号」混淆；"
    "括号按证件原样保留（中文括号）；无法辨认的字段填 null。"
)


def clean_field(value: str) -> str:
    """清洗提取字段：去除所有空格（含中间空格），英文括号统一为中文括号。"""
    if not value:
        return ""
    return re.sub(r"\s+", "", value).replace("(", "（").replace(")", "）")


_BUSINESS_NUMBER_PATTERNS = (
    re.compile(r"^\d{6,12}[A-Z]\d{2,10}$"),   # 新版：办理日期 + 类型字母(常见 F，另有 P 等) + 编号
    re.compile(r"^权\d+$"),                    # 旧版：权 开头编号
)
_CERTIFICATE_NUMBER_PATTERNS = (
    re.compile(r"^监证\d+$"),              # 旧版：监证 开头编号
    re.compile(r"^.*第\d+号$"),            # 新版：省(年份)市不动产权第N号
)


def is_valid_business_number(value: str) -> bool:
    """宽容校验业务件号（新旧版格式）。"""
    return any(p.fullmatch(value or "") for p in _BUSINESS_NUMBER_PATTERNS)


def is_valid_certificate_number(value: str) -> bool:
    """宽容校验证件编码（新旧版格式）。"""
    return any(p.fullmatch(value or "") for p in _CERTIFICATE_NUMBER_PATTERNS)


_BUSY_SEARCH = re.compile(r"\d{6,12}[A-Z]\d{2,10}|权\d+")
# 证件编码前缀只可能是单个汉字（省简称）或 1-3 位数字（行政区代码）。
# 前缀不能放宽到 2-3 个汉字：整页转写去空白后，标题行"…不动产权证"会与
# 号码行相邻，贪婪前缀会吞进"权证"等标题尾字（如"权证川（2021）…"）。
# 城市段要求精确的"成都市"：本服务只对接蓉e办（成都），实测 Q8+CPU 推理
# 在城市字上有"成副市/成新市"级误读——宁可识别失败走云端兜底，也不能
# 把错的证件编码静默传给政务查询。
_CERT_SEARCH = re.compile(
    r"监证\d+|"
    r"(?:[\u4e00-\u9fa5A-Z]|\d{1,3})[（(]\d{4}[）)]成都市不动产权第\s*\d+\s*号"
)


def _ocr_image_data_url(cert_image: Path, target_long_side: int = 2600,
                        max_upscale: float = 1.5) -> str:
    """为本地 OCR 构造 data URL：长边统一重采样到目标尺寸，高质量编码。

    实测矩阵（云端 Q8 + CPU，1279x1748 证件原图）：原生/q88 与 1600 缩放都会
    在小字号处字符级误读（"成都市→成副市"、业务号日期错位）；提高到 q95 或
    2x 放大后 3/3 稳定正确。llama-server 会把图片归一化到固定视觉 token
    预算，像素总量对耗时几乎无影响，起决定作用的是压缩质量——q88 的 JPEG
    伪影会抹掉关键笔画细节，因此这里固定 quality=95。
    """
    import base64
    import io

    from PIL import Image, ImageOps

    with Image.open(cert_image) as source:
        img = ImageOps.exif_transpose(source).convert("RGB")
    long_side = max(img.size)
    if long_side < target_long_side:
        scale = min(max_upscale, target_long_side / long_side)
    elif long_side > target_long_side:
        scale = target_long_side / long_side
    else:
        scale = 1
    if scale != 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def call_llama_ocr(base_url: str, model: str, cert_image: Path,
                   prompt: str, timeout: float) -> dict:
    """调用本地 llama.cpp OCR 服务，并从转写文本中提取证件字段。

    PaddleOCR-VL 是文档解析模型，适合用 ``OCR:`` 触发整页转写；这里不依赖模型
    输出 JSON，而是按现有证件字段规则从文本中搜索业务件号和证件编码。
    """
    import requests

    data_url = _ocr_image_data_url(cert_image)
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                # llama.cpp 官方推荐 OCR 模型"图片在前、提示词在后"的顺序，
                # PaddleOCR-VL 对顺序敏感，颠倒可能导致空转写。
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        text = body["choices"][0]["message"]["content"] or ""
    except Exception as exc:  # noqa: BLE001 - 统一由上层重试/兜底
        raise RuntimeError(f"本地 OCR 服务调用失败：{exc}") from exc

    text = re.sub(r"\s+", "", text)
    busy = _BUSY_SEARCH.findall(text)
    cert = _CERT_SEARCH.findall(text)
    ywh = next((clean_field(v) for v in busy if is_valid_business_number(clean_field(v))), "")
    zsbm = next((clean_field(v) for v in cert if is_valid_certificate_number(clean_field(v))), "")
    if not ywh or not zsbm:
        raise ValueError(
            f"本地 OCR 未能提取有效字段：业务件号={ywh!r} 证件编码={zsbm!r}"
        )
    return {"业务件号": ywh, "证件编码": zsbm}


def _try_extract(task: dict, cert_image: Path, timeout: float) -> dict:
    """单次调用一个视觉模型提取字段，返回清洗后的 dict。

    字段为空或格式不符抛 ValueError；连接异常由 call_vl_model 抛出。
    """
    if task.get("channel") == "llama":
        data = call_llama_ocr(
            task["base_url"], task["model"], cert_image, task["prompt"],
            timeout=max(timeout, 1.0),
        )
    else:
        data = call_vl_model(
            task["api_key"], task["model"], cert_image, task["prompt"],
            enhance_text=True, before_request=task.get("before_request"),
            channel=task.get("channel", "openrouter"),
            timeout=max(int(timeout) - 5, 10), max_attempts=1,
        )
    ywh = clean_field(data.get("业务件号") or "")
    zsbm = clean_field(data.get("证件编码") or "")
    if not is_valid_business_number(ywh) or not is_valid_certificate_number(zsbm):
        raise ValueError(
            f"业务件号={ywh!r} 证件编码={zsbm!r}，格式不符"
        )
    return {"业务件号": ywh, "证件编码": zsbm}


def extract_credentials_chained(tasks: list[dict], cert_image: Path,
                                retries: int = 2, timeout: float = 30) -> dict:
    """顺序调用视觉模型提取字段：主模型失败重试后仍失败，由备用模型兜底。

    ``tasks``：按优先级排列的模型调用任务：
        {label, api_key, model, channel, prompt, before_request}
    ``retries``：每个模型在失败（连接异常或未识别出有效字段）后的重试次数。
    ``timeout``：单次模型调用的超时（秒）。

    返回：
        {"status": "ok", "cred": {...}, "used_label": ...}
    所有模型均失败时抛 ValueError。
    """
    if not tasks:
        raise ValueError("未配置任何视觉模型调用任务")

    def _attempt_with_deadline(task: dict, attempt_timeout: float):
        """在守护线程中执行一次提取，超时即放弃（网络层挂起时 requests 超时不可靠）。"""
        box: dict = {}

        def _run() -> None:
            try:
                box["cred"] = _try_extract(task, cert_image, attempt_timeout)
            except BaseException as exc:  # noqa: BLE001 - 连接异常与格式不符均可重试
                box["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=max(attempt_timeout, 1.0))
        if thread.is_alive():
            return {"timeout": True}
        return box

    last_err: BaseException | None = None
    for index, task in enumerate(tasks):
        label = task["label"]
        if index:
            print(f"  [{label}] 主识别服务未成功，切换备用识别服务兜底")
        max_attempts = 1 + retries
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                print(f"  [{label}] 上次尝试失败，正在进行第 {attempt} 次重试")
            attempt_timeout = task.get("timeout") or timeout
            box = _attempt_with_deadline(task, attempt_timeout)
            if box.get("cred"):
                cred = box["cred"]
                print(f"  [{label}] 提取成功："
                      f"业务件号={cred['业务件号']} 证件编码={cred['证件编码']}")
                if task.get("on_success"):
                    try:
                        task["on_success"]()
                    except Exception:  # noqa: BLE001 - 回调失败不影响主流程
                        pass
                return {"status": "ok", "cred": cred, "used_label": label}
            error = box.get("error")
            if error is not None:
                last_err = error
                print(f"  [{label}] 提取失败（第 {attempt}/{max_attempts} 次）：{error}")
            else:
                print(f"  [{label}] 提取超时（第 {attempt}/{max_attempts} 次，"
                      f"超过 {attempt_timeout}s 未返回），放弃本次尝试")
        if task.get("on_failure") and last_err is not None:
            try:
                task["on_failure"](last_err)
            except Exception:  # noqa: BLE001 - 回调失败不影响主流程
                pass
    raise ValueError(f"所有识别服务均未能提取有效字段：{last_err}")


def _build_result(headers: list[str], cells: list[str]) -> dict:
    """组装官方查询结果的原始数据。

    返回 {headers, row, fields}；表头与数据列数不一致时 fields 留空
    （只保留原始 headers/row），避免错位。不做任何总结——结论由调用方
    （agent）根据数据自行归纳。
    """
    result = {"headers": headers, "row": cells, "fields": {}}
    if headers and cells and len(headers) == len(cells):
        result["fields"] = dict(zip(headers, cells))
    return result


def _first_visible(page, selectors: list[str], timeout: int = 8000):
    """按候选选择器顺序找第一个可见元素。"""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:  # noqa: BLE001 - 尝试下一个候选选择器
            continue
    return None


def adapt_table_width(page, ywh: str) -> None:
    """适配结果表格宽度：表头/单元格强制单行显示，并按需加宽视口。

    网站结果表默认列宽不足，「门牌号」「是否抵押」等表头及长文本单元格会换行。
    这里注入 nowrap 样式、表格 table-layout:auto + width:max-content，
    放开祖先容器的横向约束，再把视口加宽到表格自然宽度，使截图不出现换行。
    """
    needed = page.evaluate(
        """(ywh) => {
          const style = document.createElement('style');
          style.id = '__hv_nowrap';
          style.textContent = `
            th, td { white-space: nowrap !important; }
            th *, td * { white-space: nowrap !important;
                          overflow: visible !important; text-overflow: clip !important; }
            table { table-layout: auto !important;
                    width: max-content !important; min-width: 100% !important; }
          `;
          document.head.appendChild(style);

          const td = Array.from(document.querySelectorAll('td'))
            .find(el => (el.textContent || '').includes(ywh));
          if (!td) return 0;
          const table = td.closest('table');
          let el = table;
          while (el && el !== document.body) {
            el.style.setProperty('overflow', 'visible', 'important');
            el.style.setProperty('max-width', 'none', 'important');
            el = el.parentElement;
          }
          const rect = table.getBoundingClientRect();
          return Math.ceil(Math.max(rect.right,
                                    document.documentElement.scrollWidth) + 40);
        }""", ywh)
    target_w = max(1720, min(int(needed or 0), 3600))
    if target_w > 1720:
        print(f"  适配表格宽度：视口加宽至 {target_w}px")
        page.set_viewport_size({"width": target_w, "height": 1080})
        page.wait_for_timeout(800)  # 等重新布局稳定


def run_query(ywh: str, zsbm: str, url: str, out_dir: Path, headed: bool) -> dict:
    """驱动浏览器完成房源信息验证并截图。返回输出文件路径 dict。

    站点为 Ant Design Vue SPA：先点击左侧菜单「房源信息验证」进入对应表单，
    再填入业务件号/证件编码查询。使用 Playwright 内置 Chromium
    （本机 Edge/Chrome 通道的 browser.close() 存在挂起问题）。
    """
    from playwright.sync_api import sync_playwright

    def debug_dump(page, name: str) -> None:
        try:
            page.screenshot(path=str(out_dir / f"{name}.png"), full_page=True)
            (out_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    outputs: dict[str, str] = {}
    with sync_playwright() as p:
        launch_options = {"headless": not headed}
        executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
        if executable_path:
            launch_options["executable_path"] = executable_path
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1720, "height": 1080},
                                device_scale_factor=2)
        print(f"  打开验证页面：{url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 1) 点击左侧菜单「房源信息验证」（Ant Design menuitem）
        menu_item = _first_visible(page, [
            'li.ant-menu-item:has-text("房源信息验证")',
            'xpath=//li[@role="menuitem" and contains(.,"房源信息验证")]',
            'xpath=//div[contains(@class,"item") and contains(.,"房源信息验证")]',
        ], timeout=20000)
        if not menu_item:
            debug_dump(page, "debug_menu_not_found")
            browser.close()
            raise SystemExit("未找到「房源信息验证」菜单（已保存 debug_menu_not_found 快照）")
        menu_item.click()

        # 2) 等待该功能的表单渲染（label title 属性与字段同名）
        yw_input = _first_visible(page, [
            '.ant-form-item:has(label[title="业务件号"]) input',
            'xpath=//label[@title="业务件号"]/ancestor::div[contains(@class,"ant-form-item")]//input',
        ], timeout=15000)
        zs_input = _first_visible(page, [
            '.ant-form-item:has(label[title="证件编码"]) input',
            'xpath=//label[@title="证件编码"]/ancestor::div[contains(@class,"ant-form-item")]//input',
        ], timeout=5000)
        if not yw_input or not zs_input:
            debug_dump(page, "debug_form_not_found")
            browser.close()
            raise SystemExit("未能定位查询表单（已保存 debug_form_not_found 快照）")

        print(f"  填入 业务件号={ywh} 证件编码={zsbm}")
        yw_input.click()
        yw_input.fill(ywh)
        zs_input.click()
        zs_input.fill(zsbm)

        query_btn = _first_visible(page, [
            'button.btn.primary:has-text("查")',
            'xpath=//button[contains(@class,"primary") and contains(.,"查")]',
            'xpath=//button[@type="submit" and contains(.,"查")]',
        ], timeout=5000)
        if not query_btn:
            debug_dump(page, "debug_button_not_found")
            browser.close()
            raise SystemExit("未能定位「查询」按钮（已保存 debug_button_not_found 快照）")
        query_btn.click()

        print("  等待查询结果…")
        try:
            # 结果表格出现且包含业务件号，才算数据真正返回
            page.locator(f'td:has-text("{ywh}")').first.wait_for(
                state="visible", timeout=30000)
        except Exception:
            debug_dump(page, "debug_no_result")
            browser.close()
            raise SystemExit("查询结果未返回或不包含该业务件号（已保存 debug_no_result 快照）")
        page.wait_for_timeout(1200)  # 等表格渲染稳定

        # 解析结果表格：表头与数据行一一对应，写入 result.json（结构化结论）
        try:
            table = page.locator(
                f'xpath=//td[contains(.,"{ywh}")]/ancestor::table[1]').first
            headers = [t.strip() for t in
                       table.locator("thead th").all_inner_texts()]
            if not headers:
                headers = [t.strip() for t in
                           table.locator("tr").first.locator("th").all_inner_texts()]
            cells = [t.strip() for t in page.locator(
                f'xpath=//td[contains(.,"{ywh}")]/ancestor::tr[1]'
            ).first.locator("td").all_inner_texts()]
            parsed = _build_result(headers, cells)
            result_path = out_dir / "result.json"
            result_path.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            outputs["result"] = str(result_path)
            if parsed["fields"]:
                print(f"  查询结果已解析：{len(headers)} 列，"
                      f"是否查封={parsed['fields'].get('是否查封')} "
                      f"是否抵押={parsed['fields'].get('是否抵押')}")
            else:
                print(f"  查询结果已解析（列数不匹配，未生成字段映射）："
                      f"{len(headers)} 表头 / {len(cells)} 单元格")
        except Exception as exc:  # noqa: BLE001 - 解析失败不影响截图产物
            print(f"  结果表格解析失败（仅保留截图）：{exc}")

        # 适配表头宽度，避免截图中表头/单元格换行
        adapt_table_width(page, ywh)

        full_png = out_dir / "result_full.png"
        page.screenshot(path=str(full_png), full_page=True)
        outputs["full"] = str(full_png)

        # 3) 用「标题 + 结果表格」包围盒裁剪出结果区域截图
        try:
            title_loc = page.locator(
                'xpath=//*[contains(@class,"top-title") and contains(.,"房源信息验证")]'
            ).first
            table_loc = page.locator(
                f'xpath=//td[contains(.,"{ywh}")]/ancestor::table[1]'
            ).first
            b1 = title_loc.bounding_box(timeout=5000)
            b2 = table_loc.bounding_box(timeout=5000)
            if b1 and b2:
                x = min(b1["x"], b2["x"]) - 20
                y = max(b1["y"] - 70, 0)
                right = max(b1["x"] + b1["width"], b2["x"] + b2["width"]) + 20
                bottom = b2["y"] + b2["height"] + 30
                panel_png = out_dir / "result_panel.png"
                page.screenshot(path=str(panel_png), clip={
                    "x": max(x, 0), "y": y,
                    "width": right - max(x, 0), "height": bottom - y,
                })
                outputs["panel"] = str(panel_png)
        except Exception:  # noqa: BLE001 - 区域裁剪失败则仅用整页截图
            pass

        (out_dir / "page_snapshot.html").write_text(page.content(), encoding="utf-8")
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
    return outputs


def main() -> int:
    ap = argparse.ArgumentParser(description="房源信息验证：证件提取（主备模型）+ 实时查询 + 结果截图")
    ap.add_argument("cert_image", type=Path, help="产权证照片路径")
    ap.add_argument("--model", default="nvidia/nemotron-nano-12b-v2-vl:free",
                    help="主识别模型（OpenRouter）")
    ap.add_argument("--model-fallback", default="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                    help="兜底模型（NVIDIA build.nvidia.com；主模型重试后仍失败时启用）")
    ap.add_argument("--model-fallback2", default="stepfun-ai/step-3.7-flash",
                    help="第二兜底模型（NVIDIA build.nvidia.com；传空字符串可关闭）")
    ap.add_argument("--api-key", default=None, help="OpenRouter API Key（默认读环境变量/.env）")
    ap.add_argument("--nvidia-key", default=None, help="NVIDIA API Key（默认读环境变量/.env）")
    ap.add_argument("--retries", type=int, default=2,
                    help="主模型失败后的重试次数（默认 2）")
    ap.add_argument("--timeout", type=float, default=30,
                    help="单次模型调用超时（秒，默认 30）")
    ap.add_argument("--url", default=DEFAULT_URL, help="房源信息验证页面地址")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    args = ap.parse_args()

    if not args.cert_image.exists():
        sys.exit(f"输入文件不存在：{args.cert_image}")

    nvidia_key = load_nvidia_api_key(args.nvidia_key)
    openrouter_key = load_api_key(args.api_key)
    tasks: list[dict] = []
    if openrouter_key:
        tasks.append({"label": "主识别服务(OpenRouter)", "api_key": openrouter_key,
                      "model": args.model, "channel": "openrouter",
                      "prompt": EXTRACT_PROMPT})
    if nvidia_key:
        tasks.append({"label": "备用识别服务(NVIDIA)", "api_key": nvidia_key,
                      "model": args.model_fallback, "channel": "nvidia",
                      "prompt": OMNI_PROMPT})
        if args.model_fallback2.strip():
            tasks.append({"label": "备用识别服务2(StepFun)", "api_key": nvidia_key,
                          "model": args.model_fallback2.strip(), "channel": "nvidia",
                          "prompt": EXTRACT_PROMPT})
    if not tasks:
        sys.exit("未找到任何模型 API Key：请设置 NVIDIA_API_KEY 或 OPENROUTER_API_KEY。")

    out_dir = args.out or (ROOT / "outputs" / f"house_verify_{datetime.now():%Y%m%d_%H%M%S}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] 提取证件字段：{args.cert_image.name}")
    result = extract_credentials_chained(tasks, args.cert_image,
                                         retries=args.retries, timeout=args.timeout)
    cred = result["cred"]
    print(f"      业务件号={cred['业务件号']}　证件编码={cred['证件编码']}")
    (out_dir / "extracted.json").write_text(
        json.dumps(cred, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[2/3] 发起房源信息验证")
    outputs = run_query(cred["业务件号"], cred["证件编码"], args.url, out_dir, args.headed)

    print("[3/3] 查询验证图片已生成")
    for k, v in outputs.items():
        print(f"      {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
