import json
from urllib.parse import unquote
from urllib.request import Request

from app.infrastructure.crm_map_geocoder import CrmMapGeocoder


class FakeResponse:
    def __init__(self, payload: list[dict]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


def test_crm_map_geocoder_picks_exact_community() -> None:
    payload = [
        {"suggestion_id": "1", "text": "万科知园A区", "district_name": "成华",
         "item_type": "community", "latitude": 30.6958, "longitude": 104.1339,
         "bizcircle_name": "理工大"},
        {"suggestion_id": "2", "text": "A地块", "district_name": "简阳",
         "item_type": "community", "latitude": 30.33, "longitude": 104.49,
         "bizcircle_name": "东部新区"},
    ]

    def opener(request: Request, timeout: float = 0):
        assert "suggest?query=" in request.full_url
        return FakeResponse(payload)

    geocoder = CrmMapGeocoder("http://127.0.0.1:8020", opener=opener)
    hit = geocoder.locate_community("万科知园A区")
    assert hit is not None
    assert hit.district == "成华"
    assert hit.uid == "1"
    assert abs(hit.latitude - 30.6958) < 1e-6
    by_id = geocoder.locate_community("简称对不上", community_id="1")
    assert by_id is not None
    assert by_id.uid == "1"
