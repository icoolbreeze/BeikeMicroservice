"""分享海报 HTTP：假实勘 / 假静态图，零真实上游。"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

from app.domain.roughcast_score import ScoreResult
from app.infrastructure.database import Database
from app.infrastructure.roughcast_rental_fetcher import (
    RoughcastProspectGallery,
    RoughcastProspectPhoto,
    RoughcastRentalFetcher,
)
from app.infrastructure.roughcast_repository import RoughcastRepository
from app.infrastructure.settings import Settings
from app.main import create_app


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (6, 6), (180, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _score(listing_id: str) -> ScoreResult:
    return ScoreResult(
        listing_id=listing_id, unit_rent=27.7, reference_unit_rent=50.0,
        expected_unit_rent=26.5, advantage=0.05,
        quality_score_raw=88.0, quality_score=88, quality_status="scored",
        quality_tier=None, confidence_score=80, city_rank=1,
        peer_scope="community", comparable_grade="S", benchmark_mode="S_ONLY",
        effective_sample_count=3, reference_age_days=0,
        reference_community_count=None, reference_spread=None,
        extreme_price=0, reason="测试理由", benchmark_pool_json="[]",
        listing_run_id=1, reference_run_id=1,
    )


def _seed(tmp_path) -> Settings:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_communities ("
            " id, name, resblock_id, bizcircle, district, latitude, longitude, first_seen_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            ("RB1", "示例小区", "RB1", "建设路", "成华", 30.69, 104.13, "2026-08-24T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO roughcast_listing_current ("
            " listing_id, community_name, community_id, bizcircle, layout, rooms, halls, baths,"
            " area_sqm, monthly_rent_yuan, title_image_url, orientation, floor_desc,"
            " create_time, content_hash, first_seen_at, last_seen_at, last_seen_run_id, is_active"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                "106100000001", "示例小区", "RB1", "建设路", "3室2厅1卫", 3, 2, 1,
                90.0, 2500.0, "https://img.ljcdn.com/lease-image/house/cover.jpeg",
                "南", "中楼层", "2026-08-20T00:00:00+00:00", "hash",
                "2026-08-24T00:00:00+00:00", "2026-08-24T00:00:00+00:00", 1,
            ),
        )
    repo = RoughcastRepository(database)
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id, [_score("106100000001")], delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    return settings


def test_share_endpoint_returns_png(tmp_path, monkeypatch) -> None:
    settings = _seed(tmp_path)
    monkeypatch.setattr(
        RoughcastRentalFetcher,
        "prospect_for_id",
        lambda _self, _listing_id: RoughcastProspectGallery(photos=(
            RoughcastProspectPhoto(url="https://img.example.com/a.jpg", label="客厅"),
            RoughcastProspectPhoto(url="https://img.example.com/b.jpg", label="卧室"),
        )),
    )
    monkeypatch.setattr(
        "app.application.roughcast_share.download_prospect_photo",
        lambda _url, **_kwargs: Image.new("RGB", (8, 8), (20, 80, 160)),
    )
    monkeypatch.setattr(
        "app.infrastructure.baidu_map_client.fetch_static_city_map",
        lambda *_args, **_kwargs: _tiny_png(),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.get("/api/v1/display/roughcast-ranked/106100000001/share")
        second = client.get("/api/v1/display/roughcast-ranked/106100000001/share")
        bad = client.get("/api/v1/display/roughcast-ranked/ab/share")
        missing = client.get("/api/v1/display/roughcast-ranked/106199999999/share")
        js = client.get("/roughcast-rank.js")
        review_js = client.get("/score-review.js")
        ranked_body = client.get("/api/v1/display/roughcast-ranked").json()
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/png")
    assert first.content.startswith(b"\x89PNG")
    assert "roughcast-106100000001.png" in first.headers.get("content-disposition", "")
    # 同一 listing 连续两次分享,share_count 头必须 1 → 2 递增。
    assert first.headers.get("x-share-count") == "1"
    assert second.headers.get("x-share-count") == "2"
    # 排名 payload 必须带 share_count=2。
    target = next(item for item in ranked_body["items"] if item["listing_id"] == "106100000001")
    assert target["share_count"] == 2
    assert target["view_count"] == 0
    # 异常路径不能污染计数。
    assert bad.status_code == 400
    assert "x-share-count" not in bad.headers
    assert missing.status_code == 404
    assert "x-share-count" not in missing.headers
    assert "innerHTML" not in js.text
    assert "/share" in js.text
    assert "分享" in js.text
    assert "X-Share-Count" in js.text
    assert "已分享" in js.text
    assert "cd.ke.com" not in js.text
    assert "/share" in review_js.text
    assert "分享" in review_js.text
    assert "X-Share-Count" in review_js.text
    assert "已分享" in review_js.text
    assert "innerHTML" not in review_js.text
