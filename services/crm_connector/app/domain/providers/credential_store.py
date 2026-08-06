from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class ActiveCredential:
    """Opaque credential material plus the minimum metadata needed for lifecycle control."""

    session_id: str
    employee_principal: str
    credential_material: bytes
    expires_at: datetime | None
    credential_version: int
    refresh_material: bytes | None = None
    """ke.com SSO renewal material (TGC + TGC_Secure + security_ticket + login_ucid).

    Persisted alongside the business cookie jar but never handed to the
    SessionProvider; only ``crm-authd`` reads it for ``refresh()``. The
    material stays opaque: the bytes are produced by the bootstrap adapter
    and consumed by the same adapter's refresh path."""


class CredentialStore(Protocol):
    """Protected persistence boundary owned by the authorization process only."""

    def save(self, credential: ActiveCredential) -> None:
        """Atomically persist the credential and replace any active predecessor."""

    def load_active(self) -> ActiveCredential | None:
        """Load the active credential for a locally bound connector instance."""

    def invalidate(
        self,
        session_id: str,
        reason: Literal["expired", "upstream_rejected", "replaced"],
    ) -> None:
        """Prevent further use of a known-invalid credential."""

    def clear_expired(self, now: datetime) -> int:
        """Remove expired credential records and return the number removed."""
