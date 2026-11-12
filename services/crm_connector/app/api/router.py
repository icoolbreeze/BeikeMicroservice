from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.schemas import (
    ArchiveListingPageResponse,
    ArchiveListingResponse,
    ArchiveStatusResponse,
    ArchiveTickResponse,
    SaleArchiveListingPageResponse,
    SaleArchiveListingResponse,
    SaleArchiveStatusResponse,
    SaleArchiveTickResponse,
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
    RentalListingRedirectResponse,
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
    TrusteeshipDealPageResponse,
    TrusteeshipDetailResponse,
    TrusteeshipListingPageResponse,
    TrusteeshipListingSearchRequest,
    WorkbenchOpenResponse,
)
from app.application.qr_login import QrLoginManager
from app.application.rental_archive import RentalArchiveRunner
from app.application.sale_archive import SaleArchiveRunner
from app.application.service import ConnectorService
from app.application.workbench_browser import WorkbenchBrowser
from app.domain.errors import ConnectorError, UpstreamNotConfiguredError
from app.domain.models import ConnectionState
from app.mcp.tools import tool_definitions

router = APIRouter(prefix="/api/v1")
ResultT = TypeVar("ResultT")


def service(request: Request) -> ConnectorService:
    return request.app.state.crm_connector_service


def workbench_opener(request: Request) -> WorkbenchBrowser:
    opener: WorkbenchBrowser | None = getattr(
        request.app.state, "crm_workbench_opener", None
    )
    if opener is None:
        raise HTTPException(
            status_code=UpstreamNotConfiguredError.status_code,
            detail={
                "code": UpstreamNotConfiguredError.code,
                "message": "workbench browser is not wired on this process",
            },
        )
    return opener


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


@router.get("/auth/login/current", response_model=QrLoginStatusResponse)
def get_current_qr_login(
    manager: Annotated[QrLoginManager, Depends(qr_login_manager)],
) -> QrLoginStatusResponse:
    """Snapshot of the currently pending login, or 404 when none is pending.

    Registered before ``/auth/login/{login_id}`` so ``current`` is matched
    as this route rather than captured as a login id.
    """
    pending = manager.current_pending()
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CRM_QR_LOGIN_NOT_FOUND",
                "message": "no QR login attempt is pending",
            },
        )
    return QrLoginStatusResponse.from_status(pending)


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
    include_follows: bool = Query(default=True),
) -> ListingDetailInfoResponse:
    """Detail-page information beyond detailHead: labels, 小区/楼栋
    attributes, and the HQI quality score (``hqi`` is null when the house
    has no score record yet)."""
    return ListingDetailInfoResponse.from_domain(
        invoke(
            lambda: svc.get_rental_listing_house_info(
                listing_id, include_follows=include_follows
            )
        )
    )


@router.post(
    "/workbench/rental/{listing_id}/open",
    response_model=WorkbenchOpenResponse,
)
def open_rental_workbench(
    listing_id: str,
    opener: Annotated[WorkbenchBrowser, Depends(workbench_opener)],
) -> WorkbenchOpenResponse:
    """Open the CRM rental detail page in a headed browser with injected cookies.

    The public ke.com page requires a consumer login. This endpoint only
    constructs ``lease-pz.link.lianjia.com/rent/house/detail/{id}`` from a
    validated listing id — it never accepts a caller-supplied URL.
    """
    url = invoke(lambda: opener.open_rental_listing(listing_id))
    return WorkbenchOpenResponse(listing_id=listing_id.strip(), url=url, opened=True)


@router.get("/listings/rental/{listing_id}/redirect", response_model=RentalListingRedirectResponse)
def get_rental_listing_redirect(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> RentalListingRedirectResponse:
    """Resolve a listing row's trusteeship ``cell_code``.

    Mirrors the 房源列表 page's click behavior: managed (del_type=5) rows
    resolve through getRedirectUrl to the trusteeship detail page, whose
    last URL segment is the cell_code used by the tuoguan detail tools.
    ``cell_code`` is None for rows the trusteeship domain does not serve.
    """
    return RentalListingRedirectResponse(
        listing_id=listing_id,
        cell_code=invoke(lambda: svc.get_rental_listing_redirect_url(listing_id)),
    )


@router.get(
    "/listings/rental/tuoguan/{cell_code}",
    response_model=TrusteeshipDetailResponse,
)
def get_trusteeship_detail(
    cell_code: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> TrusteeshipDetailResponse:
    """托管 (省心租) detail-page head via the trusteeship domain.

    Covers 托管 listings (del_type=5) that the 普租 detailHead cannot serve:
    实勘 photos, 户型图, VR, 费用项, and 成交参考.
    """
    return TrusteeshipDetailResponse.from_domain(
        invoke(lambda: svc.get_trusteeship_detail(cell_code))
    )


@router.get(
    "/listings/rental/tuoguan/{cell_code}/deals",
    response_model=TrusteeshipDealPageResponse,
)
def get_trusteeship_deals(
    svc: Annotated[ConnectorService, Depends(service)],
    cell_code: str,
    page: int = Query(default=1, ge=1, le=1000),
    page_size: int = Query(default=5, ge=1, le=20),
) -> TrusteeshipDealPageResponse:
    """托管 成交参考 (deal/list) — one page of historical deal records."""
    return TrusteeshipDealPageResponse.from_domain(
        invoke(lambda: svc.get_trusteeship_deals(cell_code, page=page, page_size=page_size))
    )


@router.post(
    "/listings/rental/tuoguan/search", response_model=TrusteeshipListingPageResponse
)
def search_trusteeship_listings(
    payload: TrusteeshipListingSearchRequest,
    svc: Annotated[ConnectorService, Depends(service)],
) -> TrusteeshipListingPageResponse:
    """托管 待出租 inventory (waitingrent) — rows carry working cell_code values."""
    return TrusteeshipListingPageResponse.from_domain(
        invoke(
            lambda: svc.search_trusteeship_listings(
                page=payload.page,
                page_size=payload.page_size,
                cell_code=payload.cell_code,
            )
        )
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


# --- rental listing localization (本地房源库) ------------------------------


def rental_archive(request: Request) -> RentalArchiveRunner:
    runner: RentalArchiveRunner | None = getattr(
        request.app.state, "crm_rental_archive_runner", None
    )
    if runner is None:
        raise HTTPException(
            status_code=UpstreamNotConfiguredError.status_code,
            detail={
                "code": UpstreamNotConfiguredError.code,
                "message": "rental archive is not wired on this process",
            },
        )
    return runner


@router.get("/archive/rental/status", response_model=ArchiveStatusResponse)
def rental_archive_status(
    runner: Annotated[RentalArchiveRunner, Depends(rental_archive)],
) -> ArchiveStatusResponse:
    """Report the archive cursor, today's budget and the last run.

    Answers 200 even while the archive is disabled — a disabled daemon is a
    normal state, not an error.
    """
    return ArchiveStatusResponse.from_domain(runner.status())


@router.post("/archive/rental/tick", response_model=ArchiveTickResponse)
def rental_archive_tick(
    runner: Annotated[RentalArchiveRunner, Depends(rental_archive)],
) -> ArchiveTickResponse:
    """Run one unit of work (one list page, or one listing's details)."""
    return ArchiveTickResponse.from_domain(invoke(runner.run_tick))


@router.get("/archive/rental/listings", response_model=ArchiveListingPageResponse)
def rental_archive_listings(
    runner: Annotated[RentalArchiveRunner, Depends(rental_archive)],
    district_id: str | None = Query(default=None),
    active: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> ArchiveListingPageResponse:
    items, total = runner.store.list_listings(
        district_id=district_id, active=active, page=page, page_size=page_size
    )
    return ArchiveListingPageResponse.from_domain(
        items, page=page, page_size=page_size, total=total
    )


@router.get(
    "/archive/rental/listings/{listing_id}", response_model=ArchiveListingResponse
)
def rental_archive_listing(
    listing_id: str,
    runner: Annotated[RentalArchiveRunner, Depends(rental_archive)],
) -> ArchiveListingResponse:
    listing = runner.store.get_listing(listing_id)
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CRM_ARCHIVE_LISTING_NOT_FOUND",
                "message": f"listing {listing_id} is not localized yet",
            },
        )
    return ArchiveListingResponse.from_domain(listing)


# --- 买卖 listing localization (same 14 districts as rental) --------------


def sale_archive(request: Request) -> SaleArchiveRunner:
    runner: SaleArchiveRunner | None = getattr(
        request.app.state, "crm_sale_archive_runner", None
    )
    if runner is None:
        raise HTTPException(
            status_code=UpstreamNotConfiguredError.status_code,
            detail={
                "code": UpstreamNotConfiguredError.code,
                "message": "sale archive is not wired on this process",
            },
        )
    return runner


@router.get("/archive/sale/status", response_model=SaleArchiveStatusResponse)
def sale_archive_status(
    runner: Annotated[SaleArchiveRunner, Depends(sale_archive)],
) -> SaleArchiveStatusResponse:
    """Report the sale archive cursor, today's budget and the last run.

    Answers 200 even while the archive is disabled — a disabled daemon is a
    normal state, not an error.
    """
    return SaleArchiveStatusResponse.from_domain(runner.status())


@router.post("/archive/sale/tick", response_model=SaleArchiveTickResponse)
def sale_archive_tick(
    runner: Annotated[SaleArchiveRunner, Depends(sale_archive)],
) -> SaleArchiveTickResponse:
    """Run one unit of work (one list page, or one listing's details)."""
    return SaleArchiveTickResponse.from_domain(invoke(runner.run_tick))


@router.get("/archive/sale/listings", response_model=SaleArchiveListingPageResponse)
def sale_archive_listings(
    runner: Annotated[SaleArchiveRunner, Depends(sale_archive)],
    district_id: str | None = Query(default=None),
    active: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> SaleArchiveListingPageResponse:
    items, total = runner.store.list_listings(
        district_id=district_id, active=active, page=page, page_size=page_size
    )
    return SaleArchiveListingPageResponse.from_domain(
        items, page=page, page_size=page_size, total=total
    )


@router.get(
    "/archive/sale/listings/{listing_id}",
    response_model=SaleArchiveListingResponse,
)
def sale_archive_listing(
    listing_id: str,
    runner: Annotated[SaleArchiveRunner, Depends(sale_archive)],
) -> SaleArchiveListingResponse:
    listing = runner.store.get_listing(listing_id)
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CRM_ARCHIVE_LISTING_NOT_FOUND",
                "message": f"listing {listing_id} is not localized yet",
            },
        )
    return SaleArchiveListingResponse.from_domain(listing)
