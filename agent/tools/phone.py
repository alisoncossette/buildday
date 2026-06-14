"""Stead — real outbound phone calls via Vapi (the agent actually dials and orders by voice).

Proven pattern (rebuilt fresh from rubysredrover v2): Vapi places the call, ElevenLabs supplies the
voice. In production the voice ID comes from a Bolo voice grant, so revoking the grant revokes the
voice. Falls back to a clearly-marked MOCK when VAPI_API_KEY is absent, so the whole flow is demoable
and testable offline without any keys.

One-time Vapi setup for a custom (cloned) ElevenLabs voice:
  vapi.ai -> Settings -> Provider Credentials -> ElevenLabs (add your ELEVENLABS_API_KEY there),
then either create an Assistant in the dashboard (set STEAD_VAPI_ASSISTANT_ID) or let this tool build
a transient assistant per call.
"""
import os
import time

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

VAPI_API_KEY = os.environ.get("VAPI_API_KEY")
VAPI_PHONE_NUMBER_ID = os.environ.get("VAPI_PHONE_NUMBER_ID")
VAPI_ASSISTANT_ID = os.environ.get("STEAD_VAPI_ASSISTANT_ID")  # preferred: configure voice+model in dashboard
VOICE_ID = os.environ.get("STEAD_VOICE_ID")                    # ElevenLabs voiceId (in prod: from a Bolo grant)
# Voice provider switch. Default to Vapi's native voice (no extra credential, always works).
# Flip STEAD_VOICE_PROVIDER=11labs to use Chantal's cloned ElevenLabs voice — but that requires the
# ElevenLabs key that OWNS the cloned voice to be connected in Vapi (Dashboard -> Provider Credentials).
VOICE_PROVIDER = os.environ.get("STEAD_VOICE_PROVIDER", "vapi")
VAPI_VOICE = os.environ.get("STEAD_VAPI_VOICE", "Elliot")      # a Vapi-native voice
# NOTE: this is the Vapi-HOSTED call model — it must be one of Vapi's allow-listed model ids
# (Vapi doesn't accept claude-opus-4-8 yet). Sonnet-4-6 is fast + capable for low-latency voice.
# The Stead app/agent still reason with claude-opus-4-8; only this on-call model differs.
CALL_MODEL = os.environ.get("STEAD_VAPI_MODEL", "claude-sonnet-4-6")
CALL_MODEL_PROVIDER = os.environ.get("STEAD_VAPI_MODEL_PROVIDER", "anthropic")
BASE = "https://api.vapi.ai"


def _auth():
    return {"Authorization": f"Bearer {VAPI_API_KEY}"}


def place_call(phone_number: str, system_prompt: str, first_message: str) -> dict:
    """Place a REAL outbound call to `phone_number`. Returns {'call_id','status', ('mock')}.

    Mock (no keys): returns what it *would* dial/say so the flow runs end-to-end offline.
    """
    if not (VAPI_API_KEY and VAPI_PHONE_NUMBER_ID):
        return {"call_id": "mock-call", "status": "queued", "mock": True,
                "would_call": phone_number, "first_message": first_message}

    payload = {"phoneNumberId": VAPI_PHONE_NUMBER_ID, "customer": {"number": phone_number}}
    if VAPI_ASSISTANT_ID:
        # Dashboard-configured assistant (voice + model live there); override the opening line.
        payload["assistantId"] = VAPI_ASSISTANT_ID
        payload["assistantOverrides"] = {
            "firstMessage": first_message,
            "model": {"messages": [{"role": "system", "content": system_prompt}]},
        }
    else:
        assistant = {
            "firstMessage": first_message,
            "model": {"provider": CALL_MODEL_PROVIDER, "model": CALL_MODEL,
                      "messages": [{"role": "system", "content": system_prompt}]},
        }
        if VOICE_PROVIDER == "11labs" and VOICE_ID:
            # Chantal's cloned voice — needs the owning ElevenLabs key connected in Vapi.
            assistant["voice"] = {"provider": "11labs", "voiceId": VOICE_ID,
                                  "model": "eleven_turbo_v2_5"}
        else:
            assistant["voice"] = {"provider": "vapi", "voiceId": VAPI_VOICE}
        # Latency / barge-in tuning so it feels snappy and natural on the call.
        assistant["startSpeakingPlan"] = {"waitSeconds": 0.4}
        assistant["stopSpeakingPlan"] = {"numWords": 2}
        assistant["model"]["temperature"] = 0.4
        assistant["model"]["maxTokens"] = 220
        payload["assistant"] = assistant

    r = httpx.post(f"{BASE}/call/phone", headers=_auth(), json=payload, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    return {"call_id": data.get("id"), "status": data.get("status", "queued"),
            "control_url": (data.get("monitor") or {}).get("controlUrl")}


def get_call_status(call_id: str) -> dict:
    if not VAPI_API_KEY:
        return {"id": call_id, "status": "ended", "endedReason": "mock"}
    r = httpx.get(f"{BASE}/call/{call_id}", headers=_auth(), timeout=15.0)
    r.raise_for_status()
    return r.json()


def poll_until_ended(call_id: str, max_seconds: int = 300, interval: float = 3.0) -> dict:
    elapsed = 0.0
    while elapsed < max_seconds:
        info = get_call_status(call_id)
        if info.get("status") == "ended":
            return info
        time.sleep(interval)
        elapsed += interval
    return {"id": call_id, "status": "timeout", "endedReason": "poll_timeout"}


def say_into_call(control_url: str, message: str, end_after: bool = False) -> dict:
    """Inject a spoken line into a LIVE call (the pause -> ask Mom -> resume beat)."""
    if not control_url:
        return {"ok": False, "error": "no control_url"}
    r = httpx.post(control_url, json={"type": "say", "message": message,
                                      "endCallAfterSpoken": end_after}, timeout=10.0)
    return {"ok": r.status_code < 400, "status_code": r.status_code}
