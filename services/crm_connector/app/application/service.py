from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from datetime import UTC, datetime

from app.domain.errors import (
    AuthenticationRequiredError,
    ConnectorDegradedError,
    NetworkRequiredError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    ConnectionState,
    ConnectionStatus,
    MapBounds,
    Principal,
    RentalListingFilterOption,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
    RentalMapBubble,
    RentalMapBubbleFilters,
    RentalMapPage,
    RentalMapNearbySearchFilters,
    RentalMapNearbySearchResult,
    RentalMapSearchFilters,
    RentalMapSuggestion,
    RentalMapSuggestionFilters,
)
from app.domain.modules import CrmModule, crm_modules
from app.domain.providers.crm_client import CrmClient
from app.domain.providers.session_provider import SessionProvider
from app.infrastructure.settings import Settings


class ConnectorService:
    """Coordinates CRM use cases without exposing authentication material."""

    def __init__(
        self, settings: Settings, session_provider: SessionProvider, crm_client: CrmClient
    ) -> None:
        self._settings = settings
        self._session_provider = session_provider
        self._crm_client = crm_client

    def connection_status(self) -> ConnectionStatus:
        provider_status = self._session_provider.status()
        return ConnectionStatus(
            state=provider_status.state,
            message=provider_status.message,
            bound_employee_principal=self._settings.bound_employee_principal or None,
            mcp_transport=self._settings.mcp_transport,
            checked_at=datetime.now(UTC),
        )

    def modules(self) -> tuple[CrmModule, ...]:
        return crm_modules()

    def whoami(self) -> Principal:
        self._require_ready()
        principal = self._crm_client.whoami()
        self._verify_bound_principal(principal)
        return principal

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        self._require_ready()
        return self._crm_client.search_rental_listings(filters)

    def rental_listing_filter_options(self) -> tuple[RentalListingFilterOption, ...]:
        self._require_ready()
        return self._crm_client.rental_listing_filter_options()

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        self._require_ready()
        return self._crm_client.get_rental_listing_detail(listing_id)

    def search_rental_map(self, filters: RentalMapSearchFilters) -> RentalMapPage:
        self._require_ready()
        return self._crm_client.search_rental_map(filters)

    def rental_map_bubbles(self, filters: RentalMapBubbleFilters) -> tuple[RentalMapBubble, ...]:
        self._require_ready()
        return self._crm_client.rental_map_bubbles(filters)

    def rental_map_suggest(
        self, filters: RentalMapSuggestionFilters
    ) -> tuple[RentalMapSuggestion, ...]:
        self._require_ready()
        return self._crm_client.rental_map_suggest(filters)

    @property
    def default_city_id(self) -> str:
        return self._settings.crm_default_city_code

    def search_rental_map_nearby(
        self, filters: RentalMapNearbySearchFilters
    ) -> RentalMapNearbySearchResult:
        """Find listings in communities whose map centroids fall in a radius.

        This mirrors the product's draw-a-circle flow: resolve a place, fetch
        community bubbles for its enclosing rectangle, select community ids in
        the circle, then call ``drawhouselist`` with those ids.  It is not a
        precise property-coordinate radius query because the upstream exposes
        community, rather than individual-house, coordinates.
        """
        self._require_ready()
        if filters.center_latitude is not None and filters.center_longitude is not None:
            center = RentalMapSuggestion(
                item_type="provided_coordinate",
                item_type_name="provided coordinate",
                item_id="",
                name=filters.location,
                count_text=None,
                latitude=filters.center_latitude,
                longitude=filters.center_longitude,
            )
        else:
            suggestions = self._crm_client.rental_map_suggest(
                RentalMapSuggestionFilters(
                    city_id=filters.city_id,
                    data_source=filters.data_source,
                    query=filters.location,
                )
            )
            center = _pick_map_center(suggestions, filters.location)
        if center is None or center.latitude is None or center.longitude is None:
            raise UpstreamInvalidInputError(
                "map location could not be resolved to coordinates"
            )

        bounds = _radius_bounds(
            latitude=center.latitude,
            longitude=center.longitude,
            radius_meters=filters.radius_meters,
        )
        condition_tokens = _nearby_condition_tokens(filters)
        bubbles = self._crm_client.rental_map_bubbles(
            RentalMapBubbleFilters(
                city_id=filters.city_id,
                data_source=filters.data_source,
                bounds=bounds,
                group_type="community",
                group_id=None,
                condition_tokens=condition_tokens,
            )
        )
        community_ids = tuple(
            dict.fromkeys(
                bubble.bubble_id
                for bubble in bubbles
                if bubble.bubble_id
                and bubble.latitude is not None
                and bubble.longitude is not None
                and _haversine_meters(
                    center.latitude, center.longitude, bubble.latitude, bubble.longitude
                ) <= filters.radius_meters
            )
        )[:200]

        result = self._crm_client.search_rental_map(
            RentalMapSearchFilters(
                city_id=filters.city_id,
                data_source=filters.data_source,
                bounds=bounds,
                page=filters.page,
                mode="circle",
                condition_tokens=condition_tokens,
                result_type=None,
                resblock_id=None,
                resblock_ids=community_ids,
            )
        ) if community_ids else RentalMapPage(
            items=(),
            page=filters.page,
            total=0,
            has_more=False,
            mode="circle",
            request_id="local-empty-circle",
        )
        return RentalMapNearbySearchResult(
            center=center,
            radius_meters=filters.radius_meters,
            matched_community_count=len(community_ids),
            result=result,
        )

    def _require_ready(self) -> None:
        status = self._session_provider.status()
        if status.state is ConnectionState.AUTH_REQUIRED:
            raise AuthenticationRequiredError(status.message)
        if status.state is ConnectionState.NETWORK_REQUIRED:
            raise NetworkRequiredError(status.message)
        if status.state is not ConnectionState.READY:
            raise ConnectorDegradedError(status.message)

    def _verify_bound_principal(self, principal: Principal) -> None:
        expected = self._settings.bound_employee_principal
        if expected and principal.employee_principal != expected:
            raise ConnectorDegradedError("CRM principal does not match this connector instance")


def _pick_map_center(
    suggestions: tuple[RentalMapSuggestion, ...], location: str
) -> RentalMapSuggestion | None:
    """Prefer an exact label, but only accept suggestions with coordinates."""
    available = [
        item for item in suggestions if item.latitude is not None and item.longitude is not None
    ]
    if not available:
        return None
    return next((item for item in available if item.name == location), available[0])


def _radius_bounds(*, latitude: float, longitude: float, radius_meters: int):
    latitude_delta = radius_meters / 111_320
    longitude_delta = radius_meters / max(111_320 * cos(radians(latitude)), 1)
    return MapBounds(
        min_longitude=max(-180.0, longitude - longitude_delta),
        max_longitude=min(180.0, longitude + longitude_delta),
        min_latitude=max(-90.0, latitude - latitude_delta),
        max_latitude=min(90.0, latitude + latitude_delta),
    )


def _haversine_meters(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    latitude_delta = radians(latitude_b - latitude_a)
    longitude_delta = radians(longitude_b - longitude_a)
    factor = (
        sin(latitude_delta / 2) ** 2
        + cos(radians(latitude_a))
        * cos(radians(latitude_b))
        * sin(longitude_delta / 2) ** 2
    )
    return 6_371_000 * 2 * asin(sqrt(factor))


def _nearby_condition_tokens(filters: RentalMapNearbySearchFilters) -> tuple[str, ...]:
    tokens: list[str] = []
    if filters.price_min_yuan is not None:
        tokens.append(f"obrp{filters.price_min_yuan}")
    if filters.price_max_yuan is not None:
        tokens.append(f"oerp{filters.price_max_yuan}")
    tokens.extend(f"l{room}" for room in sorted(set(filters.rooms)))
    tokens.extend(
        {"whole_rent": "rt001", "shared_rent": "rt002"}[mode]
        for mode in filters.rental_modes
    )
    return tuple(tokens)
