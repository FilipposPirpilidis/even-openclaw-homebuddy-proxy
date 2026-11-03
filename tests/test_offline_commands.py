import json

import pytest

from app.offline_commands import (
    OfflineCommandRouter,
    normalize_offline_command,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Turn on the light. ", "turn on the light"),
        ("Άναψε όλα τα φώτα!", "αναψε ολα τα φωτα"),
        ("ΑΝΑΨΕ ΟΛΑ ΤΑ ΦΩΤΑ", "αναψε ολα τα φωτα"),
        ("Α\u0301ναψε όλα τα φώτα", "αναψε ολα τα φωτα"),
        ("κλείσε   το φως", "κλεισε το φωσ"),
    ],
)
def test_normalization(value: str, expected: str) -> None:
    assert normalize_offline_command(value) == expected


def test_router_rejects_normalized_duplicates(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "commands.json"
    path.write_text(
        json.dumps(
            {
                "HA_OFFLINE_EN": True,
                "home_assistant": {"url": "http://homeassistant:8123"},
                "commands": {
                    "άναψε το φως": {
                        "domain": "light",
                        "service": "turn_on",
                    },
                    "αναψε το φως": {
                        "domain": "light",
                        "service": "turn_on",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HA_OFFLINE_CONFIG_PATH", str(path))
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "secret")

    with pytest.raises(
        RuntimeError,
        match="Duplicate normalized offline command",
    ):
        OfflineCommandRouter.from_environment()

