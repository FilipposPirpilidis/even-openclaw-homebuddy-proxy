import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openlocationcode import openlocationcode

from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocationEnrichment:
    address: str | None
    nearby_landmark: str | None
    plus_code: str


class LocationEnricher:
    """Resolve user-shared coordinates through policy-conscious OSM calls."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request_started = 0.0
        self._cache: dict[
            tuple[float, float],
            tuple[float, LocationEnrichment],
        ] = {}

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=self.settings.location_enrichment_timeout_seconds,
            headers={
                "User-Agent": self.settings.location_osm_user_agent,
                "Accept": "application/json",
            },
            transport=self._transport,
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def enrich(
        self,
        latitude: float,
        longitude: float,
    ) -> LocationEnrichment:
        plus_code = openlocationcode.encode(latitude, longitude, 10)
        if not self.settings.location_enrichment_enabled:
            return LocationEnrichment(None, None, plus_code)

        key = (round(latitude, 4), round(longitude, 4))
        cached = self._cache.get(key)
        if cached is not None:
            stored_at, enrichment = cached
            if (
                time.monotonic() - stored_at
                <= self.settings.location_enrichment_cache_seconds
            ):
                return enrichment

        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                stored_at, enrichment = cached
                if (
                    time.monotonic() - stored_at
                    <= self.settings.location_enrichment_cache_seconds
                ):
                    return enrichment

            await self._respect_public_service_pacing()
            address, landmark = await asyncio.gather(
                self._reverse_geocode(latitude, longitude),
                self._nearby_landmark(latitude, longitude),
            )
            enrichment = LocationEnrichment(
                address=address,
                nearby_landmark=landmark,
                plus_code=plus_code,
            )
            self._cache[key] = (time.monotonic(), enrichment)
            return enrichment

    async def _respect_public_service_pacing(self) -> None:
        elapsed = time.monotonic() - self._last_request_started
        wait_seconds = 1.05 - elapsed
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        self._last_request_started = time.monotonic()

    async def _reverse_geocode(
        self,
        latitude: float,
        longitude: float,
    ) -> str | None:
        if self._client is None:
            return None
        try:
            response = await self._client.get(
                f"{self.settings.location_nominatim_base_url}/reverse",
                params={
                    "format": "jsonv2",
                    "lat": f"{latitude:.7f}",
                    "lon": f"{longitude:.7f}",
                    "zoom": "18",
                    "addressdetails": "1",
                    "accept-language": self.settings.location_osm_language,
                },
            )
            response.raise_for_status()
            payload = response.json()
            display_name = payload.get("display_name")
            if isinstance(display_name, str):
                return _safe_map_text(display_name, 500)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            logger.warning(
                "OpenStreetMap reverse geocoding unavailable error=%s",
                type(error).__name__,
            )
        return None

    async def _nearby_landmark(
        self,
        latitude: float,
        longitude: float,
    ) -> str | None:
        if self._client is None:
            return None

        radius = self.settings.location_nearby_radius_meters
        query = f"""
[out:json][timeout:15];
(
  nwr(around:{radius},{latitude:.7f},{longitude:.7f})["name"]["amenity"];
  nwr(around:{radius},{latitude:.7f},{longitude:.7f})["name"]["leisure"];
  nwr(around:{radius},{latitude:.7f},{longitude:.7f})["name"]["shop"];
  nwr(around:{radius},{latitude:.7f},{longitude:.7f})["name"]["tourism"];
  nwr(around:{radius},{latitude:.7f},{longitude:.7f})["name"]["office"];
);
out center tags 40;
""".strip()
        try:
            response = await self._client.post(
                self.settings.location_overpass_url,
                data={"data": query},
            )
            response.raise_for_status()
            payload = response.json()
            elements = payload.get("elements", [])
            if not isinstance(elements, list):
                return None
            return _nearest_named_landmark(
                elements,
                latitude,
                longitude,
            )
        except (httpx.HTTPError, ValueError, TypeError) as error:
            logger.warning(
                "OpenStreetMap nearby landmark lookup unavailable error=%s",
                type(error).__name__,
            )
            return None


def _nearest_named_landmark(
    elements: list[Any],
    latitude: float,
    longitude: float,
) -> str | None:
    candidates: list[tuple[float, str]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags")
        if not isinstance(tags, dict):
            continue
        name = tags.get("name:el") or tags.get("name")
        if not isinstance(name, str):
            continue
        point = _element_point(element)
        if point is None:
            continue
        safe_name = _safe_map_text(name, 160)
        if not safe_name:
            continue
        category = _landmark_category(tags)
        label = f"{safe_name} ({category})" if category else safe_name
        distance = _distance_meters(latitude, longitude, *point)
        candidates.append((distance, label))

    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[0])
    return candidates[0][1]


def _element_point(element: dict[str, Any]) -> tuple[float, float] | None:
    source = (
        element.get("center")
        if isinstance(element.get("center"), dict)
        else element
    )
    try:
        return float(source["lat"]), float(source["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _landmark_category(tags: dict[str, Any]) -> str | None:
    translations = {
        "fitness_centre": "γυμναστήριο",
        "gym": "γυμναστήριο",
        "pharmacy": "φαρμακείο",
        "hospital": "νοσοκομείο",
        "clinic": "κλινική",
        "cafe": "καφέ",
        "restaurant": "εστιατόριο",
        "school": "σχολείο",
        "supermarket": "σούπερ μάρκετ",
        "hotel": "ξενοδοχείο",
    }
    for key in ("amenity", "leisure", "shop", "tourism", "office"):
        value = tags.get(key)
        if isinstance(value, str) and value:
            return translations.get(value, value.replace("_", " "))
    return None


def _safe_map_text(value: str, maximum_length: int) -> str:
    single_line = " ".join(value.replace("|", " ").split())
    safe = single_line[:maximum_length].strip()
    for character in ("\\", "*", "_", "[", "]", "<", ">", "#"):
        safe = safe.replace(character, f"\\{character}")
    return safe


def _distance_meters(
    first_latitude: float,
    first_longitude: float,
    second_latitude: float,
    second_longitude: float,
) -> float:
    radius = 6_371_000.0
    first_latitude_radians = math.radians(first_latitude)
    second_latitude_radians = math.radians(second_latitude)
    latitude_delta = math.radians(second_latitude - first_latitude)
    longitude_delta = math.radians(second_longitude - first_longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(first_latitude_radians)
        * math.cos(second_latitude_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
