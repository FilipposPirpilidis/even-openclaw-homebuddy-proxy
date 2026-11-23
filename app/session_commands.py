from app.offline_commands import normalize_offline_command


_RESET_RESPONSES = {
    "/new": "New conversation started.",
    "/reset": "New conversation started.",
    "new conversation": "New conversation started.",
    "start new conversation": "New conversation started.",
    "start a new conversation": "New conversation started.",
    "reset conversation": "New conversation started.",
    "reset our conversation": "New conversation started.",
    "νεα συζητηση": "Ξεκίνησα νέα συζήτηση.",
    "ξεκινα νεα συζητηση": "Ξεκίνησα νέα συζήτηση.",
    "ξεκινα μια νεα συζητηση": "Ξεκίνησα νέα συζήτηση.",
    "δημιουργησε νεα συζητηση": "Ξεκίνησα νέα συζήτηση.",
    "επαναφορα συζητησης": "Ξεκίνησα νέα συζήτηση.",
    "κανε επαναφορα της συζητησης": "Ξεκίνησα νέα συζήτηση.",
}


def session_reset_response(text: str) -> str | None:
    """Return a confirmation for an exact direct-session reset command."""
    return _RESET_RESPONSES.get(normalize_offline_command(text))
