"""One-shot: real consent-gated voice order test — a large pepperoni pizza, on Ruby's behalf.

Runs the actual ConsentEngine gate (authorize within Mom's $30 grant), then places a REAL Vapi
outbound call in Chantal's cloned ElevenLabs voice. Reconciles the env-name mismatch
(VAPI_TARGET_PHONE vs STEAD_TARGET_PHONE). Prints the call id + polls status.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestration-kit"))
sys.path.insert(0, str(ROOT / "agent" / "tools"))

# Load .env BEFORE importing phone (phone reads keys at import time).
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
# Reconcile: phone.py wants STEAD_TARGET_PHONE; the demo .env defines VAPI_TARGET_PHONE.
if not os.environ.get("STEAD_TARGET_PHONE") and os.environ.get("VAPI_TARGET_PHONE"):
    os.environ["STEAD_TARGET_PHONE"] = os.environ["VAPI_TARGET_PHONE"]

from consent_agent import ConsentEngine
import phone

OWNER, AGENT = "mom", "agent:ruby"
VENDOR, AMOUNT, ITEMS = "Tony's Pizza", 28.0, "one large pepperoni pizza"

c = ConsentEngine(owner=OWNER)
c.grant(OWNER, AGENT, "order:food", vendor=VENDOR, cap=30, payment="card-on-file")
decision = c.act(AGENT, "order:food", vendor=VENDOR, amount=AMOUNT)
print("CONSENT:", decision)
if decision["status"] != "done":
    print("HALTED — not dialing.")
    sys.exit(0)

number = os.environ.get("STEAD_TARGET_PHONE", "")
print(f"TARGET: {number} | VAPI key: {bool(os.environ.get('VAPI_API_KEY'))} "
      f"| phoneNumberId: {bool(os.environ.get('VAPI_PHONE_NUMBER_ID'))} "
      f"| voiceId: {(os.environ.get('STEAD_VOICE_ID') or '-')[:8]}")

system = (
    f"You are placing a food order by phone on behalf of Ruby, a young woman with cerebral palsy. "
    f"Order: {ITEMS}. Keep the total at or under ${AMOUNT:.0f}. Be warm, clear, and brief: greet, "
    f"order one large pepperoni pizza for pickup, confirm the total and pickup ETA, thank them, and "
    f"end the call. If the total would exceed ${AMOUNT:.0f}, say you need to check and do not commit."
)
opening = "Hi! I'd like to place a pickup order, please — one large pepperoni pizza."

res = phone.place_call(number, system, opening)
print("PLACE_CALL:", res)
if res.get("mock"):
    print("MOCK — Vapi keys not loaded; no real call placed.")
    sys.exit(0)

cid = res.get("call_id")
print(f"RINGING… call_id={cid}")
for _ in range(8):
    time.sleep(4)
    st = phone.get_call_status(cid)
    print("  status:", st.get("status"), st.get("endedReason") or "")
    if st.get("status") == "ended":
        print("SUMMARY:", st.get("summary") or "(no summary field)")
        break
