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


def _score(listing_id: str, *, rank: int, extreme: int = 0) -> ScoreResult:
    return ScoreResult(
        listing_id=listing_id, unit_rent=27.7, reference_unit_rent=50.0,
        expected_unit_rent=26.5, advantage=0.05 if not extreme else 1.2,
        quality_score_raw=54.6, quality_score=55, quality_status="scored",
        quality_tier=None, confidence_score=80, city_rank=rank,
        peer_scope="community", comparable_grade="S", benchmark_mode="S_ONLY",
        effective_sample_count=3, reference_age_days=0,
        reference_community_count=None, reference_spread=None,
        extreme_price=extreme, reason="测试", benchmark_pool_json="[]",
        listing_run_id=1, reference_run_id=1,
    )


def test_review_page_and_empty_api(tmp_path) -> None:
    app = create_app(Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1"))
    with TestClient(app) as client:
        page = client.get("/score-review.html")
        assert page.status_code == 200
        assert "score-review.js" in page.text
        assert "openStatus" in page.text
        css = client.get("/score-review.css")
        js = client.get("/score-review.js")
        assert css.status_code == 200
        assert js.status_code == 200
        assert "innerHTML" not in js.text
        assert "/open" in js.text
        assert 'method: "POST"' in js.text
        assert "cd.ke.com" not in js.text
        assert "data-district" in js.text
        assert "selectedDistricts" in js.text
        assert "CORE_DISTRICTS" in js.text
        assert "requireCover" in js.text
        assert "coverFilter" in page.text
        assert "is-seen" in js.text
        assert "view_count" in js.text
        assert "id=\"districts\"" in page.text
        empty = client.get("/api/v1/display/roughcast-score-review")
        assert empty.status_code == 404


def test_review_api_returns_cover_image(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_listing_current ("
            " listing_id, community_name, community_id, layout, rooms, halls, baths,"
            " area_sqm, monthly_rent_yuan, title_image_url, content_hash,"
            " first_seen_at, last_seen_at, last_seen_run_id, is_active"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                "106100000001", "示例小区", "RB1", "3室2厅1卫", 3, 2, 1,
                90.0, 2500.0,
                "https://img.ljcdn.com/lease-image/house/cover.jpeg",
                "hash", "2026-08-24T00:00:00+00:00", "2026-08-24T00:00:00+00:00", 1,
            ),
        )
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id, [_score("106100000001", rank=1)], delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        payload = client.get("/api/v1/display/roughcast-score-review").json()
    assert payload["score_run_id"] == run_id
    assert payload["groups"]["top"][0]["community"] == "示例小区"
    assert payload["groups"]["top"][0]["image"].endswith("cover.jpeg")
    assert payload["groups"]["top"][0]["view_count"] == 0
    assert payload["groups"]["top"][0]["ke_url"].endswith(
        "/rent/house/detail/106100000001"
    )
    assert "lease-pz.link.lianjia.com" in payload["groups"]["top"][0]["ke_url"]
    assert payload["filtered_count"] == 1
    assert payload["districts"][0]["name"] == "未知"


def test_review_api_filters_by_district(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1")
    database = Database(settings.database_path)
    database.initialize()
    repo = RoughcastRepository(database)
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_listing_current ("
            " listing_id, community_name, community_id, bizcircle, layout, rooms, halls, baths,"
            " area_sqm, monthly_rent_yuan, title_image_url, content_hash,"
            " first_seen_at, last_seen_at, last_seen_run_id, is_active"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                "106100000001", "建设路小区", "RB1", "建设路", "3室2厅1卫", 3, 2, 1,
                90.0, 2500.0, "https://img.ljcdn.com/lease-image/house/cover.jpeg",
                "hash", "2026-08-24T00:00:00+00:00", "2026-08-24T00:00:00+00:00", 1,
            ),
        )
        db.execute(
            "INSERT INTO roughcast_listing_current ("
            " listing_id, community_name, community_id, bizcircle, layout, rooms, halls, baths,"
            " area_sqm, monthly_rent_yuan, title_image_url, content_hash,"
            " first_seen_at, last_seen_at, last_seen_run_id, is_active"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                "106100000002", "南湖小区", "RB2", "南湖", "2室1厅1卫", 2, 1, 1,
                70.0, 1800.0, "https://img.ljcdn.com/lease-image/house/cover2.jpeg",
                "hash2", "2026-08-24T00:00:00+00:00", "2026-08-24T00:00:00+00:00", 1,
            ),
        )
    run_id = repo.start_score_run(
        model_version="v2.1.0", delta_version=1, k_scale=0.5, listing_run_id=1,
    )
    repo.complete_score_run(
        run_id,
        [_score("106100000001", rank=1), _score("106100000002", rank=2, extreme=1)],
        delta_value=0.53, delta_note="test",
        model_version="v2.1.0", delta_version=1,
    )
    catalog = parse_district_catalog([{
        "key": "districtId",
        "name": "商圈",
        "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
            {"name": "天府新区", "value": "23008850", "children": [
                {"name": "南湖", "value": "3", "children": []},
            ]},
        ],
    }])
    app = create_app(settings)
    app.state.district_catalog_fetcher = FakeCatalogFetcher(catalog)
    with TestClient(app) as client:
        all_rows = client.get("/api/v1/display/roughcast-score-review").json()
        chenghua = client.get(
            "/api/v1/display/roughcast-score-review", params={"district": "成华"}
        ).json()
        shuangliu = client.get(
            "/api/v1/display/roughcast-score-review", params={"district": "双流"}
        ).json()
        both = client.get(
            "/api/v1/display/roughcast-score-review",
            params=[("district", "成华"), ("district", "双流")],
        ).json()
        covered = client.get(
            "/api/v1/display/roughcast-score-review",
            params={"district": "成华", "require_cover": "true"},
        ).json()
        with database.connect() as db:
            db.execute(
                "UPDATE roughcast_listing_current SET title_image_url='' WHERE listing_id=?",
                ("106100000001",),
            )
        empty_cover = client.get(
            "/api/v1/display/roughcast-score-review",
            params={"district": "成华", "require_cover": "true"},
        ).json()
    assert {item["name"] for item in all_rows["districts"]} == {"成华", "双流", "天府新区"}
    assert all_rows["groups"]["top"][0]["district"] == "成华"
    assert all_rows["groups"]["top"][0]["bizcircle"] == "建设路"
    assert all_rows["groups"]["top"][1]["district"] == "双流/天府新区"
    assert chenghua["selected_district"] == "成华"
    assert chenghua["selected_districts"] == ["成华"]
    assert chenghua["filtered_count"] == 1
    assert chenghua["groups"]["top"][0]["community"] == "建设路小区"
    assert shuangliu["filtered_count"] == 1
    assert shuangliu["groups"]["top"][0]["community"] == "南湖小区"
    assert both["selected_districts"] == ["成华", "双流"]
    assert both["filtered_count"] == 2
    assert {item["community"] for item in both["groups"]["top"]} == {"建设路小区", "南湖小区"}
    assert covered["require_cover"] is True
    assert covered["filtered_count"] == 1
    assert empty_cover["filtered_count"] == 0


def test_review_open_proxies_to_workbench_client(tmp_path) -> None:
    app = create_app(Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1"))

    class FakeClient:
        def __init__(self) -> None:
            self.ids: list[str] = []

        def open_rental_listing(self, listing_id: str) -> dict:
            self.ids.append(listing_id)
            return {
                "listing_id": listing_id,
                "url": f"https://lease-pz.link.lianjia.com/rent/house/detail/{listing_id}",
                "opened": True,
            }

    opener = FakeClient()
    with TestClient(app) as client:
        bad = client.post("/api/v1/display/roughcast-score-review/ab/open")
        app.state.workbench_open_client = opener
        ok = client.post("/api/v1/display/roughcast-score-review/106100000001/open")
        again = client.post("/api/v1/display/roughcast-score-review/106100000001/open")
    assert bad.status_code == 400
    assert ok.status_code == 200
    body = ok.json()
    assert body["opened"] is True
    assert body["url"].endswith("/rent/house/detail/106100000001")
    assert body["view_count"] == 1
    assert again.json()["view_count"] == 2
    assert opener.ids == ["106100000001", "106100000001"]
