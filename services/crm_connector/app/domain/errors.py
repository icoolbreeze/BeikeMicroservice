from __future__ import annotations


class ConnectorError(RuntimeError):
    code = "CRM_CONNECTOR_ERROR"
    status_code = 503


class AuthenticationRequiredError(ConnectorError):
    code = "CRM_AUTH_REQUIRED"
    status_code = 401


class NetworkRequiredError(ConnectorError):
    code = "CRM_NETWORK_REQUIRED"
    status_code = 503


class ConnectorDegradedError(ConnectorError):
    code = "CRM_CONNECTOR_DEGRADED"
    status_code = 503


class UpstreamNotConfiguredError(ConnectorError):
    code = "CRM_UPSTREAM_NOT_CONFIGURED"
    status_code = 501


class UpstreamInvalidInputError(ConnectorError):
    """Upstream rejected the request payload (business code 100001 etc.)."""

    code = "CRM_UPSTREAM_INVALID_INPUT"
    status_code = 400


class UpstreamChangedError(ConnectorError):
    """Upstream returned a status/shape we do not recognise (contract drift)."""

    code = "CRM_UPSTREAM_CHANGED"
    status_code = 502


class QrLoginError(ConnectorError):
    """A QR-code login session could not be started or polled as requested."""

    code = "CRM_QR_LOGIN_ERROR"
    status_code = 400


class QrLoginConflictError(QrLoginError):
    """A QR login cannot start right now (already ready or already in progress)."""

    code = "CRM_QR_LOGIN_CONFLICT"
    status_code = 409


class QrLoginNotFoundError(QrLoginError):
    """The referenced QR login session does not exist (or was pruned)."""

    code = "CRM_QR_LOGIN_NOT_FOUND"
    status_code = 404
