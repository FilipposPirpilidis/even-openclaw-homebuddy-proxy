import asyncio
from datetime import datetime, timedelta, timezone

from app.location_store import LocationStore


def test_stale_location_is_not_added_to_openclaw_context(tmp_path) -> None:
    store = LocationStore(
        str(tmp_path / "client-location.json"),
        max_age_seconds=60,
    )

    async def scenario() -> tuple[str | None, dict[str, object]]:
        await store.update(
            latitude=37.9838,
            longitude=23.7275,
            captured_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            accuracy_meters=20,
        )
        return await store.context_message(), await store.status()

    context, status = asyncio.run(scenario())

    assert context is None
    assert status["enabled"] is True
    assert status["fresh"] is False


def test_location_is_restored_after_restart(tmp_path) -> None:
    path = tmp_path / "client-location.json"
    first = LocationStore(str(path), max_age_seconds=3600)

    async def store_location() -> None:
        await first.update(
            latitude=37.9838,
            longitude=23.7275,
            captured_at=datetime.now(timezone.utc),
            accuracy_meters=None,
        )

    asyncio.run(store_location())
    restored = LocationStore(str(path), max_age_seconds=3600)
    context = asyncio.run(restored.context_message())

    assert context is not None
    assert "37.983800" in context
    assert "23.727500" in context
