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
