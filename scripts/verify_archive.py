"""查档核验工具：VL 模型解析产权证与查档结果截图，字段比对并输出差异截图。

用法：
    1. 在仓库根目录 .env 中写入 OPENROUTER_API_KEY=<你的 Key>（.env 已被 .gitignore 忽略），
       或设置同名环境变量；
    2. python scripts/verify_archive.py <产权证图片> <查档结果截图>

输出（默认 outputs/verify_<时间戳>/，已加入 .gitignore，禁止提交入库）：
    - cert_parsed.json   产权证解析结果
    - site_parsed.json   查档截图解析结果
    - comparison.json    字段比对结果
    - diff_report.png    差异截图（比对报告图）

注意：输入图片含个人敏感信息，仅用于本地核验，请勿外传或提交版本库。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

DEFAULT_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

ROOT = Path(__file__).resolve().parent.parent

CERT_PROMPT = (
    "请逐字识读这张不动产权证照片，以严格 JSON 输出以下字段（不要输出任何解释文字、不要 markdown 代码块）：\n"
    "{\n"
    '  "产权证号": "如 川（2025）成都市不动产权第0373259号",\n'
    '  "权利人": "...",\n'
    '  "坐落": "逐字抄录完整坐落字符串，不得改写、不得增删字",\n'
    '  "不动产单元号": "...",\n'
    '  "权利类型": "...",\n'
    '  "权利性质": "...",\n'
    '  "用途": "...",\n'
    '  "面积": {"共用宗地面积_平方米": "数字或null", "房屋建筑面积_平方米": "数字或null"},\n'
    '  "权利其他状况": {"房屋结构": "...", "房屋总层数": "...", "所在层数": "..."},\n'
    '  "附记": {"业务号": "...", "其他": "附记中除业务号外的完整文字"}\n'
    "}\n"
    "要求：忽略红色水印文字；无法辨认的字段填 null；数字字段只填数字字符串（不含单位）。"
)

SITE_PROMPT = (
    "请解析这张“房源信息验证”网站查询结果截图。查询结果是一张表格，只有一行数据。\n"
    "以严格 JSON 输出（不要输出任何解释文字、不要 markdown 代码块）：\n"
    "{\n"
    '  "表单": {"业务件号": "...", "证件编码": "..."},\n'
    '  "表头": ["逐列抄录表格的全部表头文字，按从左到右顺序"],\n'
    '  "数据行": ["与表头一一对应的数据单元格文字，按从左到右顺序；空单元格填空字符串"]\n'
    "}\n"
    "关键要求：数据行的每个值必须与其正上方的表头对齐，列数必须与表头一致，"
    "即使某列为空也要保留位置，严禁错位、跳列。"
)


# ---------------------------------------------------------------- 工具函数
def load_api_key(cli_key: str | None) -> str:
    """从命令行参数、环境变量或仓库根目录 .env 读取 OpenRouter Key。"""
    if cli_key:
        return cli_key.strip()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("未找到 OPENROUTER_API_KEY：请通过 --api-key、环境变量或仓库根目录 .env 提供。")


def _enhance_text_image(img: Image.Image, max_side: int) -> Image.Image:
    """生成适合证件文字识别的图像，处理全程仅在内存中进行。

    手机上的照片常见曝光发灰、轻微模糊和 EXIF 方向未被模型读取的问题。这里用
    灰度自动对比度、轻度去噪及反锐化掩模增强笔画边缘；不做二值化，以免抹掉浅色
    印刷文字或印章。放大上限略高于普通图片，避免证件号码在下采样时丢失细节。
    """
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    gray = ImageEnhance.Contrast(gray).enhance(1.35)
    gray = gray.filter(ImageFilter.UnsharpMask(radius=1.4, percent=170, threshold=2))
    return gray.convert("RGB")


def image_to_data_urls(path: Path, max_side: int = 1800) -> list[str]:
    """将证件转为增强后的 data URL；纵向双页照片额外提供两个转正方向。

    有些用户把展开的双页证件横着拍摄后直接上传。对于明显纵向的图片，同时传递
    顺/逆时针方向的增强副本，让视觉模型选择文字正向的一张，而不是猜测旋转方向。
    返回的数据均由原图在内存中生成，不会额外写入文件。
    """
    with Image.open(path) as source:
        base = ImageOps.exif_transpose(source).convert("RGB")

    variants = [base]
    if base.height >= base.width * 1.20:
        variants.extend([
            base.transpose(Image.Transpose.ROTATE_90),
            base.transpose(Image.Transpose.ROTATE_270),
        ])

    urls: list[str] = []
    for variant in variants:
        enhanced = _enhance_text_image(variant, max_side)
        buf = io.BytesIO()
        enhanced.save(buf, format="JPEG", quality=93, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        urls.append(f"data:image/jpeg;base64,{b64}")
    return urls


def image_to_data_url(path: Path, max_side: int = 1600) -> str:
    """兼容旧调用：返回一张经过尺寸约束的原方向图片。"""
    with Image.open(path) as source:
        img = ImageOps.exif_transpose(source).convert("RGB")
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def call_vl_model(api_key: str, model: str, image_path: Path, prompt: str,
                  enhance_text: bool = False) -> dict:
    """调用 OpenRouter VL 模型解析图片，返回解析后的 dict。

    ``enhance_text`` 用于证件类文字识别：请求会带上经对比度/锐化处理后的图片；
    对明显横放的双页证件还会附带两个转正方向，提升号码读取成功率。
    """
    image_urls = (image_to_data_urls(image_path) if enhance_text
                  else [image_to_data_url(image_path)])
    content = [{"type": "text", "text": prompt}]
    if enhance_text and len(image_urls) > 1:
        content[0]["text"] += (
            "\n图片为同一份证件的增强版及两个旋转方向副本；"
            "请只读取文字正向、最清晰的版本，且不要把不同版本的字段混合。"
        )
    content.extend({"type": "image_url", "image_url": {"url": url}}
                   for url in image_urls)
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 2000,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
            resp.raise_for_status()
            body = resp.json()
            if "choices" not in body:
                raise ValueError(f"响应缺少 choices：{str(body)[:200]}")
            content = body["choices"][0]["message"]["content"] or ""
            return extract_json(content)
        except Exception as exc:  # noqa: BLE001 - 重试后统一报错
            last_err = exc
            print(f"  模型调用第 {attempt} 次失败：{exc}")
    raise SystemExit(f"模型调用失败（已重试 3 次）：{last_err}")


def extract_json(text: str) -> dict:
    """从模型输出中提取 JSON（容忍 markdown 代码块与多余文字）。"""
    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"模型输出中未找到 JSON：{text[:200]}")
    return json.loads(text[start : end + 1])


ADDRESS_REREAD_PROMPT = (
    "图片是不动产权证“坐落”一栏的局部放大图。请逐字抄录该栏的完整地址文字"
    "（只输出地址文字本身，不要解释、不要 JSON、忽略红色水印）。无法辨认则输出 null。"
)


def _crop_address_band(image_path: Path) -> str:
    """裁剪证书“坐落”区域并放大，返回 base64 data URL。"""
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    # 展开双页产权证标准布局：坐落行位于左页表格上部（经实拍图标定）
    crop = img.crop((int(w * 0.05), int(h * 0.28), int(w * 0.50), int(h * 0.36)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _vl_text_call(api_key: str, model: str, data_url: str, prompt: str,
                  max_tokens: int = 300) -> str | None:
    """单次 VL 文本调用，返回模型文本输出（失败返回 None）。"""
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
        body = resp.json()
        if "choices" not in body:
            raise ValueError(f"响应缺少 choices：{str(body)[:200]}")
        return (body["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"  VL 调用失败：{exc}")
        return None


def reread_address(api_key: str, model: str, image_path: Path) -> str | None:
    """对证书“坐落”区域裁剪放大后单独复读，提升小字识别准确率。"""
    text = _vl_text_call(api_key, model, _crop_address_band(image_path),
                         ADDRESS_REREAD_PROMPT, max_tokens=200)
    if not text:
        return None
    text = text.strip('"').strip()
    return None if text.lower() in ("", "null") else text


def verify_address_candidate(api_key: str, model: str, image_path: Path,
                             candidate: str) -> tuple[bool, str]:
    """核验模式：用查档结果拼接候选地址，请模型对照证书确认（是/否判断）。

    水印遮挡严重时，模型开放识读容易丢字，但判断题准确率高得多。
    返回 (是否确认一致, 模型实际识读文字)。
    """
    prompt = (
        "图片是不动产权证“坐落”栏的放大图，上面叠加了红色水印。"
        f"请核对：该地址是否为“{candidate}”？"
        '只输出 JSON：{"是否一致": "是/否/无法确定", "实际识读": "你逐字看到的地址"}'
    )
    text = _vl_text_call(api_key, model, _crop_address_band(image_path), prompt)
    if not text:
        return False, ""
    try:
        data = extract_json(text)
        verdict = str(data.get("是否一致", ""))
        return verdict.startswith("是"), str(data.get("实际识读", ""))
    except Exception as exc:  # noqa: BLE001
        print(f"  候选地址核验结果解析失败：{exc}（原文：{text[:120]}）")
        return False, ""


def norm(value) -> str:
    """归一化用于比对：去空白、全角转半角、统一括号、转小写。"""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", "", s).replace("（", "(").replace("）", ")").lower()


def norm_num_part(value) -> str:
    """归一化门牌/栋/单元/楼层/房间类字段：仅保留数字与字母，去掉号/栋/单元/楼/层/室等单位。"""
    s = norm(value)
    return re.sub(r"(单元|号|栋|幢|座|楼|层|室)$", "", s)


ADDR_PATTERN = re.compile(
    r"^(?P<区域>.+?(?:区|县|市))(?P<街道>.+?(?:街道|大街|街|路|道|巷|镇|乡))"
    r"(?P<门牌号>\d+)号(?:(?P<附号>.+?)号)?"
    r"(?P<栋号>\d+)(?:栋|幢)(?P<单元号>\d+)单元(?P<楼层号>\d+)楼(?P<房间号>\d+)号$"
)


def parse_address(raw: str | None) -> dict:
    """用正则将证书“坐落”拆分为区域/街道/门牌号/栋号/单元号/楼层号/房间号。

    拆分逻辑由代码完成（而非依赖模型），避免模型自由发挥导致错拆。
    """
    if not raw:
        return {}
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(raw)))
    m = ADDR_PATTERN.match(s)
    return m.groupdict() if m else {}


def site_table_to_dict(site: dict) -> dict:
    """将模型输出的 表头/数据行 数组 zip 成字典，并校验列数对齐。"""
    headers = site.get("表头") or []
    values = site.get("数据行") or []
    if not headers or len(headers) != len(values):
        print(f"  警告：表头({len(headers)})与数据行({len(values)})列数不一致，表格解析可能错位")
    merged = dict(zip(headers, values))
    # 兼容模型仍按旧格式输出“查询结果”字典的情况
    legacy = site.get("查询结果") or {}
    for k, v in legacy.items():
        merged.setdefault(k, v)
    return merged


def to_float(value) -> float | None:
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(m.group()) if m else None


def get(data: dict, *keys):
    """按嵌套键取值，缺失返回 None。"""
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ---------------------------------------------------------------- 字段比对
def compare(cert: dict, site: dict) -> list[dict]:
    """逐项比对产权证与查档结果，返回比对行列表。

    status: match 一致 / mismatch 差异 / note 提示 / cert_only 仅证书侧 / site_only 仅网站侧
    """
    rows: list[dict] = []

    def add(item: str, cert_val, site_val, status: str, remark: str = ""):
        rows.append(
            {
                "item": item,
                "cert": "未识别" if cert_val in (None, "") else str(cert_val),
                "site": "无此项" if site_val in (None, "") else str(site_val),
                "status": status,
                "remark": remark,
            }
        )

    def cmp_field(item: str, cert_val, site_val, normalizer=norm):
        c, s = normalizer(cert_val), normalizer(site_val)
        if not c and not s:
            add(item, cert_val, site_val, "note", "两侧均未识别，需人工核对")
        elif not c:
            add(item, cert_val, site_val, "note", "证书侧未识别，需人工核对")
        elif not s:
            add(item, cert_val, site_val, "note", "网站侧未识别，需人工核对")
        elif c == s:
            add(item, cert_val, site_val, "match")
        else:
            add(item, cert_val, site_val, "mismatch", "两侧内容不一致")

    result = site_table_to_dict(site)
    addr = parse_address(cert.get("坐落"))

    cmp_field("业务件号", get(cert, "附记", "业务号"), result.get("业务件号"))
    cmp_field("产权证号", cert.get("产权证号"), result.get("产权证号"))
    for item in ["区域", "街道", "门牌号", "栋号", "单元号", "楼层号", "房间号"]:
        cmp_field(item, addr.get(item), result.get(item),
                  norm if item in ("区域", "街道") else norm_num_part)

    # 用途：证书通常为“城镇住宅用地/住宅”，网站为“住宅”——包含即一致
    cert_use, site_use = cert.get("用途"), result.get("房屋用途")
    if norm(site_use) and norm(site_use) in norm(cert_use):
        add("房屋用途", cert_use, site_use, "match")
    else:
        cmp_field("房屋用途", cert_use, site_use)

    # 面积：证书“房屋建筑面积” vs 网站“套内面积 + 公摊面积”
    cert_area = to_float(get(cert, "面积", "房屋建筑面积_平方米"))
    tn, gt = to_float(result.get("套内面积")), to_float(result.get("公摊面积"))
    memo = get(cert, "附记", "其他") or ""
    site_area_text = f"套内{result.get('套内面积')}㎡ / 公摊{result.get('公摊面积')}㎡"
    if cert_area is None or tn is None:
        add("房屋建筑面积", get(cert, "面积", "房屋建筑面积_平方米"), site_area_text,
            "note", "面积未完整识别，需人工核对")
    elif abs(cert_area - (tn + (gt or 0))) < 0.01:
        if "楼梯间" in str(memo) and (gt or 0) == 0:
            add("房屋建筑面积", f"{cert_area}㎡", site_area_text, "note",
                f"数值一致但口径不同：证书附记含“{memo}”，网站公摊为 0，建议人工确认面积口径")
        else:
            add("房屋建筑面积", f"{cert_area}㎡", site_area_text, "match",
                "证书建筑面积 = 网站套内 + 公摊")
    else:
        add("房屋建筑面积", f"{cert_area}㎡", site_area_text, "mismatch",
            f"差值 {cert_area - (tn + (gt or 0)):+.2f}㎡")

    # 仅网站侧字段
    add("是否抵押", None, result.get("是否抵押"), "site_only", "证书不体现，以查档结果为准")
    add("是否查封", None, result.get("是否查封"), "site_only", "证书不体现，以查档结果为准")

    # 仅证书侧字段
    add("不动产单元号", cert.get("不动产单元号"), None, "cert_only", "网站结果无此字段")
    add("权利类型", cert.get("权利类型"), None, "cert_only", "网站结果无此字段")
    add("权利性质", cert.get("权利性质"), None, "cert_only", "网站结果无此字段")
    add("房屋结构", get(cert, "权利其他状况", "房屋结构"), None, "cert_only", "网站结果无此字段")
    add("所在层数/总层数",
        f"{get(cert, '权利其他状况', '所在层数')} / {get(cert, '权利其他状况', '房屋总层数')}",
        None, "cert_only", "网站结果无此字段")
    if memo:
        add("附记", memo, None, "cert_only", "网站结果无此字段")

    if cert.get("_地址核验模式"):
        add("地址核验方式", "核验模式", None, "note",
            "证书坐落受红色水印遮挡，开放识读失败；已用查档结果拼接候选地址，"
            "经模型对照证书确认一致，仍建议人工复核")

    return rows


# ---------------------------------------------------------------- 差异截图渲染
STATUS_STYLE = {
    "match": ("一致", "#e8f5e9", "#2e7d32"),
    "mismatch": ("差异", "#ffebee", "#c62828"),
    "note": ("提示", "#fff8e1", "#f57f17"),
    "cert_only": ("仅证书侧", "#f5f5f5", "#616161"),
    "site_only": ("仅网站侧", "#f5f5f5", "#616161"),
}

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """按像素宽度对文本换行。"""
    lines, cur = [], ""
    for ch in str(text):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=font) <= max_width:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    lines.append(cur)
    return lines


def render_report(rows: list[dict], cert_img: Path, site_img: Path, out_path: Path,
                  model: str) -> None:
    """渲染差异截图：顶部结论汇总 + 字段比对表 + 底部两图对照。"""
    W, MARGIN = 1720, 40
    f_title = load_font(44)
    f_head = load_font(26)
    f_cell = load_font(24)
    f_small = load_font(20)

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_STYLE}
    summary = (f"比对完成：一致 {counts['match']} 项　差异 {counts['mismatch']} 项　"
               f"提示 {counts['note']} 项　仅单侧信息 "
               f"{counts['cert_only'] + counts['site_only']} 项")

    # 表格列宽：比对项 | 产权证(图1) | 查档结果(图2) | 结论 | 备注
    col_w = [210, 480, 480, 130, 360]
    headers = ["比对项", "产权证（图1）", "查档结果（图2）", "结论", "备注"]

    probe = Image.new("RGB", (10, 10))
    pd = ImageDraw.Draw(probe)
    line_h = 32

    wrapped_rows = []
    for r in rows:
        cells = [r["item"], r["cert"], r["site"], STATUS_STYLE[r["status"]][0], r["remark"]]
        wrapped = [wrap_text(pd, c, f_cell, w - 24) for c, w in zip(cells, col_w)]
        wrapped_rows.append((r, wrapped, max(len(x) for x in wrapped)))

    table_h = 56 + sum(max(n * line_h + 20, 56) for _, _, n in wrapped_rows) + 10

    # 底部图片区
    thumbs = []
    for p in (cert_img, site_img):
        im = Image.open(p).convert("RGB")
        im.thumbnail((810, 520), Image.LANCZOS)
        thumbs.append(im)
    img_area_h = max(t.height for t in thumbs) + 70

    H = MARGIN + 150 + table_h + 40 + img_area_h + 90
    canvas = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(canvas)

    # 标题栏
    d.rectangle([0, 0, W, 110], fill="#0d3050")
    d.text((MARGIN, 30), "查档核验差异比对报告", font=f_title, fill="#ffffff")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d.text((W - MARGIN - d.textlength(f"模型：{model}", font=f_small), 24),
           f"模型：{model}", font=f_small, fill="#9fc3e0")
    d.text((W - MARGIN - d.textlength(f"时间：{ts}", font=f_small), 58),
           f"时间：{ts}", font=f_small, fill="#9fc3e0")

    # 汇总条
    y = 130
    overall = "#c62828" if counts["mismatch"] else ("#f57f17" if counts["note"] else "#2e7d32")
    d.text((MARGIN, y), summary, font=f_head, fill=overall)
    y += 50

    # 表头
    x = MARGIN
    d.rectangle([MARGIN, y, MARGIN + sum(col_w), y + 46], fill="#0d3050")
    for h, w in zip(headers, col_w):
        d.text((x + 12, y + 8), h, font=f_head, fill="#ffffff")
        x += w
    y += 46

    # 数据行
    for r, wrapped, nlines in wrapped_rows:
        label, bg, fg = STATUS_STYLE[r["status"]]
        row_h = max(nlines * line_h + 20, 56)
        d.rectangle([MARGIN, y, MARGIN + sum(col_w), y + row_h], fill=bg)
        x = MARGIN
        cells = [r["item"], r["cert"], r["site"], label, r["remark"]]
        for i, (lines, w) in enumerate(zip(wrapped, col_w)):
            color = fg if i in (3, 4) else "#212121"
            if i == 0:
                color = "#0d3050"
            ty = y + 10
            for ln in lines:
                d.text((x + 12, ty), ln, font=f_cell, fill=color)
                ty += line_h
            x += w
        # 行边框
        d.rectangle([MARGIN, y, MARGIN + sum(col_w), y + row_h], outline="#b0bec5")
        y += row_h
    # 竖线
    x = MARGIN
    for w in col_w[:-1]:
        x += w
        d.line([x, 130 + 50, x, y], fill="#b0bec5", width=1)

    # 底部双图对照
    y += 40
    d.text((MARGIN, y), "原始图片对照", font=f_head, fill="#0d3050")
    y += 44
    labels = ["图1：产权证", "图2：查档结果"]
    x = MARGIN
    for im, lb in zip(thumbs, labels):
        d.text((x, y), lb, font=f_cell, fill="#37474f")
        canvas.paste(im, (x, y + 34))
        d.rectangle([x, y + 34, x + im.width, y + 34 + im.height], outline="#90a4ae")
        x += 850

    # 页脚
    d.text((MARGIN, H - 50),
           "说明：本报告由 VL 模型自动解析生成，仅供辅助参考，关键字段需人工复核；图片含个人敏感信息，请勿外传。",
           font=f_small, fill="#78909c")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="查档核验：产权证 vs 查档结果差异比对")
    ap.add_argument("cert_image", type=Path, help="产权证照片路径（图1）")
    ap.add_argument("site_image", type=Path, help="查档结果截图路径（图2）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter 模型 ID")
    ap.add_argument("--api-key", default=None, help="OpenRouter API Key（默认读环境变量/.env）")
    ap.add_argument("--out", type=Path, default=None, help="输出目录（默认 outputs/verify_<时间戳>）")
    args = ap.parse_args()

    for p in (args.cert_image, args.site_image):
        if not p.exists():
            sys.exit(f"输入文件不存在：{p}")

    api_key = load_api_key(args.api_key)
    out_dir = args.out or (ROOT / "outputs" / f"verify_{datetime.now():%Y%m%d_%H%M%S}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] 解析产权证：{args.cert_image.name}")
    cert = call_vl_model(api_key, args.model, args.cert_image, CERT_PROMPT)
    (out_dir / "cert_parsed.json").write_text(
        json.dumps(cert, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[2/3] 解析查档结果：{args.site_image.name}")
    site = call_vl_model(api_key, args.model, args.site_image, SITE_PROMPT)
    (out_dir / "site_parsed.json").write_text(
        json.dumps(site, ensure_ascii=False, indent=2), encoding="utf-8")

    # 地址修复链：整体识读 → 区域放大复读 → 候选地址核验
    if not parse_address(cert.get("坐落")):
        print("  坐落整体识读异常，对坐落区域裁剪放大后复读…")
        reread = reread_address(api_key, args.model, args.cert_image)
        if reread and parse_address(reread):
            print(f"  复读结果：{reread}")
            cert["坐落"] = reread
    if not parse_address(cert.get("坐落")):
        result = site_table_to_dict(site)
        candidate = (
            f"{result.get('区域', '')}{result.get('街道', '')}{result.get('门牌号', '')}号"
            f"{result.get('栋号', '')}栋{result.get('单元号', '')}单元"
            f"{result.get('楼层号', '')}楼{result.get('房间号', '')}号"
        )
        print(f"  启用核验模式，候选地址：{candidate}")
        ok, seen = verify_address_candidate(api_key, args.model, args.cert_image, candidate)
        print(f"  模型核验：{'一致' if ok else '未确认'}；实际识读：{seen}")
        if ok and parse_address(candidate):
            cert["坐落"] = candidate
            cert["_地址核验模式"] = True
        else:
            print("  地址类字段将标记为需人工核对")

    print("[3/3] 字段比对并生成差异截图")
    rows = compare(cert, site)
    (out_dir / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    report = out_dir / "diff_report.png"
    render_report(rows, args.cert_image, args.site_image, report, args.model)

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_STYLE}
    print(f"一致 {counts['match']} / 差异 {counts['mismatch']} / 提示 {counts['note']} "
          f"/ 仅单侧 {counts['cert_only'] + counts['site_only']}")
    print(f"差异截图：{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
