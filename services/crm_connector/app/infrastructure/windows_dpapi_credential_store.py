from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.domain.providers.credential_store import ActiveCredential

logger = logging.getLogger(__name__)

# When True we never persist material — used by unit tests and CI runners that
# do not have access to DPAPI. The provider still acts as a CredentialStore,
# but its slots live in-memory only. Settings pick this up via
# ``CC_CREDENTIAL_STORE_IN_MEMORY``.
_IN_MEMORY_DEFAULT = False


class WindowsDpapiCredentialStore:
    """CredentialStore backed by Windows DPAPI for at-rest encryption.

    Two modes:

    - **Windows host (production shape)**: each ``save()`` serializes the
      ActiveCredential to JSON, encrypts the blob with
      ``CryptProtectData`` (current-user scope), and atomically replaces
      the on-disk file at ``settings.credential_store_path``. ``load_active``
      reads the file, decrypts, and returns the struct.
    - **Non-Windows or ``CC_CREDENTIAL_STORE_IN_MEMORY=1``**: used for
      developer machines and CI. The blob is written as plain JSON to a
      0600 file and a warning is logged on every save; or, when in-memory
      mode is on, kept only in a process-local slot.

    The store never logs or persists credentials in plaintext when DPAPI is
    available. ``logging`` is intentionally coarse — only save/load/invalidate
    counts and reason codes.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        in_memory: bool | None = None,
    ) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._in_memory_mode = _resolve_in_memory(in_memory) or path is None

        # Process-local slot. When in-memory mode is active we keep the
        # latest ActiveCredential here; when persisted mode is active we
        # fall back to this only as a write-through cache after a successful
        # save/load, so callers see the same in-memory identity.
        self._slot: ActiveCredential | None = None

        # Invalidation history for ``clear_expired`` introspection.
        self._invalidated: list[tuple[str, str, datetime]] = []

        if not self._in_memory_mode and self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not _is_windows():
                logger.warning(
                    "credential_store.non_windows_host path=%s material will be written"
                    " as plaintext JSON; only enable this configuration on developer hosts.",
                    self._path,
                )

    # -- CredentialStore ----------------------------------------------------

    def save(self, credential: ActiveCredential) -> None:
        with self._lock:
            previous = self._slot
            self._slot = credential
            if previous is not None and previous.session_id != credential.session_id:
                self._invalidated.append(
                    (previous.session_id, "replaced", _now())
                )
            if self._in_memory_mode or self._path is None:
                return
            blob = _serialize(credential)
            encrypted = self._protect(blob)
            _atomic_write(self._path, encrypted)
            self._restrict_file_mode(self._path)

    def load_active(self) -> ActiveCredential | None:
        with self._lock:
            if self._slot is not None:
                return self._slot
            if self._in_memory_mode or self._path is None:
                return None
            try:
                raw = self._path.read_bytes()
            except FileNotFoundError:
                return None
            except OSError:
                logger.warning("credential_store.load.read_failed path=%s", self._path)
                return None
            decrypted = self._unprotect(raw)
            try:
                self._slot = _deserialize(decrypted)
            except ValueError:
                logger.warning("credential_store.load.deserialize_failed")
                return None
            return self._slot

    def invalidate(
        self,
        session_id: str,
        reason: Literal["expired", "upstream_rejected", "replaced"],
    ) -> None:
        with self._lock:
            if self._slot is None or self._slot.session_id != session_id:
                self._invalidated.append((session_id, reason, _now()))
                return
            self._invalidated.append((session_id, reason, _now()))
            self._slot = None
            if not self._in_memory_mode and self._path is not None and self._path.exists():
                try:
                    self._path.unlink()
                except OSError:
                    logger.warning("credential_store.invalidate.unlink_failed")

    def clear_expired(self, now: datetime) -> int:
        with self._lock:
            if self._slot is None:
                return 0
            expires_at = self._slot.expires_at
            if expires_at is None or expires_at > now:
                return 0
            self._invalidated.append((self._slot.session_id, "expired", _now()))
            self._slot = None
            if not self._in_memory_mode and self._path is not None and self._path.exists():
                try:
                    self._path.unlink()
                except OSError:
                    logger.warning("credential_store.clear.unlink_failed")
            return 1

    # -- internals ---------------------------------------------------------

    def _protect(self, blob: bytes) -> bytes:
        if _is_windows():
            return _dpapi_protect(blob)
        return blob

    def _unprotect(self, blob: bytes) -> bytes:
        if _is_windows():
            return _dpapi_unprotect(blob)
        return blob

    @staticmethod
    def _restrict_file_mode(path: Path) -> None:
        if _is_windows():
            # On Windows DPAPI is the confidentiality mechanism; ACL work is
            # out of scope for this layer.
            return
        try:
            os.chmod(path, 0o600)
        except OSError:
            logger.warning("credential_store.chmod_failed path=%s", path)


# -- serialization -------------------------------------------------------


def _serialize(credential: ActiveCredential) -> bytes:
    return json.dumps(
        {
            "session_id": credential.session_id,
            "employee_principal": credential.employee_principal,
            "credential_material_b64": _b64(credential.credential_material),
            "refresh_material_b64": _b64(credential.refresh_material),
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "credential_version": credential.credential_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _deserialize(blob: bytes) -> ActiveCredential:
    data: dict[str, Any] = json.loads(blob.decode("utf-8"))
    expires_at_raw = data.get("expires_at")
    expires_at = (
        datetime.fromisoformat(expires_at_raw) if isinstance(expires_at_raw, str) else None
    )
    return ActiveCredential(
        session_id=str(data["session_id"]),
        employee_principal=str(data["employee_principal"]),
        credential_material=_unb64(data.get("credential_material_b64", "")),
        refresh_material=(_unb64(data["refresh_material_b64"]) if isinstance(data.get("refresh_material_b64"), str) else None),
        expires_at=expires_at,
        credential_version=int(data.get("credential_version", 0)),
    )


def _b64(data: bytes | None) -> str:
    import base64

    return base64.b64encode(data or b"").decode("ascii")


def _unb64(text: str) -> bytes:
    import base64

    return base64.b64decode(text.encode("ascii"))


def _atomic_write(path: Path, data: bytes) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except Exception:
        # mksttemp fd ownership handled by context manager; ensure cleanup.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _dpapi_protect(blob: bytes) -> bytes:
    import win32crypt  # type: ignore[import-untyped]

    return win32crypt.CryptProtectData(blob, "crm-connector credential store", None, None, None, 0)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt  # type: ignore[import-untyped]

    description, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def _is_windows() -> bool:
    return sys.platform == "win32"


def _resolve_in_memory(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    env = os.getenv("CC_CREDENTIAL_STORE_IN_MEMORY", "").strip()
    return env in ("1", "true", "yes")


def _now() -> datetime:
    return datetime.now(UTC)


def _system_default_path() -> str:
    # Kept for compatibility with old callers; not used by Settings.
    return str(Path(tempfile.gettempdir()) / "crm-connector-credential.bin")


_DEFAULT_PATH = _system_default_path()


# The protocol-shape required by the domain layer. We deliberately keep this as
# a runtime check rather than a nominal Protocol because the domain already
# declares one and we want to depend on duck typing here.
_STORE_PROTOCOL = "save", "load_active", "invalidate", "clear_expired"


def assert_implements_protocol() -> None:  # pragma: no cover - guard for type hints
    """Sanity-check that ``WindowsDpapiCredentialStore`` satisfies the
    ``CredentialStore`` Protocol at import time of unit tests.

    This helper does not perform runtime runtime checks against an instance;
    it only verifies that all required methods are declared on the class. It
    is the cheapest way to bridge the gap between the duck-typed Protocol and
    the static type system without importing the Protocol at module scope.
    """
    missing = [name for name in _STORE_PROTOCOL if not callable(getattr(WindowsDpapiCredentialStore, name, None))]
    if missing:
        raise TypeError(
            f"WindowsDpapiCredentialStore is missing CredentialStore members: {missing}"
        )
