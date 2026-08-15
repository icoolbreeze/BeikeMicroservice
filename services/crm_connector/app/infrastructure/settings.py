from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bound_employee_principal: str = ""
    mcp_transport: str = "stdio"
    upstream_profile: str = "kecom-prod"
    request_timeout_seconds: float = 15.0
    cors_origins: tuple[str, ...] = ()
    mcp_rate_limit_per_min: int = 30

    crm_login_base: str = "https://login.ke.com"
    crm_service_url: str = "https://lease-pz.link.lianjia.com/login?gotoURL=%252F"
    crm_business_origin: str = "https://lease-pz.link.lianjia.com"
    crm_map_origin: str = "https://map.ke.com"
    crm_house_origin: str = "https://house.link.lianjia.com"
    crm_trusteeship_origin: str = "https://trusteeship.link.lianjia.com"
    crm_default_city_code: str = "510100"
    credential_store_path: str = "./run/credential_store.bin"
    authd_listen_address: str = "127.0.0.1:8021"
    qr_login_auto_start: bool = True
    bootstrap_poll_interval_seconds: float = 3.0
    bootstrap_poll_timeout_seconds: float = 300.0
    bootstrap_qrcode_refresh_initial_delay_seconds: float = 1.0
    refresh_keepalive_interval_seconds: float = 1500.0
    # Service-mode session watchdog: keeps the uvicorn process probing and
    # re-triggering QR login when credentials go stale mid-run (the
    # KeepaliveTimer in crm-authd serve does not exist in this process).
    session_watchdog_enabled: bool = True
    session_watchdog_check_interval_seconds: float = 60.0
    featured_push_enabled: bool = False
    featured_push_interval_seconds: float = 3600.0
    featured_push_local_api_base_url: str = "http://127.0.0.1:8020"
    featured_push_remote_host: str = "beike-server"
    featured_push_remote_path: str = "/var/lib/store-media/featured_snapshot.json"


def load_settings() -> Settings:
    cors_raw = os.getenv("CC_CORS_ORIGINS", "")
    store_path = os.getenv("CC_CREDENTIAL_STORE_PATH", "").strip()
    return Settings(
        bound_employee_principal=os.getenv("CC_BOUND_EMPLOYEE_PRINCIPAL", "").strip(),
        mcp_transport=os.getenv("CC_MCP_TRANSPORT", "stdio").strip() or "stdio",
        upstream_profile=os.getenv("CC_UPSTREAM_PROFILE", "kecom-prod").strip() or "kecom-prod",
        request_timeout_seconds=float(os.getenv("CC_REQUEST_TIMEOUT_SECONDS", "15")),
        cors_origins=tuple(
            origin.strip()
            for origin in cors_raw.split(",")
            if origin.strip()
        ),
        mcp_rate_limit_per_min=int(os.getenv("CC_MCP_RATE_LIMIT_PER_MIN", "30")),
        crm_login_base=os.getenv("CC_CRM_LOGIN_BASE", "https://login.ke.com").strip()
        or "https://login.ke.com",
        crm_service_url=os.getenv("CC_CRM_SERVICE_URL", "").strip()
        or "https://lease-pz.link.lianjia.com/login?gotoURL=%252F",
        crm_business_origin=os.getenv("CC_CRM_BUSINESS_ORIGIN", "").strip()
        or "https://lease-pz.link.lianjia.com",
        crm_map_origin=os.getenv("CC_CRM_MAP_ORIGIN", "").strip()
        or "https://map.ke.com",
        crm_house_origin=os.getenv("CC_CRM_HOUSE_ORIGIN", "").strip()
        or "https://house.link.lianjia.com",
        crm_trusteeship_origin=os.getenv("CC_CRM_TRUSTEESHIP_ORIGIN", "").strip()
        or "https://trusteeship.link.lianjia.com",
        crm_default_city_code=os.getenv("CC_CRM_DEFAULT_CITY_CODE", "510100").strip()
        or "510100",
        credential_store_path=store_path
        or str(Path("./run/credential_store.bin").resolve()),
        authd_listen_address=os.getenv("CC_AUTHD_LISTEN_ADDRESS", "127.0.0.1:8021").strip()
        or "127.0.0.1:8021",
        qr_login_auto_start=os.getenv("CC_QR_LOGIN_AUTO_START", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        bootstrap_poll_interval_seconds=float(
            os.getenv("CC_BOOTSTRAP_POLL_INTERVAL_SECONDS", "3")
        ),
        bootstrap_poll_timeout_seconds=float(
            os.getenv("CC_BOOTSTRAP_POLL_TIMEOUT_SECONDS", "300")
        ),
        bootstrap_qrcode_refresh_initial_delay_seconds=float(
            os.getenv("CC_BOOTSTRAP_QRCODE_REFRESH_INITIAL_DELAY_SECONDS", "1")
        ),
        refresh_keepalive_interval_seconds=float(
            os.getenv("CC_KEEPALIVE_INTERVAL_SECONDS", "1500")
        ),
        session_watchdog_enabled=os.getenv("CC_SESSION_WATCHDOG_ENABLED", "1").strip()
        .lower() not in ("0", "false", "no", "off"),
        session_watchdog_check_interval_seconds=float(
            os.getenv("CC_SESSION_WATCHDOG_CHECK_INTERVAL_SECONDS", "60")
        ),
        featured_push_enabled=os.getenv("CC_FEATURED_PUSH_ENABLED", "0").strip()
        .lower() in ("1", "true", "yes", "on"),
        featured_push_interval_seconds=max(
            60.0, float(os.getenv("CC_FEATURED_PUSH_INTERVAL_SECONDS", "3600"))
        ),
        featured_push_local_api_base_url=os.getenv(
            "CC_FEATURED_PUSH_LOCAL_API_BASE_URL", "http://127.0.0.1:8020"
        ).strip() or "http://127.0.0.1:8020",
        featured_push_remote_host=os.getenv(
            "CC_FEATURED_PUSH_REMOTE_HOST", "beike-server"
        ).strip() or "beike-server",
        featured_push_remote_path=os.getenv(
            "CC_FEATURED_PUSH_REMOTE_PATH",
            "/var/lib/store-media/featured_snapshot.json",
        ).strip() or "/var/lib/store-media/featured_snapshot.json",
    )
