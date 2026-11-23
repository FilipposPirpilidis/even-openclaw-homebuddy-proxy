import importlib
import json
import stat
import sys

from fastapi.testclient import TestClient

from app.config import get_settings


def load_location_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_TRANSPORT", "direct")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "g" * 32)
    monkeypatch.setenv("PROXY_BEARER_TOKEN", "p" * 32)
    monkeypatch.setenv("LOCATION_CONTEXT_ENABLED", "true")
    monkeypatch.setenv(
        "LOCATION_STATE_PATH",
        str(tmp_path / "client-location.json"),
    )
    monkeypatch.setenv(
        "OPENCLAW_SESSION_STATE_PATH",
        str(tmp_path / "openclaw-session.json"),
    )
    monkeypatch.setenv(
        "HA_OFFLINE_CONFIG_PATH",
        str(tmp_path / "missing-offline-commands.json"),
    )
    get_settings.cache_clear()
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main")


def test_location_update_status_and_clear(monkeypatch, tmp_path) -> None:
    main = load_location_app(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {'p' * 32}"}
    state_path = tmp_path / "client-location.json"

    with TestClient(main.app) as client:
        assert client.post(
            "/v1/location",
            json={
                "latitude": 37.9838,
                "longitude": 23.7275,
            },
        ).status_code == 401

        updated = client.post(
            "/v1/location",
            headers=headers,
            json={
                "enabled": True,
                "latitude": 37.9838,
                "longitude": 23.7275,
                "accuracy_meters": 20,
                "captured_at": "2026-11-23T15:30:00Z",
            },
        )
        status_response = client.get("/v1/location", headers=headers)
        cleared = client.delete("/v1/location", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["location"]["latitude"] == 37.9838
    assert status_response.json()["location"]["longitude"] == 23.7275
    assert cleared.json() == {"enabled": False, "location": None}
    assert not state_path.exists()
    get_settings.cache_clear()


def test_location_state_is_private_and_persistent(monkeypatch, tmp_path) -> None:
    main = load_location_app(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {'p' * 32}"}
    state_path = tmp_path / "client-location.json"

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/location",
            headers=headers,
            json={
                "latitude": 37.9838,
                "longitude": 23.7275,
            },
        )

    assert response.status_code == 200
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["latitude"] == 37.9838
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    get_settings.cache_clear()


def test_location_can_arrive_with_chat_request(monkeypatch, tmp_path) -> None:
    main = load_location_app(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {'p' * 32}"}

    async def fake_ask(
        text: str,
        *,
        system_context: str | None = None,
    ) -> str:
        assert text == "Where am I?"
        assert system_context is not None
        assert "37.983800" in system_context
        assert "23.727500" in system_context
        return "Your last known location is in Athens."

    main.bridge.ask = fake_ask

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "openclaw",
                "messages": [{"role": "user", "content": "Where am I?"}],
                "location": {
                    "latitude": 37.9838,
                    "longitude": 23.7275,
                    "accuracy_meters": 20,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == (
        "Your last known location is in Athens."
    )
    get_settings.cache_clear()
