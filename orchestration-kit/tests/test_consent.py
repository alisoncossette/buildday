"""Red Rover consent transitions — the machine-checkable definition of 'done'.
Starts RED (contract raises NotImplementedError); the build-verify loop turns it green."""
from consent_agent import ConsentAgent

OWNER, AGENT, SCOPE = "@owner", "@voicebot", "voice_clone"


def granted():
    a = ConsentAgent()
    a.grant(OWNER, AGENT, SCOPE)
    return a


def test_speaks_as_clone_when_granted():
    assert granted().speak(AGENT, "hello")["voice"] == "clone"


def test_revoke_shifts_to_generic_immediately():
    a = granted()
    a.revoke(OWNER, AGENT, SCOPE)
    assert a.speak(AGENT, "still here?")["voice"] == "generic"  # SHIFT, not crash


def test_no_clone_utterance_after_revoke():
    a = granted()
    a.revoke(OWNER, AGENT, SCOPE)
    assert all(a.speak(AGENT, t)["voice"] != "clone" for t in ("a", "b", "c"))


def test_check_access_tracks_grant_then_revoke():
    a = granted()
    assert a.check_access(AGENT, SCOPE) is True
    a.revoke(OWNER, AGENT, SCOPE)
    assert a.check_access(AGENT, SCOPE) is False


def test_audit_logs_grant_and_revoke():
    a = granted()
    a.revoke(OWNER, AGENT, SCOPE)
    events = [e.get("event") for e in a.audit]
    assert "grant" in events and "revoke" in events
