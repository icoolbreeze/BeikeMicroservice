from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def test_health_status_and_tool_catalog_are_available() -> None:
    app = create_app(Settings(bound_employee_principal="employee-1"))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "service": "crm_connector"}

        status = client.get("/api/v1/connection/status")
        assert status.status_code == 200
        assert status.json()["state"] == "auth_required"
        assert status.json()["bound_employee_principal"] == "employee-1"

        modules = client.get("/api/v1/modules")
        assert modules.status_code == 200
        rental = next(
            module for module in modules.json() if module["module_id"] == "property.rental"
        )
        assert rental["status"] == "implemented"
        sale = next(module for module in modules.json() if module["module_id"] == "property.sale")
        assert sale["status"] == "reserved"

        tools = client.get("/api/v1/mcp/tools")
        assert tools.status_code == 200
        assert [tool["name"] for tool in tools.json()] == [
            "crm_connection_status",
            "crm_whoami",
            "rental_listing_search",
            "rental_listing_get_detail",
        ]


def test_unconfigured_business_requests_return_structured_errors() -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        me = client.get("/api/v1/crm/me")
        assert me.status_code == 401
        assert me.json()["detail"]["code"] == "CRM_AUTH_REQUIRED"

        listings = client.post("/api/v1/listings/rental/search", json={})
        assert listings.status_code == 401
        assert listings.json()["detail"]["code"] == "CRM_AUTH_REQUIRED"

        detail = client.get("/api/v1/listings/rental/listing-1")
        assert detail.status_code == 401
        assert detail.json()["detail"]["code"] == "CRM_AUTH_REQUIRED"
