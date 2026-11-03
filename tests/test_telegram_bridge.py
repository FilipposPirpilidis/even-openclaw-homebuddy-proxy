import pytest

from app.telegram_bridge import is_transient_status


@pytest.mark.parametrize(
    "value",
    [
        "Working",
        "Thinking",
        "Working\nLast activity: Gmail Get Thread",
        "Web Fetch: running",
        "Last activity: Home Assistant",
    ],
)
def test_transient_statuses(value: str) -> None:
    assert is_transient_status(value)


def test_final_response_is_not_transient() -> None:
    assert not is_transient_status(
        "The kitchen light is currently off."
    )

