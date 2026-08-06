from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.models import Principal
from app.domain.providers.credential_store import ActiveCredential


@dataclass(frozen=True)
class BootstrapResult:
    """Opaque authentication material produced by a deployment-specific bootstrap flow."""

    credential_material: bytes
    expires_at: datetime | None
    credential_version: int
    refresh_material: bytes | None = None
    """ke.com SSO renewal material for ``refresh()``; None when the bootstrap
    flow did not obtain it (e.g. proxy-only environment without TGC)."""


class CredentialBootstrapProvider(Protocol):
    """Acquires and renews session material without coupling the rest of the service to its source."""

    def bootstrap(self) -> BootstrapResult:
        """Begin or complete an authorization bootstrap and return usable material."""

    def refresh(self, current: ActiveCredential) -> BootstrapResult | None:
        """Return replacement material when the provider can renew the active credential."""

    def validate(self, credential_material: bytes) -> Principal:
        """Resolve the CRM principal associated with the material before persistence."""

    def revoke(self, current: ActiveCredential) -> None:
        """Notify the provider that the locally active material is no longer valid."""
