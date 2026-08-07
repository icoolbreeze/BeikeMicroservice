from __future__ import annotations

from app.application.service import ConnectorService
from app.domain.errors import AuthenticationRequiredError, UpstreamNotConfiguredError
from app.domain.models import (
    ConnectionState,
    Principal,
    ProviderStatus,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
)
from app.infrastructure.settings import Settings
from app.infrastructure.unconfigured_crm_client import UnconfiguredCrmClient


class ReadySessionProvider:
    def status(self) -> ProviderStatus:
        return ProviderStatus(ConnectionState.READY, "CRM authorization is ready")

    def bound_principal(self) -> Principal | None:
        return Principal(employee_principal="employee-1")

    def authorized_fetch(self, request):  # pragma: no cover - exercised by future HTTP adapter
        raise AssertionError("Fake CRM client does not make HTTP requests")


class FakeCrmClient:
    def whoami(self) -> Principal:
        return Principal(employee_principal="employee-1", display_name="Test User")

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        return RentalListingPage(
            items=(
                RentalListing(
                    "listing-1", "Test Community", "2室1厅", 80.0, 2000.0, "南", filters.scope
                ),
            ),
            page=filters.page,
            page_size=filters.page_size,
            has_more=False,
            request_id="request-1",
        )

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        return RentalListing(
            listing_id, "Test Community", "2室1厅", 80.0, 2000.0, "南", "my_maintained"
        )


def filters() -> RentalListingFilters:
    return RentalListingFilters(
        community_keyword=None,
        listing_id=None,
        maintainer=None,
        scope="my_maintained",
        districts=(),
        monthly_rent_yuan=None,
        area_sqm=None,
        rooms=(),
        orientations=(),
        tags=(),
        page=1,
        page_size=20,
    )


def test_unconfigured_session_prevents_business_requests() -> None:
    service = ConnectorService(
        Settings(),
        session_provider=type(
            "MissingSession",
            (),
            {
                "status": lambda self: ProviderStatus(
                    ConnectionState.AUTH_REQUIRED, "sign in required"
                )
            },
        )(),
        crm_client=FakeCrmClient(),
    )

    try:
        service.search_rental_listings(filters())
    except AuthenticationRequiredError as exc:
        assert exc.code == "CRM_AUTH_REQUIRED"
    else:
        raise AssertionError("business request must not execute without an authorized session")


def test_ready_service_queries_through_crm_client() -> None:
    service = ConnectorService(
        Settings(bound_employee_principal="employee-1"), ReadySessionProvider(), FakeCrmClient()
    )

    assert service.whoami().employee_principal == "employee-1"
    assert service.search_rental_listings(filters()).items[0].listing_id == "listing-1"
    assert service.get_rental_listing_detail("listing-1").listing_id == "listing-1"


def test_ready_service_reports_unconfigured_upstream() -> None:
    service = ConnectorService(Settings(), ReadySessionProvider(), UnconfiguredCrmClient())

    try:
        service.search_rental_listings(filters())
    except UpstreamNotConfiguredError as exc:
        assert exc.code == "CRM_UPSTREAM_NOT_CONFIGURED"
    else:
        raise AssertionError("unconfigured upstream must not issue a network request")
