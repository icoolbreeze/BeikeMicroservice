from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from app.infrastructure.workbench_open_client import WorkbenchOpenClient, WorkbenchOpenError


def test_client_rejects_bad_listing_id() -> None:
    client = WorkbenchOpenClient("http://127.0.0.1:1")
    with pytest.raises(WorkbenchOpenError) as exc:
        client.open_rental_listing("../evil")
    assert exc.value.status_code == 400


def test_client_maps_auth_required(monkeypatch) -> None:
    def fake_urlopen(request, timeout=0):
        raise HTTPError(
            request.full_url,
            401,
            "auth",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(json.dumps({
                "detail": {"code": "CRM_AUTH_REQUIRED", "message": "No active CRM credential"},
            }).encode("utf-8")),
        )

    monkeypatch.setattr(
        "app.infrastructure.workbench_open_client.urlopen", fake_urlopen
    )
    client = WorkbenchOpenClient("http://127.0.0.1:8020")
    with pytest.raises(WorkbenchOpenError) as exc:
        client.open_rental_listing("106100000001")
    assert exc.value.status_code == 401
    assert "扫码" in str(exc.value)


def test_client_maps_connection_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.workbench_open_client.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("down")),
    )
    client = WorkbenchOpenClient("http://127.0.0.1:1")
    with pytest.raises(WorkbenchOpenError) as exc:
        client.open_rental_listing("106100000001")
    assert exc.value.status_code == 503
    assert "无法连接" in str(exc.value)
