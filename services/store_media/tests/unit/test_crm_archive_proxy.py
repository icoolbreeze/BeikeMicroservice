from unittest.mock import patch

from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def test_archive_status_timeout_is_504(tmp_path) -> None:
    app = create_app(Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:1"))
    with patch("app.main.urlopen", side_effect=TimeoutError("timed out")):
        with TestClient(app) as client:
            response = client.get("/api/v1/archive/rental/status")
    assert response.status_code == 504
    assert response.text == "crm connector timeout"

def test_new_dashboard_proxy_routes_and_query_whitelist(tmp_path) -> None:
    import io
    import json
    calls = []
    class Reply(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}
    def upstream(req, timeout):
        calls.append((req.full_url, timeout))
        return Reply(json.dumps({"available": True}).encode())
    app = create_app(Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:8020"))
    with patch("app.main.urlopen", side_effect=upstream), TestClient(app) as client:
        for path in ("/api/v1/archive/rental/dashboard", "/api/v1/archive/sale/dashboard",
                     "/api/v1/archive/vision/status"):
            response = client.get(path)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        response = client.get("/api/v1/archive/rental/issues?status=failed&page=2&page_size=20&secret=discard")
        assert response.status_code == 200
        assert "page=2" in calls[-1][0] and "secret" not in calls[-1][0]
        assert all(timeout == 10 for _, timeout in calls)
        assert client.get("/api/v1/archive/unknown/dashboard").status_code == 404


def test_new_dashboard_timeout_is_504(tmp_path) -> None:
    app = create_app(Settings(storage_dir=tmp_path, crm_connector_base_url="http://127.0.0.1:8020"))
    with patch("app.main.urlopen", side_effect=TimeoutError("timed out")), TestClient(app) as client:
        response = client.get("/api/v1/archive/vision/status")
    assert response.status_code == 504
