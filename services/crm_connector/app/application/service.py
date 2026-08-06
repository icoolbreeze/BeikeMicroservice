from __future__ import annotations

from datetime import UTC, datetime

from app.domain.errors import (
    AuthenticationRequiredError,
    ConnectorDegradedError,
    NetworkRequiredError,
)
from app.domain.models import (
    ConnectionState,
    ConnectionStatus,
    Principal,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
)
from app.domain.modules import CrmModule, crm_modules
from app.domain.providers.crm_client import CrmClient
from app.domain.providers.session_provider import SessionProvider
from app.infrastructure.settings import Settings


class ConnectorService:
    """Coordinates CRM use cases without exposing authentication material."""

    def __init__(
        self, settings: Settings, session_provider: SessionProvider, crm_client: CrmClient
    ) -> None:
        self._settings = settings
        self._session_provider = session_provider
        self._crm_client = crm_client

    def connection_status(self) -> ConnectionStatus:
        provider_status = self._session_provider.status()
        return ConnectionStatus(
            state=provider_status.state,
            message=provider_status.message,
            bound_employee_principal=self._settings.bound_employee_principal or None,
            mcp_transport=self._settings.mcp_transport,
            checked_at=datetime.now(UTC),
        )

    def modules(self) -> tuple[CrmModule, ...]:
        return crm_modules()

    def whoami(self) -> Principal:
        self._require_ready()
        principal = self._crm_client.whoami()
        self._verify_bound_principal(principal)
        return principal

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        self._require_ready()
        return self._crm_client.search_rental_listings(filters)

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        self._require_ready()
        return self._crm_client.get_rental_listing_detail(listing_id)

    def _require_ready(self) -> None:
        status = self._session_provider.status()
        if status.state is ConnectionState.AUTH_REQUIRED:
            raise AuthenticationRequiredError(status.message)
        if status.state is ConnectionState.NETWORK_REQUIRED:
            raise NetworkRequiredError(status.message)
        if status.state is not ConnectionState.READY:
            raise ConnectorDegradedError(status.message)

    def _verify_bound_principal(self, principal: Principal) -> None:
        expected = self._settings.bound_employee_principal
        if expected and principal.employee_principal != expected:
            raise ConnectorDegradedError("CRM principal does not match this connector instance")
