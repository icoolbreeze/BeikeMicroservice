from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from app.domain.models import Principal, ProviderStatus


@dataclass(frozen=True)
class AuthorizedRequest:
    """A route-safe request the auth boundary may send on behalf of the connector."""

    route: str
    method: str
    query: Mapping[str, str | int | float | bool | None]
    body: object | None
    request_id: str


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    body: object


class SessionProvider(Protocol):
    """Authentication boundary; implementations must not disclose raw credentials."""

    def status(self) -> ProviderStatus:
        """Return the current authorization/network state."""

    def bound_principal(self) -> Principal | None:
        """Return the employee principal this session was bootstrapped with.

        ``None`` means the identity is not known locally and must be
        discovered from the upstream, if it exposes one.
        """

    def authorized_fetch(self, request: AuthorizedRequest) -> UpstreamResponse:
        """Send an allow-listed request without disclosing authentication material."""

    def run_keepalive(self) -> None:
        """Probe identity, extend the ssid window, and refresh via TGC on failure."""
