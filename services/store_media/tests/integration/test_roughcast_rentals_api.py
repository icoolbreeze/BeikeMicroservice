from fastapi.testclient import TestClient

from app.infrastructure.roughcast_rental_fetcher import (
    RoughcastRentalFeed,
    RoughcastRentalFetcher,
    RoughcastRentalListing,
    RoughcastProspectGallery,
    RoughcastProspectPhoto,
)
from app.infrastructure.settings import Settings
from app.main import create_app


def test_roughcast_mobile_page_and_assets_are_served(tmp_path) -> None:
    app = create_app(Settings(storage_dir=tmp_path))

    with TestClient(app) as client:
        page = client.get("/roughcast.html")
        script = client.get("/roughcast.js")
        stylesheet = client.get("/roughcast.css")

    assert page.status_code == 200
    assert "/roughcast.css" in page.text
    assert "/roughcast.js" in page.text
    assert 'id="refreshButton"' not in page.text
    assert "scope-card" not in page.text
    assert 'id="pullRefresh"' in page.text
    assert script.status_code == 200
    assert "roughcast-rentals" in script.text
    assert "innerHTML" not in script.text
    assert "naturalWidth" in script.text
    assert "Math.max(1.15" in script.text
    assert "IntersectionObserver" in script.text
    assert 'addEventListener("touchmove"' in script.text
    assert stylesheet.status_code == 200
    assert "listing-card" in stylesheet.text
    assert "object-fit: contain" in stylesheet.text
    assert "scroll-snap-type" in stylesheet.text
    assert "height: 100dvh" in stylesheet.text
    assert "margin-top: auto" not in stylesheet.text


def test_roughcast_rentals_endpoint_exposes_only_the_fixed_display_feed(monkeypatch, tmp_path) -> None:
    requested_pages: list[int] = []

    def fake_latest(self, page=1):
        assert isinstance(self, RoughcastRentalFetcher)
        requested_pages.append(page)
        return RoughcastRentalFeed(
            items=(
                RoughcastRentalListing(
                    listing_id="RC-1",
                    community="Riverside Garden",
                    layout="2 bed 1 bath",
                    area_sqm=89.5,
                    monthly_rent_yuan=4300,
                    orientation="South",
                    floor="Low floor · 共24层",
                    image="https://img.ljcdn.com/lease-image/house/example.jpeg",
                ),
            ),
            updated_at="2026-08-19 10:00:00",
            page=page,
            has_more=True,
        )

    monkeypatch.setattr(RoughcastRentalFetcher, "latest", fake_latest)
    app = create_app(Settings(
        storage_dir=tmp_path,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
        crm_connector_base_url="http://127.0.0.1:1",
    ))

    with TestClient(app) as client:
        # Query-string attempts cannot alter filters; only bounded pagination is public.
        response = client.get(
            "/api/v1/display/roughcast-rentals?page=2&scope=mine&fitment=001&houseCurrentStatus=online"
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [{
            "listing_id": "RC-1",
            "community": "Riverside Garden",
            "layout": "2 bed 1 bath",
            "area_sqm": 89.5,
            "monthly_rent_yuan": 4300.0,
            "orientation": "South",
            "floor": "Low floor · 共24层",
            "image": "https://img.ljcdn.com/lease-image/house/example.jpeg",
        }],
        "updated_at": "2026-08-19 10:00:00",
        "page": 2,
        "has_more": True,
    }
    assert requested_pages == [2]


def test_roughcast_rentals_endpoint_reports_upstream_unavailability(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(RoughcastRentalFetcher, "latest", lambda _self, page=1: None)
    app = create_app(Settings(
        storage_dir=tmp_path,
        crm_connector_base_url="http://127.0.0.1:1",
    ))

    with TestClient(app) as client:
        response = client.get("/api/v1/display/roughcast-rentals")

    assert response.status_code == 503


def test_roughcast_prospect_endpoint_only_returns_sanitized_gallery(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(RoughcastRentalFetcher, "knows_listing", lambda _self, listing_id: listing_id == "RC-1")
    monkeypatch.setattr(RoughcastRentalFetcher, "prospect", lambda _self, _listing_id: RoughcastProspectGallery(
        photos=(RoughcastProspectPhoto(
            url="https://img.ljcdn.com/inspection/example.jpg.1500x.jpg",
            label="客厅",
        ),),
    ))
    app = create_app(Settings(storage_dir=tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/v1/display/roughcast-rentals/RC-1/prospect")
        missing = client.get("/api/v1/display/roughcast-rentals/RC-2/prospect")

    assert response.status_code == 200
    assert response.json() == {"photos": [{
        "url": "https://img.ljcdn.com/inspection/example.jpg.1500x.jpg",
        "label": "客厅",
    }]}
    assert missing.status_code == 404
