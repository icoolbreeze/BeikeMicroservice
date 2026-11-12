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
