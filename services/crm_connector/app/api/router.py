from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.schemas import (
    ConnectionStatusResponse,
    HealthResponse,
    ModuleResponse,
    PrincipalResponse,
    QrLoginStartResponse,
    QrLoginStatusResponse,
    RentalListingResponse,
    RentalListingPageResponse,
    RentalListingFilterOptionResponse,
    RentalListingSearchRequest,
    RentalMapNearbySearchRequest,
    RentalMapNearbySearchResponse,
    RentalMapSuggestionRequest,
    RentalMapSuggestionResponse,
)
from app.application.qr_login import QrLoginManager
from app.application.service import ConnectorService
from app.domain.errors import ConnectorError, UpstreamNotConfiguredError
from app.mcp.tools import tool_definitions

router = APIRouter(prefix="/api/v1")
ResultT = TypeVar("ResultT")


def service(request: Request) -> ConnectorService:
    return request.app.state.crm_connector_service


def qr_login_manager(request: Request) -> QrLoginManager:
    manager: QrLoginManager | None = request.app.state.crm_qr_login_manager
    if manager is None:
        raise HTTPException(
            status_code=UpstreamNotConfiguredError.status_code,
            detail={
                "code": UpstreamNotConfiguredError.code,
                "message": (
                    "QR login requires a configured CRM profile; set "
                    "CC_UPSTREAM_PROFILE to a real profile and restart"
                ),
            },
        )
    return manager


def invoke(call: Callable[[], ResultT]) -> ResultT:
    try:
        return call()
    except ConnectorError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="crm_connector")


@router.get("/connection/status", response_model=ConnectionStatusResponse)
def connection_status(
    svc: Annotated[ConnectorService, Depends(service)],
) -> ConnectionStatusResponse:
    return ConnectionStatusResponse.from_domain(svc.connection_status())


@router.post("/auth/login", response_model=QrLoginStartResponse)
def start_qr_login(
    manager: Annotated[QrLoginManager, Depends(qr_login_manager)],
) -> QrLoginStartResponse:
    status = invoke(manager.start)
    return QrLoginStartResponse(
        login_id=status.login_id,
        state=status.state,
        qrcode=status.qrcode,
        note=status.note,
        message=status.message,
    )


@router.get("/auth/login/{login_id}", response_model=QrLoginStatusResponse)
def get_qr_login(
    login_id: str,
    manager: Annotated[QrLoginManager, Depends(qr_login_manager)],
) -> QrLoginStatusResponse:
    return QrLoginStatusResponse.from_status(invoke(lambda: manager.status(login_id)))


@router.get("/auth/login/{login_id}/qrcode.png")
def get_qr_login_qrcode(
    login_id: str,
    manager: Annotated[QrLoginManager, Depends(qr_login_manager)],
) -> Response:
    png = invoke(lambda: manager.qrcode_png(login_id))
    return Response(content=png, media_type="image/png")


@router.post("/auth/login/{login_id}/cancel", response_model=QrLoginStatusResponse)
def cancel_qr_login(
    login_id: str,
    manager: Annotated[QrLoginManager, Depends(qr_login_manager)],
) -> QrLoginStatusResponse:
    return QrLoginStatusResponse.from_status(invoke(lambda: manager.cancel(login_id)))


@router.get("/modules", response_model=list[ModuleResponse])
def modules(svc: Annotated[ConnectorService, Depends(service)]) -> list[ModuleResponse]:
    return [ModuleResponse.from_domain(module) for module in svc.modules()]


@router.get("/mcp/tools")
def mcp_tools() -> list[dict[str, object]]:
    """Expose tool metadata for diagnostics until a protocol transport is wired."""
    return [tool.as_dict() for tool in tool_definitions()]


@router.get("/crm/me", response_model=PrincipalResponse)
def crm_me(svc: Annotated[ConnectorService, Depends(service)]) -> PrincipalResponse:
    return PrincipalResponse.from_domain(invoke(svc.whoami))


@router.post("/listings/rental/search", response_model=RentalListingPageResponse)
def search_rental_listings(
    payload: RentalListingSearchRequest,
    svc: Annotated[ConnectorService, Depends(service)],
) -> RentalListingPageResponse:
    result = invoke(lambda: svc.search_rental_listings(payload.to_domain()))
    return RentalListingPageResponse.from_domain(result)


@router.get(
    "/listings/rental/filter-options",
    response_model=list[RentalListingFilterOptionResponse],
)
def rental_listing_filter_options(
    svc: Annotated[ConnectorService, Depends(service)],
) -> list[RentalListingFilterOptionResponse]:
    options = invoke(svc.rental_listing_filter_options)
    return [RentalListingFilterOptionResponse.from_domain(option) for option in options]


@router.get("/listings/rental/{listing_id}", response_model=RentalListingResponse)
def get_rental_listing_detail(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> RentalListingResponse:
    return RentalListingResponse.from_domain(
        invoke(lambda: svc.get_rental_listing_detail(listing_id))
    )


@router.post("/listings/rental/map/suggest", response_model=list[RentalMapSuggestionResponse])
def rental_map_suggest(
    payload: RentalMapSuggestionRequest,
    svc: Annotated[ConnectorService, Depends(service)],
) -> list[RentalMapSuggestionResponse]:
    result = invoke(
        lambda: svc.rental_map_suggest(payload.to_domain(svc.default_city_id))
    )
    return [RentalMapSuggestionResponse.from_domain(item) for item in result]


@router.post(
    "/listings/rental/map/nearby", response_model=RentalMapNearbySearchResponse
)
def search_rental_map_nearby(
    payload: RentalMapNearbySearchRequest,
    svc: Annotated[ConnectorService, Depends(service)],
) -> RentalMapNearbySearchResponse:
    result = invoke(
        lambda: svc.search_rental_map_nearby(payload.to_domain(svc.default_city_id))
    )
    return RentalMapNearbySearchResponse.from_domain(result)
