import importlib
import sys

from fastapi.testclient import TestClient

from app.config import get_settings


def load_direct_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_TRANSPORT", "direct")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "g" * 32)
    monkeypatch.setenv("PROXY_BEARER_TOKEN", "p" * 32)
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


def test_authenticated_session_reset_endpoint(monkeypatch, tmp_path) -> None:
    main = load_direct_app(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {'p' * 32}"}

    with TestClient(main.app) as client:
        assert client.post("/v1/session/reset").status_code == 401

        response = client.post("/v1/session/reset", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "reset": True,
        "message": "New conversation started.",
    }
    assert (tmp_path / "openclaw-session.json").is_file()
    get_settings.cache_clear()


def test_exact_chat_command_resets_without_gateway_call(
    monkeypatch,
    tmp_path,
) -> None:
    main = load_direct_app(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {'p' * 32}"}

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "openclaw",
                "messages": [
                    {
                        "role": "user",
                        "content": "Ξεκίνα μια νέα συζήτηση.",
                    }
                ],
                "stream": False,
            },
        )

    assert response.status_code == 200
    assert (
        response.json()["choices"][0]["message"]["content"]
        == "Ξεκίνησα νέα συζήτηση."
    )
    assert (tmp_path / "openclaw-session.json").is_file()
    get_settings.cache_clear()
