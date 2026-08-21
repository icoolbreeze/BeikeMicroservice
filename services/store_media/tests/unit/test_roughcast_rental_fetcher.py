from dataclasses import asdict

from app.infrastructure.roughcast_rental_fetcher import RoughcastRentalFetcher


def test_fetcher_uses_fixed_roughcast_query_and_only_returns_display_fields(monkeypatch) -> None:
    fetcher = RoughcastRentalFetcher("http://connector.example", cache_seconds=60)
    requests: list[tuple[str, dict]] = []

    def fake_post_json(path: str, payload: dict):
        requests.append((path, payload))
        return {
            "has_more": True,
            "items": [
                {
                    "community": "  Riverside Garden  ",
                    "layout": " 2 bed 1 bath ",
                    "area_sqm": "89.5",
                    "monthly_rent_yuan": "4300",
                    "orientation": " South ",
                    "floor_desc": " Low floor ",
                    "total_floors": 24,
                    "title_image_url": (
                        "https://img.ljcdn.com/110000-inspection/pc1_example.jpg!m_fill"
                    ),
                    "listing_id": "crm-private-id",
                    "owner_phone": "not-for-display",
                    "houseCurrentStatus": "not-a-product-filter",
                },
                {"community": "", "layout": "must be dropped"},
                {
                    "community": "Unknown House",
                    "layout": None,
                    "area_sqm": "not-a-number",
                    "monthly_rent_yuan": True,
                    "orientation": "",
                    "total_floors": "18",
                    "title_image_url": "javascript:alert(1)",
                },
            ]
        }

    monkeypatch.setattr(fetcher, "_post_json", fake_post_json)

    feed = fetcher.latest()

    assert requests == [
        (
            "/api/v1/listings/rental/search",
            {
                "scope": "all",
                "condition_filters": {"fitment": "002"},
                "page": 1,
                "page_size": 30,
            },
        )
    ]
    assert feed is not None
    assert len(feed.items) == 2
    assert feed.page == 1
    assert feed.has_more is True

    primary = asdict(feed.items[0])
    assert primary == {
        "listing_id": "crm-private-id",
        "community": "Riverside Garden",
        "layout": "2 bed 1 bath",
        "area_sqm": 89.5,
        "monthly_rent_yuan": 4300.0,
        "orientation": "South",
        "floor": "Low floor · 共24层",
        "image": "https://img.ljcdn.com/110000-inspection/pc1_example.jpg.1500x.jpg",
    }
    assert set(primary) == {
        "listing_id", "community", "layout", "area_sqm", "monthly_rent_yuan",
        "orientation", "floor", "image",
    }

    incomplete = asdict(feed.items[1])
    assert incomplete["area_sqm"] is None
    assert incomplete["monthly_rent_yuan"] is None
    assert incomplete["image"] is None
    assert incomplete["listing_id"] is None
    assert incomplete["floor"]
    assert incomplete["layout"]
    assert incomplete["orientation"]

    # The cache prevents another connector request for the same short-lived feed.
    assert fetcher.latest() is feed
    assert len(requests) == 1


def test_fetcher_returns_cached_feed_when_the_connector_is_temporarily_unavailable(monkeypatch) -> None:
    fetcher = RoughcastRentalFetcher("http://connector.example", cache_seconds=0)
    responses = iter([
        {"items": [{"community": "Cached House"}]},
        None,
    ])

    monkeypatch.setattr(fetcher, "_post_json", lambda _path, _payload: next(responses))

    cached = fetcher.latest()
    assert cached is not None
    assert fetcher.latest() is cached


def test_fetcher_pages_without_changing_fixed_filters(monkeypatch) -> None:
    fetcher = RoughcastRentalFetcher("http://connector.example", cache_seconds=60)
    requests: list[dict] = []

    def fake_post_json(_path: str, payload: dict):
        requests.append(payload)
        return {
            "items": [{"listing_id": f"row-{payload['page']}", "community": "Page House"}],
            "has_more": payload["page"] < 2,
        }

    monkeypatch.setattr(fetcher, "_post_json", fake_post_json)

    first = fetcher.latest(page=1)
    second = fetcher.latest(page=2)

    assert first is not None and first.has_more is True
    assert second is not None and second.has_more is False
    assert [request["page"] for request in requests] == [1, 2]
    assert all(request["scope"] == "all" for request in requests)
    assert all(request["condition_filters"] == {"fitment": "002"} for request in requests)


def test_fetcher_backfills_floor_from_detail_for_an_older_connector(monkeypatch) -> None:
    fetcher = RoughcastRentalFetcher("http://connector.example", cache_seconds=60)
    detail_paths: list[str] = []
    monkeypatch.setattr(fetcher, "_post_json", lambda _path, _payload: {
        "items": [{
            "listing_id": "RC-floor-1",
            "community": "Floor House",
            "del_type": 2,
            "floor_desc": None,
            "total_floors": None,
        }],
        "has_more": False,
    })

    def fake_get_json(path: str):
        detail_paths.append(path)
        return {"floor_desc": "中楼层", "total_floors": 32}

    monkeypatch.setattr(fetcher, "_get_json", fake_get_json)

    feed = fetcher.latest()

    assert feed is not None
    assert feed.items[0].floor == "中楼层 · 共32层"
    assert detail_paths == ["/api/v1/listings/rental/RC-floor-1"]


def test_prospect_only_exposes_safe_real_photos_for_seen_listings(monkeypatch) -> None:
    fetcher = RoughcastRentalFetcher("http://connector.example", cache_seconds=60)
    monkeypatch.setattr(fetcher, "_post_json", lambda _path, _payload: {
        "items": [{"listing_id": "RC-1", "community": "Gallery House"}],
        "has_more": False,
    })
    monkeypatch.setattr(fetcher, "_get_json", lambda path: {
        "listing_id": "RC-1",
        "photos": [
            {
                "url": "https://img.ljcdn.com/inspection/real-one.jpg",
                "room_name": "客厅",
                "image_type": "REAL",
                "upload_user": "must-not-leak",
            },
            {
                "url": "https://img.ljcdn.com/inspection/title-one.jpg",
                "room_name": "标题",
                "image_type": "TITLE",
            },
            {"url": "javascript:alert(1)", "room_name": "bad", "image_type": "REAL"},
        ],
    } if path.endswith("/RC-1/prospect") else None)

    assert fetcher.latest() is not None
    gallery = fetcher.prospect("RC-1")

    assert gallery is not None
    assert [asdict(photo) for photo in gallery.photos] == [{
        "url": "https://img.ljcdn.com/inspection/real-one.jpg.1500x.jpg",
        "label": "客厅",
    }]
    assert fetcher.prospect("not-seen") is None
