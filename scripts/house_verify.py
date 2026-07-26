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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_archive import ROOT, call_vl_model, load_api_key  # noqa: E402

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
    '  "业务件号": "附记中的业务号（形如 2025112701F90697）",\n'
    '  "证件编码": "产权证号（形如 川（2025）成都市不动产权第0373259号）"\n'
    "}\n"
    "无法辨认的字段填 null。"
)


def clean_field(value: str) -> str:
    """清洗提取字段：去除所有空格（含中间空格），英文括号统一为中文括号。"""
    if not value:
        return ""
    return re.sub(r"\s+", "", value).replace("(", "（").replace(")", "）")


def _try_extract(api_key: str, model: str, cert_image: Path,
                 before_model_request: object = None) -> dict:
    """单次调用 VL 模型提取字段，返回清洗后的 dict。任一字段为空则抛 ValueError。"""
    data = call_vl_model(
        api_key, model, cert_image, EXTRACT_PROMPT, enhance_text=True,
        before_request=before_model_request)
    ywh = clean_field(data.get("业务件号") or "")
    zsbm = clean_field(data.get("证件编码") or "")
    if not ywh or not zsbm:
        raise ValueError(
            f"业务件号={ywh!r} 证件编码={zsbm!r}，请更换清晰照片"
        )
    return {"业务件号": ywh, "证件编码": zsbm}


def extract_credentials(
    api_key: str, model: str, cert_image: Path,
    fallback_model: str | None = None,
    on_fallback: object = None,
    before_model_request: object = None,
) -> dict:
    """从产权证图片提取业务件号与证件编码。

    先用主模型；任一字段为空时自动切换到 fallback_model 重试一次；
    两次都失败才抛 SystemExit。

    如果提供了 on_fallback 回调，切换兜底模型时会调用它（参数为
    primary_model, fallback_model, error_message），便于调用方上报进度。
    """
    try:
        return _try_extract(api_key, model, cert_image, before_model_request)
    except ValueError as primary_err:
        if not fallback_model or fallback_model == model:
            raise SystemExit(f"字段提取失败：{primary_err}") from primary_err
        if on_fallback:
            try:
                on_fallback(model, fallback_model, str(primary_err))
            except Exception:  # noqa: BLE001 - 回调异常不影响主流程
                pass
        print(
            f"  ⚠ 主模型提取失败（{model}：{primary_err}），"
            f"切换到兜底模型 {fallback_model}"
        )
        try:
            return _try_extract(api_key, fallback_model, cert_image,
                                before_model_request)
        except ValueError as fb_err:
            raise SystemExit(
                f"字段提取失败：主模型与兜底模型均失败（{fb_err}）"
            ) from fb_err


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
    ap = argparse.ArgumentParser(description="房源信息验证：证件提取 + 实时查询 + 结果截图")
    ap.add_argument("cert_image", type=Path, help="产权证照片路径")
    ap.add_argument("--model", default="nvidia/nemotron-nano-12b-v2-vl:free")
    ap.add_argument("--model-fallback",
                    default="google/gemma-4-26b-a4b-it:free",
                    help="主模型提取失败时的兜底模型（默认 google/gemma-4-26b-a4b-it:free；"
                         "传空字符串可关闭）")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--url", default=DEFAULT_URL, help="房源信息验证页面地址")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    args = ap.parse_args()

    if not args.cert_image.exists():
        sys.exit(f"输入文件不存在：{args.cert_image}")

    api_key = load_api_key(args.api_key)
    out_dir = args.out or (ROOT / "outputs" / f"house_verify_{datetime.now():%Y%m%d_%H%M%S}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] 提取证件字段：{args.cert_image.name}")
    fallback = args.model_fallback.strip() or None
    cred = extract_credentials(
        api_key, args.model, args.cert_image, fallback_model=fallback)
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
