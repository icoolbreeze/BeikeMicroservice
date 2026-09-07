"""分享海报拼图（零上游）。"""

from __future__ import annotations

import io

from PIL import Image

from app.application.roughcast_share import (
    PROSPECT_DOWNLOAD_DEADLINE_SECONDS,
    ShareListingFacts,
    ShareProspectPhoto,
    compose_share_poster,
    format_score_line,
    preload_prospect_photos,
    sanitize_share_reason,
)


def _tiny_image(color=(30, 140, 90)) -> Image.Image:
    return Image.new("RGB", (8, 8), color)


def _facts(**overrides) -> ShareListingFacts:
    base = dict(
        listing_id="106100000001",
        community="示例小区",
        district="成华",
        bizcircle="建设路",
        layout="3室2厅1卫",
        area_sqm=90.0,
        monthly_rent_yuan=2500.0,
        orientation="南",
        floor="中楼层",
        unit_rent=27.7,
        reference_unit_rent=50.0,
        expected_unit_rent=26.5,
        confidence_score=80,
        benchmark_mode="S_ONLY",
        quality_status="scored",
        quality_tier=None,
        quality_score=88,
        reason="装修参考单价 50.00 元/㎡ × δ 0.530 = 合理清水价 26.50 元/㎡",
        city_lat=30.69,
        city_lng=104.13,
    )
    base.update(overrides)
    return ShareListingFacts(**base)


def test_format_score_line_hides_integer_for_nearby() -> None:
    scored = _facts(quality_status="scored", quality_score=88)
    nearby = _facts(
        quality_status="nearby_estimate", quality_tier="疑似划算", quality_score=95,
    )
    assert format_score_line(scored) == "88 分"
    assert format_score_line(nearby) == "疑似划算"
    assert "95" not in format_score_line(nearby)


def test_compose_share_poster_returns_png() -> None:
    png = compose_share_poster(
        _facts(),
        map_image=_tiny_image((220, 60, 60)),
        map_placeholder=None,
        photos=[
            ShareProspectPhoto(url="https://img.example.com/a.jpg", label="客厅"),
            ShareProspectPhoto(url="https://img.example.com/b.jpg", label="卧室"),
        ],
        photo_loader=lambda _url: _tiny_image((40, 80, 160)),
    )
    assert png.startswith(b"\x89PNG")
    image = Image.open(io.BytesIO(png))
    assert image.size[0] == 1080
    assert image.size[1] > 400


def test_compose_share_poster_nearby_and_placeholder_map() -> None:
    png = compose_share_poster(
        _facts(
            quality_status="nearby_estimate",
            quality_tier="疑似划算",
            quality_score=95,
        ),
        map_image=None,
        map_placeholder="未配置百度 AK",
        photos=(),
    )
    assert png.startswith(b"\x89PNG")


def test_sanitize_share_reason_strips_formula_and_meta() -> None:
    raw = "装修参考单价 20.93 元/㎡ × δ 0.531 = 合理清水价 11.12 元/㎡；实际 23.03 元/㎡；S_ONLY / community；R=003"
    out = sanitize_share_reason(raw)
    assert "×" not in out
    assert "δ" not in out
    assert "合理清水价" not in out
    assert "S_ONLY" not in out
    assert "R=003" not in out
    assert "community" not in out
    assert "实际 23.03" in out
    assert "装修参考单价 20.93" in out


def test_sanitize_share_reason_passthrough_short_text() -> None:
    # insufficient / data_error 等没有公式的短文本,原样保留
    for raw in ("无可用基准", "疑似车位/非住房（1室0厅0卫），不评分", ""):
        assert sanitize_share_reason(raw) == raw


def test_preload_prospect_photos_parallel_and_partial_failure() -> None:
    """并发下载 4 张:2 张成功、1 张超时、1 张 404 → 整体 ≤ deadline。

    时长断言:即使 4 张里有 1 张人为 sleep 超过单图 timeout,
    `preload_prospect_photos` 也应在 `PROSPECT_DOWNLOAD_DEADLINE_SECONDS` 内
    返回(dict 形式,失败的 URL 值是 None)。
    """
    import time
    from urllib.error import URLError

    tiny_png = _tiny_image((40, 80, 160))
    buf = io.BytesIO()
    tiny_png.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    class _FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return self._data

    sleep_log: list[float] = []

    def _fake_open(request, timeout=None):
        url = request.full_url
        sleep_log.append(timeout or 0.0)
        if "hang" in url:
            time.sleep(10.0)        # 远超 timeout,会触发 per-call timeout
        if "404" in url:
            raise URLError("404")
        return _FakeResponse(png_bytes)

    photos = [
        ShareProspectPhoto(url="https://x/a-ok.jpg", label="a"),
        ShareProspectPhoto(url="https://x/slow.jpg", label="b"),
        ShareProspectPhoto(url="https://x/hang.jpg", label="c"),
        ShareProspectPhoto(url="https://x/404.jpg", label="d"),
    ]
    t0 = time.monotonic()
    results = preload_prospect_photos(
        photos, opener=_fake_open, timeout=0.3, max_workers=4,
    )
    elapsed = time.monotonic() - t0

    # 必须按 URL 索引;c / d 失败 → None
    assert set(results) == {p.url for p in photos}
    assert results["https://x/a-ok.jpg"] is not None
    assert results["https://x/slow.jpg"] is not None
    assert results["https://x/hang.jpg"] is None
    assert results["https://x/404.jpg"] is None
    # 并发 ≠ 串行:总耗时应在 deadline + 1s 内,远低于 4 张串行
    assert elapsed < PROSPECT_DOWNLOAD_DEADLINE_SECONDS + 1.0
    # 并发证据:4 张都拿到了自己的 per-call timeout
    assert len(sleep_log) == 4
    assert all(abs(t - 0.3) < 0.05 for t in sleep_log), sleep_log
