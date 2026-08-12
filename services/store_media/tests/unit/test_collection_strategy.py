import json
from unittest.mock import Mock

from app.infrastructure.featured_fetcher import (
    FeaturedListingsFetcher, FeaturedSnapshotStore, NEARBY_CENTER,
    NEARBY_RADIUS_METERS, PRIORITY_COMMUNITIES,
)

# 造一条完整可展示的租赁行（普租 del_type=2，实勘桶图片）
_PZ_ROW = {
    "listing_id": "R100",
    "community": "成发紫悦府",
    "layout": "3室2厅2卫",
    "area_sqm": 114.0,
    "monthly_rent_yuan": 4500,
    "orientation": "南北",
    "del_type": 2,
    "title_image_url": "https://img.ljcdn.com//110000-inspection/pc1_R100.jpg",
}

_TG_ROW = {
    "listing_id": "R200",
    "community": "成发紫悦府",
    "layout": "2室1厅1卫",
    "area_sqm": 60.0,
    "monthly_rent_yuan": 3200,
    "orientation": "南",
    "del_type": 5,
    "title_image_url": "https://img.ljcdn.com/lease-image/house/r200.jpeg",
}

# 造一条完整可展示的买卖行
_SALE_ROW = {
    "listing_id": "S100",
    "community": "成发紫悦府",
    "layout": "3-2-2",
    "area_sqm": 120.0,
    "total_price_yuan": 2_000_000,
    "total_price_text": "200万",
    "orientation": "南北",
    "surface_image_url": "https://img.ljcdn.com//110000-inspection/pc1_S100.jpg",
}


def _make_fetcher(routes) -> FeaturedListingsFetcher:
    fetcher = FeaturedListingsFetcher("http://unused", cache_seconds=3600)
    mock = Mock()
    mock.side_effect = (
        routes if callable(routes)
        else lambda path, payload: json.loads(routes[path])
    )
    fetcher._post_json = mock
    return fetcher


def test_rent_collection_uses_unrestricted_scope_and_keeps_priority_order() -> None:
    """租赁指定小区使用 CRM 的“不限”范围，原图房源在同一小区中排前。"""
    routes = {
        "/api/v1/listings/rental/search": json.dumps(
            {"items": [_TG_ROW, _PZ_ROW], "has_more": False}
        ),
        "/api/v1/listings/rental/map/nearby": json.dumps(
            {"community_ids": []}
        ),
    }
    fetcher = _make_fetcher(routes)
    rent, total = fetcher._collect_rent()
    assert total == 2
    assert {item.id for item in rent} == {"R100", "R200"}
    # 托管（带原图）排前面，普租在后面
    assert rent[0].id == "R200"
    assert rent[0].original_image is not None
    assert rent[1].id == "R100"
    assert rent[1].original_image is None
    search_calls = [
        call for call in fetcher._post_json.call_args_list
        if call.args[0] == "/api/v1/listings/rental/search"
    ]
    assert len(search_calls) == len(PRIORITY_COMMUNITIES)
    scopes = {call.args[1]["scope"] for call in search_calls}
    assert scopes == {"all"}
    for call in search_calls:
        assert len(call.args[1]["resblock_ids"]) == 1


def test_rent_collection_caps_none_and_keeps_all_pages() -> None:
    """没有数量上限：多页数据全部保留。"""
    rows_page1 = [
        {**_TG_ROW, "listing_id": f"R{i}"} for i in range(50)
    ]
    routes = {
        "/api/v1/listings/rental/search": json.dumps(
            {"items": rows_page1, "has_more": True}
        ),
    }
    fetcher = _make_fetcher(routes)
    rent, total = fetcher._collect_rent()
    assert total == len(rows_page1)


def test_sale_collection_scopes_all_and_checks_nearby_when_sparse() -> None:
    """买卖指定小区不足 6 套时，执行范围查询。"""
    routes = {
        "/api/v1/listings/sale/search": json.dumps(
            {"items": [_SALE_ROW], "has_more": False}
        ),
        "/api/v1/listings/sale/map/nearby": json.dumps(
            {"community_ids": []}
        ),
    }
    fetcher = _make_fetcher(routes)
    sale, total = fetcher._collect_sale()
    assert total == 1
    assert sale[0].id == "S100"
    search_calls = [
        call for call in fetcher._post_json.call_args_list
        if call.args[0] == "/api/v1/listings/sale/search"
    ]
    assert all(call.args[1]["scope"] == "all" for call in search_calls)
    assert all(
        len(call.args[1]["community_ids"]) == 1
        for call in search_calls
    )
    nearby_call = next(
        call for call in fetcher._post_json.call_args_list
        if call.args[0] == "/api/v1/listings/sale/map/nearby"
    )
    assert nearby_call.args[1]["location"] == NEARBY_CENTER
    assert nearby_call.args[1]["radius_meters"] == NEARBY_RADIUS_METERS


def test_nearby_results_are_appended_after_priority_for_sale() -> None:
    nearby_row = {
        **_SALE_ROW,
        "listing_id": "S200",
        "community": "水碾河社区",
    }

    def route(path, payload):
        if path == "/api/v1/listings/sale/map/nearby":
            return {"community_ids": ["nearby-1"]}
        if payload["community_ids"] == ["nearby-1"]:
            return {"items": [nearby_row], "has_more": False}
        return {"items": [_SALE_ROW], "has_more": False}

    fetcher = _make_fetcher(route)
    sale, total = fetcher._collect_sale()
    assert total == 2
    assert [item.id for item in sale] == ["S100", "S200"]


def test_nearby_results_are_appended_after_priority_for_rent() -> None:
    nearby_row = {
        **_TG_ROW,
        "listing_id": "R300",
        "community": "水碾河社区",
    }

    def route(path, payload):
        if path == "/api/v1/listings/rental/map/nearby":
            return {"community_ids": ["nearby-1"]}
        if payload["resblock_ids"] == ["nearby-1"]:
            return {"items": [nearby_row], "has_more": False}
        return {"items": [_PZ_ROW], "has_more": False}

    fetcher = _make_fetcher(route)
    rent, total = fetcher._collect_rent()
    assert total == 2
    assert [item.id for item in rent] == ["R100", "R300"]


def test_collect_returns_empty_feed_when_no_rows() -> None:
    routes = {
        "/api/v1/listings/sale/search": json.dumps(
            {"items": [], "has_more": False}
        ),
        "/api/v1/listings/rental/search": json.dumps(
            {"items": [], "has_more": False}
        ),
        "/api/v1/listings/sale/map/nearby": json.dumps(
            {"community_ids": []}
        ),
        "/api/v1/listings/rental/map/nearby": json.dumps(
            {"community_ids": []}
        ),
    }
    fetcher = _make_fetcher(routes)
    assert fetcher._collect_sale() is None
    assert fetcher._collect_rent() is None


def test_snapshot_store_reads_exported_feed(tmp_path) -> None:
    snapshot = tmp_path / "featured_snapshot.json"
    snapshot.write_text(json.dumps({
        "sale": [],
        "rent": [{
            "id": "R1", "title": "成发紫悦府", "layout": "2室1厅1卫",
            "area": "60", "floor": "—", "orient": "南", "decor": "—",
            "price": "3200", "priceUnit": "元/月", "unitPrice": "53",
            "location": "成发紫悦府", "tags": [], "image": "https://img/R1.jpg",
            "desc": "真实房源", "original_image": None,
        }],
        "sale_total": 0, "rent_total": 1, "updated_at": "2026-08-11 12:00:00",
    }, ensure_ascii=False), encoding="utf-8")

    feed = FeaturedSnapshotStore(snapshot).latest()

    assert feed is not None
    assert feed.rent[0].id == "R1"
    assert feed.rent_total == 1
