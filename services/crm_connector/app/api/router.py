from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas import (
    ConnectionStatusResponse,
    HealthResponse,
    ModuleResponse,
    PrincipalResponse,
    RentalListingResponse,
    RentalListingPageResponse,
    RentalListingSearchRequest,
)
from app.application.service import ConnectorService
from app.domain.errors import ConnectorError
from app.mcp.tools import tool_definitions

router = APIRouter(prefix="/api/v1")
ResultT = TypeVar("ResultT")


def service(request: Request) -> ConnectorService:
    return request.app.state.crm_connector_service


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


@router.get("/listings/rental/{listing_id}", response_model=RentalListingResponse)
def get_rental_listing_detail(
    listing_id: str,
    svc: Annotated[ConnectorService, Depends(service)],
) -> RentalListingResponse:
    return RentalListingResponse.from_domain(
        invoke(lambda: svc.get_rental_listing_detail(listing_id))
    )
