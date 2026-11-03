import asyncio
import json
from pathlib import Path

import pytest

from app.offline_commands import (
    IntentMatcher,
    OfflineCommandRouter,
    normalize_offline_command,
    normalize_intent_text,
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


def _intent_config() -> dict:
    return {
        "enabled": True,
        "polite_phrases": [
            "please",
            "could you",
            "σε παρακαλώ",
            "μπορείς να",
        ],
        "articles": [
            "the",
            "my",
            "το",
            "τον",
            "τη",
            "την",
            "τα",
        ],
        "actions": {
            "turn_on": [
                "turn on",
                "switch on",
                "άναψε",
                "άνοιξε",
                "ανάψεις",
            ],
            "turn_off": ["turn off", "switch off", "σβήσε", "κλείσε"],
        },
        "entities": {
            "water_heater": {
                "aliases": [
                    "water heater",
                    "θερμοσίφωνας",
                    "θερμοσίφωνο",
                    "θερμοσίφωνα",
                ],
                "actions": {
                    "turn_on": {
                        "domain": "switch",
                        "service": "turn_on",
                        "service_data": {"entity_id": "switch.water_heater"},
                        "response": "Water heater turned on.",
                    },
                    "turn_off": {
                        "domain": "switch",
                        "service": "turn_off",
                        "service_data": {"entity_id": "switch.water_heater"},
                        "response": "Water heater turned off.",
                    },
                },
            }
        },
    }


@pytest.mark.parametrize(
    "text",
    [
        "Άναψε τον θερμοσίφωνα",
        "Άναψε το θερμοσίφωνα, σε παρακαλώ.",
        "Σε παρακαλώ, άναψε τον θερμοσίφωνα",
        "Άναψε, σε παρακαλώ, τον θερμοσίφωνα",
        "Μπορείς να ανάψεις τον θερμοσίφωνα;",
        "Please turn on the water heater.",
        "Could you turn on the water heater, please?",
    ],
)
def test_intent_matcher_accepts_complete_commands(text: str) -> None:
    matcher = IntentMatcher.from_config(_intent_config())
    assert matcher is not None
    action = matcher.match(text)
    assert action is not None
    assert action.domain == "switch"
    assert action.service == "turn_on"


@pytest.mark.parametrize(
    "text",
    [
        "Γιατί δεν άναψε ο θερμοσίφωνας;",
        "Θύμισέ μου να ανάψω τον θερμοσίφωνα",
        "Πες μου αν πρέπει να ανάψω τον θερμοσίφωνα",
        "Ο Γιάννης είπε να ανάψει τον θερμοσίφωνα",
        "What happens if I turn on the water heater?",
        "Please tell me why I should turn on the water heater",
        "Turn on the water heater and tell me the temperature",
    ],
)
def test_intent_matcher_rejects_discussion_sentences(text: str) -> None:
    matcher = IntentMatcher.from_config(_intent_config())
    assert matcher is not None
    assert matcher.match(text) is None


def test_intent_normalization_is_multilingual_and_punctuation_safe() -> None:
    assert normalize_intent_text("Άναψε, THE θερμοσίφωνα!") == (
        "αναψε the θερμοσιφωνα"
    )


def test_legacy_execute_if_match_alias() -> None:
    router = OfflineCommandRouter.disabled()
    assert asyncio.run(router.execute_if_match("anything")) is None


@pytest.mark.parametrize(
    ("text", "domain", "service", "entity_id"),
    [
        ("Turn on the TV", "script", "turn_on", "script.power_tv"),
        ("Κλείσε σε παρακαλώ την τηλεόραση", "script", "turn_on", "script.power_tv"),
        ("Σε παρακαλώ κλείσε την τηλεόραση", "script", "turn_on", "script.power_tv"),
        ("Άναψε τα φώτα στην κουζίνα", "light", "turn_on", "light.kitchen"),
        ("Turn off the bedroom lights", "light", "turn_off", "light.bedroom"),
        ("Δυνάμωσε τη φωνή", "script", "turn_on", "script.volume_up_tv"),
        ("Φτιάξε μου ομελέτα", "script", "turn_on", "script.omelete_fryer"),
        ("Close the garage door", "script", "turn_on", "script.close_garage_door"),
        ("Set movie lights", "script", "turn_on", "script.movie_lights"),
        (
            "Ρύθμισε τα φώτα για ταινία",
            "script",
            "turn_on",
            "script.movie_lights",
        ),
        (
            "Φτιάξε ατμόσφαιρα για ταινία",
            "script",
            "turn_on",
            "script.movie_lights",
        ),
    ],
)
def test_homebuddy_intent_configuration(
    text: str,
    domain: str,
    service: str,
    entity_id: str,
) -> None:
    path = (
        Path(__file__).parents[1]
        / "config"
        / "intent_matching.homebuddy.example.json"
    )
    matcher = IntentMatcher.from_config(
        json.loads(path.read_text(encoding="utf-8"))
    )
    assert matcher is not None
    action = matcher.match(text)
    assert action is not None
    assert action.domain == domain
    assert action.service == service
    assert action.service_data["entity_id"] == entity_id

