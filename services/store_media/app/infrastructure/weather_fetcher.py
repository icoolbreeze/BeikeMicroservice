from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_WMO_DESCRIPTIONS: dict[int, tuple[str, str]] = {
    0: ("晴", "fa-sun"),
    1: ("晴间多云", "fa-sun"),
    2: ("多云", "fa-cloud-sun"),
    3: ("阴", "fa-cloud"),
    45: ("雾", "fa-smog"),
    48: ("雾凇", "fa-smog"),
    51: ("毛毛雨", "fa-cloud-rain"),
    53: ("毛毛雨", "fa-cloud-rain"),
    55: ("毛毛雨", "fa-cloud-rain"),
    56: ("冻毛毛雨", "fa-cloud-rain"),
    57: ("冻毛毛雨", "fa-cloud-rain"),
    61: ("小雨", "fa-cloud-rain"),
    63: ("中雨", "fa-cloud-showers-heavy"),
    65: ("大雨", "fa-cloud-showers-heavy"),
    66: ("冻雨", "fa-cloud-showers-heavy"),
    67: ("冻雨", "fa-cloud-showers-heavy"),
    71: ("小雪", "fa-snowflake"),
    73: ("中雪", "fa-snowflake"),
    75: ("大雪", "fa-snowflake"),
    77: ("雪粒", "fa-snowflake"),
    80: ("阵雨", "fa-cloud-rain"),
    81: ("阵雨", "fa-cloud-showers-heavy"),
    82: ("强阵雨", "fa-cloud-showers-heavy"),
    85: ("阵雪", "fa-snowflake"),
    86: ("强阵雪", "fa-snowflake"),
    95: ("雷阵雨", "fa-cloud-bolt"),
    96: ("雷阵雨伴冰雹", "fa-cloud-bolt"),
    99: ("强雷暴伴冰雹", "fa-cloud-bolt"),
}


@dataclass(frozen=True)
class WeatherReading:
    temperature_c: float
    description: str
    icon: str
    observed_at: str


class OpenMeteoWeatherFetcher:
    """抓取并缓存 Open-Meteo 官方实时天气（免费、无需密钥）。"""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        latitude: float,
        longitude: float,
        *,
        location_name: str = "成都 · 成华",
        timezone: str = "Asia/Shanghai",
        cache_seconds: int = 600,
        request_timeout: float = 10.0,
        user_agent: str = "store-media-big-screen/0.1",
    ):
        self._latitude = latitude
        self._longitude = longitude
        self._location_name = location_name
        self._timezone = timezone
        self._cache_seconds = cache_seconds
        self._timeout = request_timeout
        self._user_agent = user_agent
        self._reading: WeatherReading | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def latest(self) -> WeatherReading | None:
        with self._lock:
            if self._reading is not None and time.monotonic() - self._fetched_at < self._cache_seconds:
                return self._reading
        fetched = self._fetch()
        if fetched is not None:
            with self._lock:
                self._reading = fetched
                self._fetched_at = time.monotonic()
        with self._lock:
            return self._reading

    def _fetch(self) -> WeatherReading | None:
        query = urlencode({
            "latitude": f"{self._latitude:.4f}",
            "longitude": f"{self._longitude:.4f}",
            "current": "temperature_2m,weather_code",
            "timezone": self._timezone,
        })
        try:
            request = Request(f"{self.BASE_URL}?{query}", headers={"User-Agent": self._user_agent})
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        current = payload.get("current") or {}
        try:
            temperature = round(float(current.get("temperature_2m")), 1)
            code = int(current.get("weather_code", 0))
        except (TypeError, ValueError):
            return None
        description, icon = _WMO_DESCRIPTIONS.get(code, ("未知", "fa-cloud"))
        return WeatherReading(
            temperature_c=temperature,
            description=description,
            icon=icon,
            observed_at=current.get("time", ""),
        )

    @property
    def location_name(self) -> str:
        return self._location_name
