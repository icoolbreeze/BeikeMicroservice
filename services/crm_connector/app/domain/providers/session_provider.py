from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from app.domain.models import ProviderStatus


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

    def authorized_fetch(self, request: AuthorizedRequest) -> UpstreamResponse:
        """Send an allow-listed request without disclosing authentication material."""
