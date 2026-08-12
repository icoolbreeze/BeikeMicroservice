from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.schemas import (
    ConnectionStatusResponse,
    HealthResponse,
    ListingDetailInfoResponse,
    ListingProspectResponse,
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
    SaleCommunitySuggestionResponse,
    SaleFollowRecordResponse,
    SaleListingDetailResponse,
    SaleListingFilterOptionResponse,
    SaleListingPageResponse,
    SaleListingResponse,
    SaleListingSearchRequest,
    SaleMaintainInfoResponse,
    SaleMapNearbySearchRequest,
    SaleMapNearbySearchResponse,
    SaleMapSuggestionResponse,
)
from app.application.qr_login import QrLoginManager
from app.application.service import ConnectorService
from app.domain.errors import ConnectorError, UpstreamNotConfiguredError
from app.domain.models import ConnectionState
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
def health(
    svc: Annotated[ConnectorService, Depends(service)],
) -> HealthResponse:
    """Liveness + credential-readiness probe.

    HTTP 200 with ``status: "ok"`` means the process is alive; the
    credential fields report whether the stored CRM authorization still
    falls within its validity period.
    """
    status = svc.connection_status()
    return HealthResponse(
        status="ok",
        service="crm_connector",
        connection_state=status.state.value,
        credential_valid=status.state
        in (ConnectionState.READY, ConnectionState.EXPIRING),
        credential_expires_at=status.credential_expires_at,
        checked_at=status.checked_at,
    )


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


@router.get("/listings/rental/{listing_id}/prospect", response_model=ListingProspectResponse)
def get_rental_listing_prospect(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> ListingProspectResponse:
    """Detail-page 实勘 record: survey photos, floor plan, edit permission.

    ``has_survey_photo=false`` with an empty ``photos`` list is a valid
    answer — the house has not been surveyed yet.
    """
    return ListingProspectResponse.from_domain(
        invoke(lambda: svc.get_rental_listing_prospect(listing_id))
    )


@router.get("/listings/rental/{listing_id}/house-info", response_model=ListingDetailInfoResponse)
def get_rental_listing_house_info(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> ListingDetailInfoResponse:
    """Detail-page information beyond detailHead: labels, 小区/楼栋
    attributes, and the HQI quality score (``hqi`` is null when the house
    has no score record yet)."""
    return ListingDetailInfoResponse.from_domain(
        invoke(lambda: svc.get_rental_listing_house_info(listing_id))
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


# -- 买卖 (sale, house.link) ------------------------------------------------


@router.post("/listings/sale/search", response_model=SaleListingPageResponse)
def search_sale_listings(
    payload: SaleListingSearchRequest,
    svc: Annotated[ConnectorService, Depends(service)],
) -> SaleListingPageResponse:
    result = invoke(lambda: svc.search_sale_listings(payload.to_domain()))
    return SaleListingPageResponse.from_domain(result)


@router.get(
    "/listings/sale/filter-options",
    response_model=list[SaleListingFilterOptionResponse],
)
def sale_listing_filter_options(
    svc: Annotated[ConnectorService, Depends(service)],
) -> list[SaleListingFilterOptionResponse]:
    options = invoke(svc.sale_filter_options)
    return [SaleListingFilterOptionResponse.from_domain(option) for option in options]


@router.get(
    "/listings/sale/suggest",
    response_model=list[SaleCommunitySuggestionResponse],
)
def sale_community_suggest(
    query: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> list[SaleCommunitySuggestionResponse]:
    result = invoke(lambda: svc.sale_community_suggest(query))
    return [SaleCommunitySuggestionResponse.from_domain(item) for item in result]


@router.get("/listings/sale/{listing_id}", response_model=SaleListingResponse)
def get_sale_listing_detail(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> SaleListingResponse:
    return SaleListingResponse.from_domain(
        invoke(lambda: svc.get_sale_listing_detail(listing_id))
    )


@router.get("/listings/sale/{listing_id}/detail-head", response_model=SaleListingDetailResponse)
def get_sale_listing_detail_head(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> SaleListingDetailResponse:
    return SaleListingDetailResponse.from_domain(
        invoke(lambda: svc.get_sale_listing_detail_head(listing_id))
    )


@router.get(
    "/listings/sale/{listing_id}/maintain-info",
    response_model=SaleMaintainInfoResponse,
)
def get_sale_listing_maintain_info(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> SaleMaintainInfoResponse:
    return SaleMaintainInfoResponse.from_domain(
        invoke(lambda: svc.get_sale_listing_maintain_info(listing_id))
    )


@router.get(
    "/listings/sale/{listing_id}/follows",
    response_model=list[SaleFollowRecordResponse],
)
def get_sale_listing_follows(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> list[SaleFollowRecordResponse]:
    records = invoke(lambda: svc.get_sale_listing_follows(listing_id))
    return [SaleFollowRecordResponse.from_domain(record) for record in records]


@router.get("/listings/sale/map/suggest", response_model=list[SaleMapSuggestionResponse])
def sale_map_suggest(
    query: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> list[SaleMapSuggestionResponse]:
    result = invoke(lambda: svc.sale_map_suggest(query))
    return [SaleMapSuggestionResponse.from_domain(item) for item in result]


@router.post(
    "/listings/sale/map/nearby", response_model=SaleMapNearbySearchResponse
)
def search_sale_map_nearby(
    payload: SaleMapNearbySearchRequest,
    svc: Annotated[ConnectorService, Depends(service)],
) -> SaleMapNearbySearchResponse:
    result = invoke(lambda: svc.search_sale_map_nearby(payload.to_domain()))
    return SaleMapNearbySearchResponse.from_domain(result)
