from __future__ import annotations

from app.domain.models import ConnectionState, Principal, ProviderStatus
from app.domain.errors import AuthenticationRequiredError
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse


class UnconfiguredSessionProvider:
    """Safe default that prevents accidental calls before an auth boundary exists."""

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            state=ConnectionState.AUTH_REQUIRED,
            message="CRM session provider has not been configured",
        )

    def bound_principal(self) -> Principal | None:
        return None

    def authorized_fetch(self, request: AuthorizedRequest) -> UpstreamResponse:
        raise AuthenticationRequiredError("CRM session provider has not been configured")
