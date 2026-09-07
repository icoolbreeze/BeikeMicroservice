"""Phase 5 first cut:本地自用排名榜 + 页面。

回归现有 `roughcast-score-review` 与 `roughcast-rentals` 不在本文件的职责里,
只是顺带跑那条线下的测试。这里只覆盖 ranked 端到端行为。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.domain.roughcast_score import ScoreResult
from app.infrastructure.database import Database
from app.infrastructure.district_catalog import parse_district_catalog
from app.infrastructure.roughcast_repository import RoughcastRepository
from app.infrastructure.settings import Settings
from app.main import create_app


class FakeCatalogFetcher:
    def __init__(self, catalog) -> None:
        self._catalog = catalog

    def latest(self):
        return self._catalog


def _score(
    listing_id: str,
    *,
    raw: float,
    score: int,
    status: str,
    confidence: int = 80,
    rank: int | None = None,
    tier: str | None = None,
    peer_scope: str = "community",
    benchmark_mode: str = "S_ONLY",
    unit_rent: float = 27.7,
    reference: float = 50.0,
    expected: float = 26.5,
    advantage: float = 0.05,
    reason: str = "测试",
) -> ScoreResult:
    return ScoreResult(
        listing_id=listing_id, unit_rent=unit_rent, reference_unit_rent=reference,
        expected_unit_rent=expected, advantage=advantage,
        quality_score_raw=raw, quality_score=score, quality_status=status,
        quality_tier=tier, confidence_score=confidence, city_rank=rank,
        peer_scope=peer_scope, comparable_grade="S", benchmark_mode=benchmark_mode,
        effective_sample_count=3, reference_age_days=0,
        reference_community_count=None, reference_spread=None,
        extreme_price=0, reason=reason, benchmark_pool_json="[]",
        listing_run_id=1, reference_run_id=1,
    )


def _insert_listing(
    database: Database,
    *,
    listing_id: str,
    community: str,
    bizcircle: str = "建设路",
    image: str = "https://img.ljcdn.com/lease-image/house/cover.jpeg",
    monthly_rent: float = 2500.0,
    area_sqm: float = 90.0,
) -> None:
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_listing_current ("
            " listing_id, community_name, community_id, bizcircle, layout, rooms, halls, baths,"
            " area_sqm, monthly_rent_yuan, title_image_url, orientation, floor_desc,"
            " create_time, content_hash, first_seen_at, last_seen_at, last_seen_run_id, is_active"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                listing_id, community, "RB" + listing_id[-3:], bizcircle, "3室2厅1卫", 3, 2, 1,
                area_sqm, monthly_rent, image, "南", "中楼层",
                "2026-08-20T00:00:00+00:00", "hash", "2026-08-20T00:00:00+00:00",
                "2026-08-20T00:00:00+00:00", 1,
            ),
        )


def _seed_district_catalog_fetcher(app, catalog) -> None:
    app.state.district_catalog_fetcher = FakeCatalogFetcher(catalog)


def test_ranked_page_assets_and_no_innerhtml(tmp_path) -> None:
    app = create_app(Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1"))
    with TestClient(app) as client:
        page = client.get("/roughcast-rank.html")
        css = client.get("/roughcast-rank.css")
        js = client.get("/roughcast-rank.js")
        empty_api = client.get("/api/v1/display/roughcast-ranked")
    assert page.status_code == 200
    assert css.status_code == 200
    assert js.status_code == 200
    assert "/roughcast-rank.css" in page.text
    assert "/roughcast-rank.js" in page.text
    assert 'id="grid"' in page.text
    assert 'id="districts"' in page.text
    # 跟 /score-review 同步:cover control id 必须是 coverFilter,默认 is-active,文案是「有封面」
    assert 'id="coverFilter"' in page.text
    assert "有封面" in page.text
    assert "openStatus" in page.text
    assert "data-group" in page.text
    assert "data-sort" in page.text
    assert "innerHTML" not in js.text
    assert "roughcast-ranked" in js.text
    # 兜底:HTML 已经没有 #coverToggle,JS 不准再 query / bind 它,否则 parse 阶段 null.addEventListener 抛错,fetchPage(true) 永远跑不到
    assert "coverToggle" not in js.text
    assert 'querySelector("#coverToggle")' not in js.text
    assert "roughcast-score-review" in js.text and "/open" in js.text
    assert "method: \"POST\"" in js.text
    assert "cd.ke.com" not in js.text
    assert "roughcast-rentals" not in js.text
    # renderItems 不能再 wipe state.items;分页三件套也必须留着
    assert "state.items = []" not in js.text
    assert "hasMore" in js.text
    assert "loadMore" in js.text
    assert "page" in js.text
    # cover 过滤行为必须跟 score-review 一致:CORE_DISTRICTS + 默认 requireCover = true
    assert "CORE_DISTRICTS" in js.text
    assert ("requireCover: true" in js.text) or ("requireCover = true" in js.text)
    assert "全部" in js.text
    assert "__core__" in js.text
    assert "selectedDistricts" in js.text
    # CSS:响应式宽度 + auto-fill 网格,适配 360px 视口
    assert "minmax" in css.text
    assert "auto-fill" in css.text
    assert "72rem" in css.text
    assert empty_api.status_code == 404


def test_ranked_api_default_group_orders_by_quality_raw(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="A 小区")
    _insert_listing(database, listing_id="106100000002", community="B 小区", bizcircle="南湖")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1,
                   reference=50.0, expected=24.0, unit_rent=22.0, advantage=0.10),
            _score("106100000002", raw=88.0, score=88, status="scored", rank=2,
                   reference=50.0, expected=25.0, unit_rent=20.0, advantage=0.20),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        body = client.get("/api/v1/display/roughcast-ranked").json()
    assert body["score_run_id"] == run_id
    assert body["group"] == "scored"
    assert body["sort_applied"] == "quality"
    assert body["deals"] is False
    assert body["min_confidence"] == 0
    assert body["require_cover"] is False
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 30
    assert body["has_more"] is False
    assert [item["listing_id"] for item in body["items"]] == ["106100000002", "106100000001"]
    assert all(item["city_rank"] is not None for item in body["items"])
    assert body["items"][0]["quality_score"] == 88
    assert body["items"][0]["quality_score_raw"] == 88.0
    assert body["group_counts"]["scored"] == 2
    assert body["group_counts"]["nearby_estimate"] == 0
    assert body["group_counts"]["insufficient"] == 0
    assert body["group_counts"]["data_error"] == 0


def test_ranked_require_cover_true_round_trips(tmp_path) -> None:
    """前端发 require_cover=true 时,服务端必须 echo 回 true。

    默认仍是 false(由其它 test 验证),只有显式带 true 才翻转,跟 /score-review 一致。
    """
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="A 小区")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [_score("106100000001", raw=72.0, score=72, status="scored", rank=1)],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        default_body = client.get("/api/v1/display/roughcast-ranked").json()
        forced = client.get(
            "/api/v1/display/roughcast-ranked", params={"require_cover": "true"}
        ).json()
    # 服务端默认仍然是 false(由 test_ranked_api_default_group_orders_by_quality_raw 守住)
    assert default_body["require_cover"] is False
    # 显式带 true 时,响应里也是 true
    assert forced["require_cover"] is True


def test_ranked_nearby_separate_group_with_no_city_rank(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="A")
    _insert_listing(database, listing_id="106100000002", community="B", bizcircle="南湖")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1,
                   reference=50.0, expected=24.0, unit_rent=22.0),
            _score("106100000002", raw=80.0, score=80, status="nearby_estimate",
                   rank=None, tier="疑似划算", peer_scope="nearby",
                   reference=50.0, expected=25.0, unit_rent=20.0),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        scored = client.get(
            "/api/v1/display/roughcast-ranked", params={"group": "scored"}
        ).json()
        nearby = client.get(
            "/api/v1/display/roughcast-ranked", params={"group": "nearby_estimate"}
        ).json()
    scored_ids = {item["listing_id"] for item in scored["items"]}
    assert scored_ids == {"106100000001"}
    assert scored["group_counts"]["scored"] == 1
    assert scored["group_counts"]["nearby_estimate"] == 1
    nearby_ids = {item["listing_id"] for item in nearby["items"]}
    assert nearby_ids == {"106100000002"}
    nearby_card = nearby["items"][0]
    assert nearby_card["city_rank"] is None
    assert nearby_card["quality_tier"] == "疑似划算"
    assert nearby_card["quality_status"] == "nearby_estimate"
    # 前端承诺不渲染整数,但 payload 仍可带 quality_score 供 δ / replay
    assert "quality_score" in nearby_card


def test_ranked_deals_excludes_nearby_and_keeps_only_high(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    for lid, biz in (("106100000001", "建设路"), ("106100000002", "南湖"),
                     ("106100000003", "东郊")):
        _insert_listing(database, listing_id=lid, community=f"小区-{lid}", bizcircle=biz)
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=80.0, score=80, status="scored", rank=1,
                   confidence=85),
            # 邻居组高分也不能进 deals:周边估算没有 confidence 可言
            _score("106100000002", raw=95.0, score=95, status="nearby_estimate",
                   rank=None, tier="疑似划算", peer_scope="nearby", confidence=90),
            _score("106100000003", raw=90.0, score=90, status="scored", rank=2,
                   confidence=70),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
                {"name": "东郊", "value": "3", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        body = client.get(
            "/api/v1/display/roughcast-ranked", params={"deals": "true"}
        ).json()
    assert body["deals"] is True
    assert body["group"] == "scored"
    ids = {item["listing_id"] for item in body["items"]}
    assert ids == {"106100000001", "106100000003"}
    assert body["total"] == 2


def test_ranked_min_confidence_drops_low_score(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="A")
    _insert_listing(database, listing_id="106100000002", community="B", bizcircle="南湖")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1,
                   confidence=85),
            _score("106100000002", raw=68.0, score=68, status="scored", rank=2,
                   confidence=50),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        body = client.get(
            "/api/v1/display/roughcast-ranked", params={"min_confidence": 80}
        ).json()
    assert body["total"] == 1
    assert body["items"][0]["listing_id"] == "106100000001"


def test_ranked_district_filter_scopes_cards(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="建设路小区")
    _insert_listing(database, listing_id="106100000002", community="南湖小区", bizcircle="南湖")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1),
            _score("106100000002", raw=68.0, score=68, status="scored", rank=2),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        all_rows = client.get("/api/v1/display/roughcast-ranked").json()
        chenghua = client.get(
            "/api/v1/display/roughcast-ranked", params={"district": "成华"}
        ).json()
        both = client.get(
            "/api/v1/display/roughcast-ranked",
            params=[("district", "成华"), ("district", "双流")],
        ).json()
    assert {item["name"] for item in all_rows["districts"]} == {"成华", "双流"}
    assert chenghua["total"] == 1
    assert chenghua["items"][0]["district"] == "成华"
    assert both["total"] == 2
    assert both["selected_districts"] == ["成华", "双流"]


def test_ranked_district_chips_remain_after_selecting_one(tmp_path) -> None:
    """选中「成华」后:items 只收窄到成华,但 chips 仍要展示「成华 + 双流」。

    否则用户没法在不点「全部」的前提下加选 / 切到双流。跟
    `/score-review.html` 的 chip 计数口径一致:group + cover 之后、区筛选之前。
    """
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="建设路小区")
    _insert_listing(database, listing_id="106100000002", community="南湖小区", bizcircle="南湖")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1),
            _score("106100000002", raw=68.0, score=68, status="scored", rank=2),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        chenghua = client.get(
            "/api/v1/display/roughcast-ranked", params={"district": "成华"}
        ).json()
    # items 只剩成华
    assert chenghua["selected_districts"] == ["成华"]
    assert chenghua["total"] == 1
    assert {item["listing_id"] for item in chenghua["items"]} == {"106100000001"}
    assert all(item["district"] == "成华" for item in chenghua["items"])
    # chips 仍包含两个区,这样用户能直接切去双流
    assert {item["name"] for item in chenghua["districts"]} == {"成华", "双流"}
    # counts 也按区分别给,不能因为过滤就归零
    chenghua_chip = next(c for c in chenghua["districts"] if c["name"] == "成华")
    shuangliu_chip = next(c for c in chenghua["districts"] if c["name"] == "双流")
    assert chenghua_chip["count"] == 1
    assert shuangliu_chip["count"] == 1


def test_ranked_insufficient_and_data_error_carry_no_score(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="OK")
    _insert_listing(database, listing_id="106100000002", community="INSUF", bizcircle="南湖")
    _insert_listing(database, listing_id="106100000003", community="ERR", bizcircle="东郊")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1),
            _score("106100000002", raw=None, score=None, status="insufficient",
                   rank=None, reason="无可用基准"),
            _score("106100000003", raw=None, score=None, status="data_error",
                   rank=None, reason="疑似车位"),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
                {"name": "东郊", "value": "3", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        insuf = client.get(
            "/api/v1/display/roughcast-ranked", params={"group": "insufficient"}
        ).json()
        err = client.get(
            "/api/v1/display/roughcast-ranked", params={"group": "data_error"}
        ).json()
    assert insuf["total"] == 1
    assert insuf["items"][0]["quality_score"] is None
    assert insuf["items"][0]["quality_status"] == "insufficient"
    assert err["total"] == 1
    assert err["items"][0]["quality_score"] is None
    assert err["items"][0]["quality_status"] == "data_error"


def test_ranked_pagination_sets_has_more(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    _insert_listing(database, listing_id="106100000001", community="A")
    _insert_listing(database, listing_id="106100000002", community="B", bizcircle="南湖")
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [
            _score("106100000001", raw=72.0, score=72, status="scored", rank=1),
            _score("106100000002", raw=68.0, score=68, status="scored", rank=2),
        ],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    _seed_district_catalog_fetcher(app, parse_district_catalog([
        {"key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
        ]},
    ]))
    with TestClient(app) as client:
        body = client.get(
            "/api/v1/display/roughcast-ranked",
            params={"page_size": 1, "page": 1},
        ).json()
    assert body["total"] == 2
    assert body["has_more"] is True
    assert body["items"][0]["listing_id"] == "106100000001"
