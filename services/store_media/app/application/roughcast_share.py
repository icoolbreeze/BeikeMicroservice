"""清水房排名榜 / 核分页「分享」海报：服务侧拼图 (Pillow)。

不进榜 / 不打 CRM cookies / 不读 owner_phone。一个长图,顺序:

1. 标题 + 整数分(nearby 时只显 tier 不显整数)
2. 小区 + 区 · 商圈
3. 户型 / 面积 / 月租 / 朝向 / 楼层
4. 单价 / 装修参考 / 合理清水 / 可信度 / benchmark_mode
5. 原因(全文,自动换行)
6. 全市静态地图(成都 BD-09 中心 + 红点);缺坐标 / 缺 AK → 占位块
7. 实勘图(REAL,2 列,上限 16);空 → "暂无实勘图"
8. 页脚:listing_id
"""

from __future__ import annotations

import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageColor, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

POSTER_WIDTH = 1080
POSTER_BACKGROUND = "#f5f4f0"
POSTER_INK = "#202825"
POSTER_MUTED = "#717a74"
POSTER_LINE = "#e3e4dd"
POSTER_GREEN = "#1f6755"
POSTER_SAND = "#d9a454"
POSTER_DANGER = "#b34b40"
MAP_PANEL_HEIGHT = 420
MAP_PANEL_CAPTION_HEIGHT = 44
PHOTO_GRID_GAP = 12
PHOTO_GRID_COLUMNS = 2
PHOTO_MAX_COUNT = 16
PHOTO_CELL_HEIGHT = 320
SECTION_PADDING = 28
LISTING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,31}$")
PROSPECT_PHOTO_TIMEOUT = 4.0
# 单次 share 调用允许花在等实勘图上的总时长上限。超过此值的 URL 视为
# 失败(图位留空,继续拼其他要素),保证浏览器"正在生成"不卡太久。
PROSPECT_DOWNLOAD_DEADLINE_SECONDS = 6.0

# 分享图对外文案清洗:reason 里把 "× δ X.XXX = 合理清水价 Y 元/㎡" 这段显式
# 公式、以及 ";MODE / community;R=NNN" 这串算法元数据都剥掉。保留 "装修参考
# 单价 X / 实际 Y" 这种用户能直接读懂的事实对。
_REASON_FORMULA_RE = re.compile(
    r"\s*[×x]\s*[δd]\s*\d+(?:\.\d+)?\s*=\s*合理清水价\s*\d+(?:\.\d+)?\s*元/㎡"
)
_REASON_TAIL_META_RE = re.compile(
    r"\s*[;；]\s*[A-Z][A-Z0-9_]*\s*/\s*community\s*[;；]\s*R=\d+"
)
_REASON_DOUBLE_SEP_RE = re.compile(r"\s*[;；]\s*[;；]\s*")
_REASON_TRAILING_PUNCT_RE = re.compile(r"[\s;；,，。]+$")


@dataclass(frozen=True)
class ShareListingFacts:
    listing_id: str
    community: str
    district: str | None
    bizcircle: str | None
    layout: str
    area_sqm: float | None
    monthly_rent_yuan: float | None
    orientation: str | None
    floor: str | None
    unit_rent: float | None
    reference_unit_rent: float | None
    expected_unit_rent: float | None
    confidence_score: int | None
    benchmark_mode: str | None
    quality_status: str
    quality_tier: str | None
    quality_score: int | None
    reason: str
    city_lat: float | None
    city_lng: float | None


@dataclass(frozen=True)
class ShareProspectPhoto:
    url: str
    label: str


Block = Callable[["PosterContext"], int]


@dataclass
class PosterContext:
    image: Image.Image
    draw: ImageDraw.ImageDraw
    facts: ShareListingFacts
    map_image: Image.Image | None
    map_placeholder: str | None
    photos: Sequence[ShareProspectPhoto]
    photo_loader: Callable[[str], Image.Image | None] | None
    fonts: dict[str, ImageFont.ImageFont]


def is_valid_listing_id(listing_id: str) -> bool:
    return bool(LISTING_ID_RE.fullmatch(listing_id or ""))


def sanitize_share_reason(reason: str) -> str:
    """分享海报对外文案:剥掉显式公式与算法元数据。

    原始 reason 例:`装修参考单价 20.93 元/㎡ × δ 0.531 = 合理清水价 11.12 元/㎡；实际 23.03 元/㎡；S_ONLY / community；R=003`
    清洗后  :`装修参考单价 20.93 元/㎡；实际 23.03 元/㎡`

    - `× δ X.XXX = 合理清水价 Y 元/㎡` 整段删
    - `;MODE / community;R=NNN` 这串算法元数据删
    - 折号、句号、分号收尾不留下
    - 删完若全空,回退原文(原 `insufficient` / `data_error` 等不带公式的短文本)
    """
    if not reason:
        return reason
    text = _REASON_FORMULA_RE.sub("", reason)
    text = _REASON_TAIL_META_RE.sub("", text)
    text = _REASON_DOUBLE_SEP_RE.sub("；", text)
    text = _REASON_TRAILING_PUNCT_RE.sub("", text)
    text = text.strip()
    return text or reason


def format_score_line(facts: ShareListingFacts) -> str:
    if facts.quality_status == "scored" and facts.quality_score is not None:
        return f"{facts.quality_score} 分"
    if facts.quality_tier:
        return facts.quality_tier
    return "—"


def compose_share_poster(
    facts: ShareListingFacts,
    *,
    map_image: Image.Image | None,
    map_placeholder: str | None,
    photos: Sequence[ShareProspectPhoto],
    photo_loader: Callable[[str], Image.Image | None] | None = None,
    font_paths: Sequence[str] = (),
) -> bytes:
    """拼图 → PNG bytes。"""
    fonts = _load_fonts(font_paths)
    blocks: list[tuple[int, Block]] = [
        (SECTION_PADDING, _draw_title_block),
        (16, _draw_community_block),
        (14, _draw_meta_block),
        (14, _draw_price_block),
        (14, _draw_reason_block),
        (24, _draw_map_block),
        (24, _draw_photos_block),
        (24, _draw_footer_block),
    ]

    probe = Image.new("RGB", (POSTER_WIDTH, 1), POSTER_BACKGROUND)
    probe_draw = ImageDraw.Draw(probe)
    probe_ctx = PosterContext(
        image=probe, draw=probe_draw, facts=facts, map_image=map_image,
        map_placeholder=map_placeholder, photos=photos, photo_loader=None,
        fonts=fonts,
    )
    cursor = 0
    heights: list[int] = []
    for top_pad, block in blocks:
        cursor += top_pad
        h = max(int(block(probe_ctx) or 0), 1)
        heights.append(h)
        cursor += h
    total_height = max(cursor + SECTION_PADDING, 1)

    canvas = Image.new("RGB", (POSTER_WIDTH, total_height), POSTER_BACKGROUND)
    cursor = 0
    for (top_pad, block), h in zip(blocks, heights):
        cursor += top_pad
        strip = Image.new("RGB", (POSTER_WIDTH, h), POSTER_BACKGROUND)
        strip_ctx = PosterContext(
            image=strip, draw=ImageDraw.Draw(strip), facts=facts,
            map_image=map_image, map_placeholder=map_placeholder,
            photos=photos, photo_loader=photo_loader, fonts=fonts,
        )
        block(strip_ctx)
        canvas.paste(strip, (0, cursor))
        cursor += h

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


# ---------------------------------------------------------------- draw blocks


def _draw_title_block(ctx: PosterContext) -> int:
    facts = ctx.facts
    title_font = ctx.fonts["title"]
    h2_font = ctx.fonts["h2"]
    line_h = 56
    y = 0
    ctx.draw.text((SECTION_PADDING, y), "清水房优质指数", font=title_font,
                  fill=safe_image_color(POSTER_INK))
    score_text = format_score_line(facts)
    bbox = ctx.draw.textbbox((0, 0), score_text, font=h2_font)
    text_w = bbox[2] - bbox[0]
    color = POSTER_GREEN if facts.quality_status == "scored" else POSTER_SAND
    ctx.draw.text((POSTER_WIDTH - SECTION_PADDING - text_w, y + 8),
                  score_text, font=h2_font, fill=safe_image_color(color))
    return line_h


def _draw_community_block(ctx: PosterContext) -> int:
    facts = ctx.facts
    h2_font = ctx.fonts["h2"]
    body_font = ctx.fonts["body"]
    community = facts.community or "未知小区"
    place_parts = [p for p in (facts.district, facts.bizcircle) if p]
    place = " · ".join(place_parts) if place_parts else "暂无商圈"
    line_h = 38
    ctx.draw.text((SECTION_PADDING, 0), community, font=h2_font,
                  fill=safe_image_color(POSTER_INK))
    bbox = ctx.draw.textbbox((0, 0), place, font=body_font)
    place_w = bbox[2] - bbox[0]
    ctx.draw.text((POSTER_WIDTH - SECTION_PADDING - place_w, 8),
                  place, font=body_font, fill=safe_image_color(POSTER_SAND))
    return line_h


def _draw_meta_block(ctx: PosterContext) -> int:
    facts = ctx.facts
    body_font = ctx.fonts["body"]
    parts: list[str] = []
    if facts.layout:
        parts.append(facts.layout)
    if facts.area_sqm is not None:
        parts.append(f"{facts.area_sqm:.0f}㎡")
    if facts.monthly_rent_yuan is not None:
        parts.append(f"{facts.monthly_rent_yuan:.0f}元/月")
    if facts.orientation:
        parts.append(facts.orientation)
    if facts.floor:
        parts.append(facts.floor)
    line = " · ".join(parts) if parts else "—"
    return _draw_wrapped(ctx, line, body_font, line_height=30, color=POSTER_INK)


def _draw_price_block(ctx: PosterContext) -> int:
    facts = ctx.facts
    body_font = ctx.fonts["body"]
    parts: list[str] = []
    if facts.unit_rent is not None:
        parts.append(f"单价 {facts.unit_rent:.1f} 元/㎡")
    if facts.reference_unit_rent is not None:
        parts.append(f"装修参考 {facts.reference_unit_rent:.1f}")
    if facts.expected_unit_rent is not None:
        parts.append(f"合理清水 {facts.expected_unit_rent:.1f}")
    if facts.quality_status == "scored":
        parts.append(f"可信度 {facts.confidence_score if facts.confidence_score is not None else '—'}")
    elif facts.quality_tier:
        parts.append(f"质量档 {facts.quality_tier}")
    # benchmark_mode (S_ONLY / D_ONLY …) 是内部算法档位,share 对外不展示
    line = " · ".join(parts) if parts else "—"
    return _draw_wrapped(ctx, line, body_font, line_height=30, color=POSTER_INK)


def _draw_reason_block(ctx: PosterContext) -> int:
    facts = ctx.facts
    body_font = ctx.fonts["body"]
    reason = sanitize_share_reason(facts.reason)
    if not reason:
        return 0
    return _draw_wrapped(ctx, reason, body_font, line_height=32, color=POSTER_MUTED)


def _draw_map_block(ctx: PosterContext) -> int:
    h2_font = ctx.fonts["h2"]
    body_font = ctx.fonts["body"]
    caption_h = MAP_PANEL_CAPTION_HEIGHT
    panel_w = POSTER_WIDTH - 2 * SECTION_PADDING
    panel_x = SECTION_PADDING
    caption_text = "成都市相对位置"
    ctx.draw.rectangle((panel_x, 0, panel_x + panel_w, caption_h),
                       fill=safe_image_color(POSTER_GREEN))
    ctx.draw.text((panel_x + 16, 8), caption_text, font=h2_font, fill="#ffffff")

    box_top = caption_h
    if ctx.map_image is not None:
        try:
            img = ctx.map_image.copy()
            img.thumbnail((panel_w, MAP_PANEL_HEIGHT), Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img_x = panel_x + (panel_w - img.width) // 2
            img_y = box_top + (MAP_PANEL_HEIGHT - img.height) // 2
            ctx.image.paste(img, (img_x, img_y))
        except Exception as exc:                                # noqa: BLE001
            logger.warning("share: paste map failed: %s", exc)
        ctx.draw.rectangle((panel_x, 0, panel_x + panel_w, box_top + MAP_PANEL_HEIGHT),
                           outline=safe_image_color(POSTER_LINE), width=1)
        return caption_h + MAP_PANEL_HEIGHT

    ctx.draw.rectangle((panel_x, box_top, panel_x + panel_w, box_top + MAP_PANEL_HEIGHT),
                       outline=safe_image_color(POSTER_LINE), width=1, fill="#eeeae0")
    msg = ctx.map_placeholder or "暂无坐标"
    bbox = ctx.draw.textbbox((0, 0), msg, font=body_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    ctx.draw.text((panel_x + (panel_w - text_w) // 2,
                   box_top + (MAP_PANEL_HEIGHT - text_h) // 2),
                  msg, font=body_font, fill=safe_image_color(POSTER_MUTED))
    return caption_h + MAP_PANEL_HEIGHT


def _draw_photos_block(ctx: PosterContext) -> int:
    h2_font = ctx.fonts["h2"]
    body_font = ctx.fonts["body"]
    small_font = ctx.fonts["small"]
    total = len(ctx.photos)
    title = f"实勘 {total} 张" if total else "实勘"
    ctx.draw.text((SECTION_PADDING, 0), title, font=h2_font,
                  fill=safe_image_color(POSTER_INK))
    title_h = 44
    if not total:
        msg = "暂无实勘图"
        bbox = ctx.draw.textbbox((0, 0), msg, font=body_font)
        ctx.draw.text((SECTION_PADDING, title_h), msg, font=body_font,
                      fill=safe_image_color(POSTER_MUTED))
        return title_h + (bbox[3] - bbox[1]) + 8
    shown = min(total, PHOTO_MAX_COUNT)
    rows = (shown + PHOTO_GRID_COLUMNS - 1) // PHOTO_GRID_COLUMNS
    cell_w = (POSTER_WIDTH - 2 * SECTION_PADDING
              - PHOTO_GRID_GAP * (PHOTO_GRID_COLUMNS - 1)) // PHOTO_GRID_COLUMNS
    cell_h = PHOTO_CELL_HEIGHT
    for index in range(shown):
        row = index // PHOTO_GRID_COLUMNS
        col = index % PHOTO_GRID_COLUMNS
        cell_x = SECTION_PADDING + col * (cell_w + PHOTO_GRID_GAP)
        cell_y = title_h + row * (cell_h + PHOTO_GRID_GAP)
        photo = ctx.photos[index]
        image = None
        if ctx.photo_loader is not None:
            try:
                image = ctx.photo_loader(photo.url)
            except Exception as exc:                                # noqa: BLE001
                logger.warning("share: load photo %s failed: %s", photo.url, exc)
        _draw_photo_cell(ctx, cell_x, cell_y, cell_w, cell_h, image, photo.label, small_font)
    grid_h = rows * (cell_h + PHOTO_GRID_GAP)
    if total > shown:
        note = f"共 {total} 张，展示前 {shown} 张"
        bbox = ctx.draw.textbbox((0, 0), note, font=small_font)
        note_y = title_h + grid_h + 4
        ctx.draw.text((SECTION_PADDING, note_y), note, font=small_font,
                      fill=safe_image_color(POSTER_MUTED))
        return title_h + grid_h + 8 + (bbox[3] - bbox[1])
    return title_h + grid_h


def _draw_photo_cell(ctx: PosterContext, x: int, y: int, w: int, h: int,
                     image, label: str, small_font) -> None:
    ctx.draw.rectangle((x, y, x + w, y + h),
                       outline=safe_image_color(POSTER_LINE), width=1, fill="#eeeae0")
    if image is not None:
        try:
            img = image.copy()
            img.thumbnail((w, h), Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img_x = x + (w - img.width) // 2
            img_y = y + (h - img.height) // 2
            ctx.image.paste(img, (img_x, img_y))
        except Exception as exc:                                # noqa: BLE001
            logger.warning("share: paste photo failed: %s", exc)
    label_box_h = 36
    ctx.draw.rectangle((x, y + h - label_box_h, x + w, y + h),
                       fill="#000000")
    ctx.draw.text((x + 12, y + h - label_box_h + 8),
                  label or "实勘图片", font=small_font, fill="#ffffff")


def _draw_footer_block(ctx: PosterContext) -> int:
    small_font = ctx.fonts["small"]
    text = ctx.facts.listing_id or ""
    bbox = ctx.draw.textbbox((0, 0), text, font=small_font)
    text_w = bbox[2] - bbox[0]
    line_h = 30
    ctx.draw.text(((POSTER_WIDTH - text_w) // 2, 4), text, font=small_font,
                  fill=safe_image_color(POSTER_MUTED))
    return line_h


# ---------------------------------------------------------------- helpers


def _draw_wrapped(ctx: PosterContext, text: str, font, *,
                  line_height: int, color: str) -> int:
    if not text:
        return 0
    max_w = POSTER_WIDTH - 2 * SECTION_PADDING
    lines = _wrap_text(ctx.draw, text, font, max_w)
    y = 0
    for line in lines:
        ctx.draw.text((SECTION_PADDING, y), line, font=font,
                      fill=safe_image_color(color))
        y += line_height
    return line_height * len(lines)


def _wrap_text(draw, text: str, font, max_w: int) -> list[str]:
    if not text:
        return []
    if _text_fits(draw, text, font, max_w):
        return [text]
    lines: list[str] = []
    cur = ""
    for char in text:
        candidate = cur + char
        if _text_fits(draw, candidate, font, max_w):
            cur = candidate
            continue
        if cur:
            lines.append(cur.rstrip() or cur)
        cur = char if not char.isspace() else ""
    if cur:
        lines.append(cur.rstrip() or cur)
    return lines or [text]


def _text_fits(draw, text: str, font, max_w: int) -> bool:
    if not text:
        return True
    bbox = draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0]) <= max_w


def _load_fonts(paths: Sequence[str]) -> dict[str, ImageFont.ImageFont]:
    candidates = list(paths) if paths else [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/Library/Fonts/PingFang.ttc",
    ]
    sizes = {"title": 40, "h2": 28, "body": 22, "small": 18}
    fonts: dict[str, ImageFont.ImageFont] = {}
    for name, size in sizes.items():
        font = None
        for path in candidates:
            try:
                font = ImageFont.truetype(path, size=size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        fonts[name] = font
    return fonts


def safe_image_color(value: str, default: str = POSTER_INK) -> tuple[int, int, int]:
    try:
        color = ImageColor.getrgb(value)
    except ValueError:
        color = ImageColor.getrgb(default)
    if len(color) >= 3:
        return color[:3]
    return ImageColor.getrgb(default)[:3]


def download_prospect_photo(url: str, *, opener=None,
                            timeout: float = PROSPECT_PHOTO_TIMEOUT) -> Image.Image | None:
    """下载一张实勘图,返回 PIL Image;失败返 None。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    open_fn = opener or urlopen
    try:
        request = Request(url, headers={"User-Agent": "store-media-share/1.0"})
        with open_fn(request, timeout=timeout) as response:
            data = response.read()
    except (HTTPError, URLError, TimeoutError, IncompleteRead, OSError) as exc:
        logger.warning("share: photo %s fetch failed: %s", url, exc.__class__.__name__)
        return None
    try:
        return Image.open(io.BytesIO(data))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("share: photo %s decode failed: %s", url, exc.__class__.__name__)
        return None


def preload_prospect_photos(
    photos: Sequence[ShareProspectPhoto],
    *,
    opener=None,
    timeout: float = PROSPECT_PHOTO_TIMEOUT,
    max_workers: int = 4,
) -> Mapping[str, Image.Image | None]:
    """并发下载实勘图,按 URL 返回 dict。失败的 URL 值是 None。

    - 默认 4 个 worker,小文件 + 同 CDN 并发就够了
    - 单图超时 `timeout`(默认 4s),超过的 URL 视为失败
    - 调用方把 dict 当 `photo_loader` 传入 `compose_share_poster`,
      内部 O(1) 查表,不再在拼图阶段触发额外下载
    """
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="share-photo")
    try:
        future_map = {
            pool.submit(
                download_prospect_photo, photo.url, opener=opener, timeout=timeout,
            ): photo.url
            for photo in photos
        }
        results: dict[str, Image.Image | None] = {}
        for future, url in future_map.items():
            try:
                results[url] = future.result(timeout=PROSPECT_DOWNLOAD_DEADLINE_SECONDS)
            except Exception as exc:                        # noqa: BLE001
                logger.warning("share: photo %s preload failed: %s", url, exc.__class__.__name__)
                results[url] = None
        return results
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def build_facts_from_row(
    row: dict[str, Any],
    *,
    city_lat: float | None = None,
    city_lng: float | None = None,
) -> ShareListingFacts:
    """从 `list_score_review_rows` 返回的 sqlite3.Row / 字典合成 share 用 facts。"""
    listing_id = str(row.get("listing_id") or "")
    quality_status = str(row.get("quality_status") or "")
    quality_tier = row.get("quality_tier")
    quality_score = row.get("quality_score")
    return ShareListingFacts(
        listing_id=listing_id,
        community=str(row.get("community_name") or ""),
        district=_first_district(row),
        bizcircle=str(row.get("bizcircle") or "").strip() or None,
        layout=str(row.get("layout") or _fallback_layout(row)),
        area_sqm=row.get("area_sqm"),
        monthly_rent_yuan=row.get("monthly_rent_yuan"),
        orientation=str(row.get("orientation") or "").strip() or None,
        floor=str(row.get("floor_desc") or "").strip() or None,
        unit_rent=row.get("unit_rent"),
        reference_unit_rent=row.get("reference_unit_rent"),
        expected_unit_rent=row.get("expected_unit_rent"),
        confidence_score=row.get("confidence_score"),
        benchmark_mode=row.get("benchmark_mode"),
        quality_status=quality_status,
        quality_tier=str(quality_tier) if quality_tier else None,
        quality_score=quality_score,
        reason=str(row.get("reason") or ""),
        city_lat=city_lat,
        city_lng=city_lng,
    )


def _first_district(row: dict[str, Any]) -> str | None:
    raw = row.get("districts_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            first = str(parsed[0]).strip()
            if first:
                return first
    persisted = row.get("persisted_district")
    if persisted:
        return str(persisted)
    return None


def _fallback_layout(row: dict[str, Any]) -> str:
    rooms = row.get("rooms") or 0
    halls = row.get("halls") or 0
    baths = row.get("baths") or 0
    return f"{rooms}室{halls}厅{baths}卫"
