import pytest

from app.session_commands import session_reset_response


@pytest.mark.parametrize(
    ("command", "response"),
    [
        ("/new", "New conversation started."),
        ("Start a new conversation.", "New conversation started."),
        ("Reset our conversation", "New conversation started."),
        ("Νέα συζήτηση", "Ξεκίνησα νέα συζήτηση."),
        ("Ξεκίνα μια νέα συζήτηση.", "Ξεκίνησα νέα συζήτηση."),
        ("Δημιούργησε νέα συζήτηση", "Ξεκίνησα νέα συζήτηση."),
    ],
)
def test_exact_session_reset_commands(command: str, response: str) -> None:
    assert session_reset_response(command) == response


@pytest.mark.parametrize(
    "text",
    [
        "Tell me how to start a new conversation in OpenClaw",
        "Μπορείς να μου πεις πώς ξεκινά μια νέα συζήτηση;",
        "Do not reset our conversation",
        "new conversation about artificial intelligence",
    ],
)
def test_session_reset_does_not_search_inside_conversation(text: str) -> None:
    assert session_reset_response(text) is None
