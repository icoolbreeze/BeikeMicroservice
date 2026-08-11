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
        assert "saleProperties" in response.text
        assert "DINGFENG" in response.text
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
