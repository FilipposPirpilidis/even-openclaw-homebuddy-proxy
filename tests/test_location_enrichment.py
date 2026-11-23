import asyncio
import httpx

from app.config import Settings
from app.location_enrichment import LocationEnricher


def direct_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openclaw_transport": "direct",
        "openclaw_gateway_token": "g" * 32,
        "proxy_bearer_token": "p" * 32,
        "location_enrichment_enabled": True,
        "location_enrichment_cache_seconds": 900,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_location_enrichment_uses_nominatim_and_nearest_landmark() -> None:
    observed_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_paths.append(request.url.path)
        if request.url.path == "/reverse":
            assert request.url.params["accept-language"] == "el,en"
            assert request.headers["user-agent"].startswith(
                "HomeBuddy-OpenClaw/1.0"
            )
            return httpx.Response(
                200,
                json={
                    "display_name": "Κορίνθου – Πατρών, Πάτρα 264 41"
                },
            )
        assert request.url.path == "/api/interpreter"
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "lat": 38.2600,
                        "lon": 21.7500,
                        "tags": {
                            "name": "Far Cafe",
                            "amenity": "cafe",
                        },
                    },
                    {
                        "type": "node",
                        "lat": 38.2584,
                        "lon": 21.7480,
                        "tags": {
                            "name": "Patra Fitness",
                            "leisure": "fitness_centre",
                        },
                    },
                ]
            },
        )

    async def scenario():
        enricher = LocationEnricher(
            direct_settings(),
            transport=httpx.MockTransport(handler),
        )
        await enricher.start()
        try:
            return await enricher.enrich(38.25834, 21.747959)
        finally:
            await enricher.stop()

    enrichment = asyncio.run(scenario())

    assert enrichment.address == "Κορίνθου – Πατρών, Πάτρα 264 41"
    assert enrichment.nearby_landmark == "Patra Fitness (γυμναστήριο)"
    assert enrichment.plus_code == "8GC37P5X+85"
    assert observed_paths == ["/reverse", "/api/interpreter"]


def test_location_enrichment_survives_osm_failure() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async def scenario():
        enricher = LocationEnricher(
            direct_settings(),
            transport=httpx.MockTransport(handler),
        )
        await enricher.start()
        try:
            return await enricher.enrich(38.25834, 21.747959)
        finally:
            await enricher.stop()

    enrichment = asyncio.run(scenario())

    assert enrichment.address is None
    assert enrichment.nearby_landmark is None
    assert enrichment.plus_code == "8GC37P5X+85"
