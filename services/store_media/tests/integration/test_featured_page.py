from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def test_featured_page_is_served(tmp_path) -> None:
    app = create_app(Settings(
        storage_dir=tmp_path,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
    ))
    with TestClient(app) as client:
        response = client.get("/featured.html")
        assert response.status_code == 200
        assert "fallbackSaleProperties" in response.text
        assert "loadFeatured" in response.text
        assert "const carouselSale" not in response.text
        assert "allProperties = rent" in response.text
        assert ".filter(p => p.original_image)" in response.text
        assert "image: p.original_image" in response.text
        assert "updateMarketBrief" in response.text
        assert "originalMarketCount" in response.text
        assert "communityMarketCount" in response.text
        assert "COMMUNITY" in response.text
        assert "https://" not in response.text
        assert "http://" not in response.text
        for asset in (
            "/vendor/tailwind/tailwind.js",
            "/vendor/fonts/fonts.css",
            "/vendor/fontawesome/css/all.min.css",
            "/featured/images/luxe-sale-01.jpg",
            "/featured/images/luxe-rent-06.jpg",
        ):
            assert client.get(asset).status_code == 200
