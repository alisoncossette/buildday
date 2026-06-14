"""Stead — Composio adapter: 1000+ real integrations as Stead agent tools.

Composio is the execution layer that lets Stead act in the real world (Google Calendar,
Gmail, and ~1000 other apps) without Stead holding any third-party OAuth tokens itself.
Composio holds the connected account; Stead just calls a tool slug with a `user_id`. This
keeps Stead a thin, swappable adapter and keeps every credential revocable at the Composio
side (which mirrors the Bolo philosophy: scoped, owner-held, revocable access).

Two jobs, matching the track:
  (a) set up a CONNECTED ACCOUNT for a user (hosted OAuth via connected_accounts.link), and
  (b) expose Google Calendar create-event and Gmail send-email as @beta_tool functions for the
      tool_runner — same pattern as agent/stead_agent.py (order_food / care_handoff).

OFFLINE-FIRST. With no COMPOSIO_API_KEY (or the SDK not installed) every tool returns a clear,
honest message describing what it WOULD do and how to enable it — never a silent fake success —
so the demo runs on a hotspot and offline pytest / grade.py stay deterministic.

Confirmed against the CURRENT (2026) Composio Python SDK:
  - pip install composio composio_anthropic anthropic   (docs.composio.dev/docs/providers/anthropic)
  - Composio(api_key=...) ; tools.execute(slug=, user_id=, arguments=)  (docs.composio.dev/docs/executing-tools)
  - connected_accounts.link(user_id=, auth_config_id=) is the recommended hosted-OAuth flow;
    initiate() is being deprecated for managed OAuth (2026-05-08 new orgs / 2026-07-03 all orgs).
    (docs.composio.dev/docs/auth-configuration/connected-accounts)

Tool slugs: GOOGLECALENDAR_CREATE_EVENT, GMAIL_SEND_EMAIL.

Prereqs:  pip install composio composio_anthropic
          export COMPOSIO_API_KEY=...            (https://app.composio.dev -> Settings -> API Keys)

SHIP_BUILDERS free-starter setup (see ship_builders_setup() / the docstring below):
  1. Create a free account at https://app.composio.dev.
  2. (Optional) Apply the SHIP_BUILDERS promo / starter code in Settings -> Billing to unlock the
     free builder tier (extra tool-call quota + managed OAuth for Google Calendar + Gmail).
  3. Copy your API key into COMPOSIO_API_KEY.
  4. In the dashboard add the Google Calendar and Gmail toolkits; Composio's MANAGED OAuth means
     you do NOT need your own Google Cloud OAuth app for the demo — auth_config_id is optional and
     Composio picks the toolkit's managed config when omitted.
  5. Run connect_account("googlecalendar") / connect_account("gmail"), open the printed URL, approve.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from anthropic import beta_tool
except Exception:  # pragma: no cover - lets the module import for docs/offline even w/o anthropic
    def beta_tool(fn):
        """No-op fallback so this module imports without the anthropic SDK present."""
        return fn

COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY")
# One stable id ties every Stead action back to one owner's connected accounts in Composio.
# In the live app this is Mom's id; offline it is a deterministic placeholder.
STEAD_USER_ID = os.environ.get("COMPOSIO_USER_ID", "stead:mom")

# Confirmed current tool slugs (Composio dashboard -> Tools & Triggers).
CALENDAR_CREATE_EVENT = "GOOGLECALENDAR_CREATE_EVENT"
GMAIL_SEND_EMAIL = "GMAIL_SEND_EMAIL"

_OFFLINE_HINT = (
    "set COMPOSIO_API_KEY and `pip install composio composio_anthropic` to act for real "
    "(free starter: https://app.composio.dev)"
)


def _client():
    """Return a live Composio client, or None when offline (no key / SDK).

    Kept lazy so importing this module never requires the key or the package — the offline
    fallback paths below stay importable for the hotspot demo and the grader.
    """
    if not COMPOSIO_API_KEY:
        return None
    try:
        from composio import Composio
    except Exception:
        return None
    return Composio(api_key=COMPOSIO_API_KEY)


def _execute(slug: str, arguments: dict, user_id: str = None) -> dict:
    """Run one Composio tool by slug for `user_id`. Returns a normalized dict:
      {'ok': bool, 'data': <tool result>, 'error': <str|None>, ('offline': True)}.

    Offline (no key/SDK) returns ok=False with a clear message and offline=True — never a
    fake success — so callers can be honest about what did NOT happen.
    """
    user_id = user_id or STEAD_USER_ID
    composio = _client()
    if composio is None:
        return {"ok": False, "offline": True, "data": None,
                "error": f"OFFLINE: would call {slug} as {user_id} — {_OFFLINE_HINT}"}
    try:
        result = composio.tools.execute(slug=slug, user_id=user_id, arguments=arguments)
    except Exception as exc:  # noqa: BLE001 - surface the real error to the agent, don't crash
        return {"ok": False, "data": None, "error": f"{slug} failed: {exc}"}

    # The SDK returns either a typed object (with .successful / .data / .error) or a plain dict
    # depending on version; normalize both. Treat "no explicit failure" as success.
    if isinstance(result, dict):
        ok = result.get("successful", result.get("success", True))
        return {"ok": bool(ok), "data": result.get("data", result),
                "error": result.get("error")}
    ok = getattr(result, "successful", getattr(result, "success", True))
    return {"ok": bool(ok), "data": getattr(result, "data", result),
            "error": getattr(result, "error", None)}


# --------------------------------------------------------------------------- #
# (a) Connected-account setup
# --------------------------------------------------------------------------- #

def connect_account(toolkit: str, user_id: str = None, auth_config_id: str = None) -> dict:
    """Set up (or reuse) a connected account so Stead can act in `toolkit` for `user_id`.

    Uses the recommended hosted-OAuth flow `connected_accounts.link()`. If the user already has an
    ACTIVE connection for the toolkit, returns it without re-authorizing. Otherwise returns a
    `redirect_url` for the owner to approve in a browser (the credential lives in Composio, not in
    Stead — revoke it there to revoke Stead's access).

    Args:
        toolkit: Composio toolkit slug, e.g. 'googlecalendar' or 'gmail'.
        user_id: Whose account to connect (defaults to COMPOSIO_USER_ID / 'stead:mom').
        auth_config_id: Optional dashboard auth-config id. Omit to use Composio MANAGED OAuth
            (no Google Cloud app needed for the demo).

    Returns:
        {'status': 'connected'|'authorize'|'offline'|'error', 'redirect_url': ..., 'account_id': ...}
    """
    user_id = user_id or STEAD_USER_ID
    composio = _client()
    if composio is None:
        return {"status": "offline", "toolkit": toolkit, "user_id": user_id,
                "message": f"OFFLINE: would start a {toolkit} connection for {user_id} — {_OFFLINE_HINT}"}
    try:
        # Reuse an existing ACTIVE connection if there is one (idempotent setup).
        existing = composio.connected_accounts.list(user_ids=[user_id], statuses=["ACTIVE"])
        items = getattr(existing, "items", existing) or []
        for conn in items:
            if str(getattr(conn, "toolkit", "")).lower().startswith(toolkit.lower()):
                return {"status": "connected", "toolkit": toolkit, "user_id": user_id,
                        "account_id": getattr(conn, "id", None)}

        # Hosted OAuth. auth_config_id is optional with Composio-managed OAuth.
        kwargs = {"user_id": user_id}
        if auth_config_id:
            kwargs["auth_config_id"] = auth_config_id
        else:
            kwargs["toolkit"] = toolkit  # let Composio pick the toolkit's managed auth config
        connection = composio.connected_accounts.link(**kwargs)
        return {"status": "authorize", "toolkit": toolkit, "user_id": user_id,
                "redirect_url": getattr(connection, "redirect_url", None),
                "message": "Open redirect_url and approve; then tools will work for this user."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "toolkit": toolkit, "user_id": user_id, "error": str(exc)}


# --------------------------------------------------------------------------- #
# (b) Stead agent tools — register these in the tool_runner alongside the others
#     in stead_agent.py (calendar booking for the scheduler, messaging).
# --------------------------------------------------------------------------- #

@beta_tool
def schedule_event(summary: str, start_datetime: str, duration_minutes: int = 60,
                   description: str = "", attendees: str = "") -> str:
    """Book an event on Ruby's Google Calendar (the scheduler's calendar-booking action).
    Acts through Composio's connected Google Calendar account; offline it says clearly that it
    did NOT book and how to enable it — it never pretends the event was created.

    Args:
        summary: Event title, e.g. 'PCA shift — morning'.
        start_datetime: Start time in ISO 8601, e.g. '2026-06-15T09:00:00-04:00'.
        duration_minutes: Length of the event in minutes (used to set the end time).
        description: Optional notes for the event body.
        attendees: Optional comma-separated attendee emails to invite.
    """
    arguments = {
        "summary": summary,
        "start_datetime": start_datetime,
        "event_duration_minutes": duration_minutes,
    }
    if description:
        arguments["description"] = description
    invitees = [a.strip() for a in attendees.split(",") if a.strip()]
    if invitees:
        arguments["attendees"] = invitees

    res = _execute(CALENDAR_CREATE_EVENT, arguments)
    if res.get("offline"):
        return (f"NOT BOOKED (offline): would create '{summary}' at {start_datetime} "
                f"for {duration_minutes} min on Google Calendar. {_OFFLINE_HINT}")
    if not res["ok"]:
        return f"NOT BOOKED: Google Calendar refused — {res.get('error') or 'unknown error'}."
    data = res.get("data") or {}
    link = ""
    if isinstance(data, dict):
        link = data.get("htmlLink") or data.get("html_link") or ""
    return (f"BOOKED '{summary}' at {start_datetime} ({duration_minutes} min) on Google Calendar"
            + (f": {link}" if link else "."))


@beta_tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email on Ruby's behalf via Gmail (messaging — e.g. notify a caregiver of a shift).
    Acts through Composio's connected Gmail account; offline it says clearly that it did NOT send
    and how to enable it — it never pretends the message went out.

    Args:
        to: Recipient email address.
        subject: Subject line.
        body: Plain-text message body.
    """
    res = _execute(GMAIL_SEND_EMAIL, {
        "recipient_email": to,
        "subject": subject,
        "body": body,
    })
    if res.get("offline"):
        return (f"NOT SENT (offline): would email {to} — subj '{subject}'. {_OFFLINE_HINT}")
    if not res["ok"]:
        return f"NOT SENT: Gmail refused — {res.get('error') or 'unknown error'}."
    return f"SENT email to {to} — subj '{subject}'."


# The tools to register in stead_agent.py's tool_runner(tools=[...]).
COMPOSIO_TOOLS = [schedule_event, send_email]


def ship_builders_setup() -> str:
    """Return the SHIP_BUILDERS free-starter setup instructions as a printable string."""
    return (
        "Stead x Composio — SHIP_BUILDERS free-starter setup\n"
        "  1. Sign up free at https://app.composio.dev\n"
        "  2. Settings -> Billing: apply the SHIP_BUILDERS starter code for the free builder tier\n"
        "     (extra tool-call quota + managed OAuth for Google Calendar + Gmail).\n"
        "  3. Settings -> API Keys: copy your key into  export COMPOSIO_API_KEY=...\n"
        "  4. pip install composio composio_anthropic\n"
        "  5. Add the 'googlecalendar' and 'gmail' toolkits in the dashboard (managed OAuth — no\n"
        "     Google Cloud app needed for the demo).\n"
        "  6. python -c \"from tools.composio_tool import connect_account; "
        "print(connect_account('googlecalendar')); print(connect_account('gmail'))\"\n"
        "     Open each printed redirect_url and approve. Then schedule_event / send_email act for real."
    )


if __name__ == "__main__":
    print(ship_builders_setup())
    print()
    print("Self-check (offline-safe):")
    print(" ", connect_account("googlecalendar"))
    print(" ", schedule_event("PCA shift — morning", "2026-06-15T09:00:00-04:00", 180))
    print(" ", send_email("pca@example.com", "Shift confirmed", "See you at 9am."))
