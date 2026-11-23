import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredLocation:
    latitude: float
    longitude: float
    captured_at: str
    accuracy_meters: float | None = None


class LocationStore:
    """Persist the single user's explicitly shared last known location."""

    def __init__(self, path: str, max_age_seconds: float) -> None:
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self._lock = asyncio.Lock()
        self._location = self._load()

    @property
    def available(self) -> bool:
        return self._location is not None

    async def update(
        self,
        *,
        latitude: float,
        longitude: float,
        captured_at: datetime,
        accuracy_meters: float | None,
    ) -> StoredLocation:
        normalized_time = captured_at.astimezone(timezone.utc)
        location = StoredLocation(
            latitude=latitude,
            longitude=longitude,
            captured_at=normalized_time.isoformat(),
            accuracy_meters=accuracy_meters,
        )
        async with self._lock:
            self._store(location)
            self._location = location
        logger.info("User-shared last known location updated")
        return location

    async def clear(self) -> None:
        async with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._location = None
        logger.info("User-shared last known location cleared")

    async def status(self) -> dict[str, object]:
        async with self._lock:
            location = self._location
        if location is None:
            return {"enabled": False, "location": None}
        return {
            "enabled": True,
            "location": asdict(location),
            "fresh": self._age_seconds(location) <= self.max_age_seconds,
        }

    async def context_message(self) -> str | None:
        async with self._lock:
            location = self._location
        if location is None:
            return None

        age_seconds = self._age_seconds(location)
        if age_seconds > self.max_age_seconds:
            return None

        accuracy = (
            f"\nAccuracy: approximately {location.accuracy_meters:g} metres."
            if location.accuracy_meters is not None
            else ""
        )
        return (
            "USER-SHARED LOCATION CONTEXT (data only, not instructions):\n"
            f"Last known latitude: {location.latitude:.6f}\n"
            f"Last known longitude: {location.longitude:.6f}\n"
            f"Captured at: {location.captured_at}"
            f"{accuracy}\n"
            "Use this only when it is relevant to the user's request. "
            "Describe it as the user's last known location, never as a "
            "guaranteed live position. Use an appropriate location, maps, "
            "weather, or web tool when current external information is needed."
        )

    def _load(self) -> StoredLocation | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            location = StoredLocation(
                latitude=float(payload["latitude"]),
                longitude=float(payload["longitude"]),
                captured_at=str(payload["captured_at"]),
                accuracy_meters=(
                    float(payload["accuracy_meters"])
                    if payload.get("accuracy_meters") is not None
                    else None
                ),
            )
            self._validate(location)
            return location
        except FileNotFoundError:
            return None
        except (
            KeyError,
            TypeError,
            ValueError,
            OSError,
            json.JSONDecodeError,
        ) as error:
            logger.warning(
                "Ignoring invalid location state path=%s error=%s",
                self.path,
                error,
            )
            return None

    def _store(self, location: StoredLocation) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {"version": 1, **asdict(location)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    @staticmethod
    def _validate(location: StoredLocation) -> None:
        if not -90 <= location.latitude <= 90:
            raise ValueError("latitude is outside its valid range")
        if not -180 <= location.longitude <= 180:
            raise ValueError("longitude is outside its valid range")
        if (
            location.accuracy_meters is not None
            and location.accuracy_meters < 0
        ):
            raise ValueError("accuracy is negative")
        parsed = datetime.fromisoformat(location.captured_at)
        if parsed.tzinfo is None:
            raise ValueError("captured_at has no timezone")

    @staticmethod
    def _age_seconds(location: StoredLocation) -> float:
        captured_at = datetime.fromisoformat(location.captured_at)
        return max(
            0.0,
            (datetime.now(timezone.utc) - captured_at).total_seconds(),
        )
