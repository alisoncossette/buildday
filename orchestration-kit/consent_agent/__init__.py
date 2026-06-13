"""Red Rover — consent state machine (STARTS RED on purpose).

A voice agent may speak AS the owner (their cloned voice) ONLY within a live grant.
Revoke -> the agent SHIFTS to a generic voice immediately (graceful, not a crash).
Every transition is audited. Consent is scoped, revocable, owner-held.

This contract is intentionally unimplemented so `python grade.py` starts red; the
build-verify loop fills in the real behavior until the tests + grader go green.
"""


class ConsentAgent:
    def __init__(self):
        raise NotImplementedError("build the consent state machine")

    def grant(self, owner, agent, scope):
        """Owner grants `agent` a `scope` (e.g. 'voice_clone'). Audited."""
        raise NotImplementedError

    def revoke(self, owner, agent, scope):
        """Owner revokes the scope. Takes effect on the very next action. Audited."""
        raise NotImplementedError

    def check_access(self, agent, scope) -> bool:
        """True iff `agent` currently holds a live grant for `scope`."""
        raise NotImplementedError

    def speak(self, agent, text) -> dict:
        """Return {'voice': 'clone' | 'generic', 'text': text}.
        'clone' ONLY if the agent holds a live voice_clone grant; otherwise it SHIFTS to 'generic'."""
        raise NotImplementedError

    @property
    def audit(self) -> list:
        """List of {'actor', 'event', 'scope'} transitions (grant / revoke / speak)."""
        raise NotImplementedError
