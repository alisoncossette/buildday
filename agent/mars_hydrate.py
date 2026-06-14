"""
mars_hydrate.py — Connect MARS (Innate robot) to Stead so the robot HYDRATES the app's live data.

WHAT IT DOES
  1. Connects to the robot's rosbridge_server over WebSocket (ROSBRIDGE_URL).
  2. Has MARS ASK Ruby how she's feeling: publishes to /brain/tts
     "Hi Ruby, how are you feeling today?" (the room hears it).
  3. Derives a SENSED state (mood / energy / activity / note):
       - Robot reachable  -> a light perception/placeholder pass (battery + any
         chat reply heard on /brain/chat_out) shapes the sensed mood.
       - Robot NOT reachable -> gracefully falls back to a sensible MOCKED sensed
         mood so the demo ALWAYS produces a hydration event.
  4. POSTs to  STEAD_URL/api/mars/sense  {mood, note, activity, energy}
     so it lands on the app timeline as  logged_by = MARS  (persisted to Neon, audited).

WHY
  The whole point is a visible, live data-flow: a real robot's sensing shows up in
  Ruby's care timeline in the app, attributed to MARS — never silently failing.

----------------------------------------------------------------------------------
RUN

  # From the robot's network (or your laptop on the same LAN / Tailscale):
  pip install websocket-client          # OPTIONAL — falls back to mock if missing
  python agent/mars_hydrate.py

ENV
  ROSBRIDGE_URL   robot rosbridge, e.g.  ws://10.103.115.110:9090   (default below)
  STEAD_URL       Stead server base URL.  Default: Tailscale http://100.122.132.58:8770
                  Other ways to reach the SAME server (runs on the demo machine):
                    localhost   http://localhost:8770
                    LAN         http://10.103.115.110:8770
                    Tailscale   http://100.122.132.58:8770

EXAMPLES
  ROSBRIDGE_URL=ws://10.103.115.110:9090 STEAD_URL=http://localhost:8770 python agent/mars_hydrate.py
  python agent/mars_hydrate.py            # uses defaults; mocks the sense if robot is offline

NOTES
  - stdlib urllib for the HTTP POST (no extra deps required for the Stead side).
  - websocket-client is OPTIONAL: if it isn't installed or the robot is unreachable,
    we mock the sensed mood and STILL hydrate the app so the demo always works.
  - Robot-side companion endpoints on the SAME Stead server:
      POST /api/mars/order  {vendor,amount,items} -> {status:done|halted|needs_consent,message,spoken}
      POST /api/mars/sense  {mood,note,activity,energy} -> care_event logged_by MARS
----------------------------------------------------------------------------------
"""
import json
import os
import random
import sys
import time
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

# --- Config (env-overridable) -------------------------------------------------
ROSBRIDGE_URL = os.environ.get("ROSBRIDGE_URL", "ws://10.103.115.110:9090")
STEAD_URL = os.environ.get("STEAD_URL", "http://100.122.132.58:8770").rstrip("/")
QUESTION = "Hi Ruby, how are you feeling today?"

# Topics (exact strings — a typo is a silent failure).
TTS_TOPIC = "/brain/tts"
CHAT_OUT_TOPIC = "/brain/chat_out"
BATTERY_TOPIC = "/battery_state"

CONNECT_TIMEOUT = 5      # seconds to open the WS
LISTEN_WINDOW = 4        # seconds to listen for a reply / battery after asking


def log(msg):
    print(f"[mars_hydrate] {msg}", flush=True)


# --- websocket-client is OPTIONAL --------------------------------------------
try:
    import websocket  # type: ignore  (the `websocket-client` package)
    HAVE_WS = True
except Exception:
    websocket = None
    HAVE_WS = False


def _rb(op, **kw):
    """Build a rosbridge protocol frame."""
    return json.dumps({"op": op, **kw})


def robot_ask_and_sense():
    """Open rosbridge, SPEAK the question on /brain/tts, then listen briefly for any
    chat reply + battery to shape a sensed state.

    Returns a sense dict on success, or None if the robot is unreachable / ws missing
    (the caller then falls back to a mock so the demo never breaks).
    """
    if not HAVE_WS:
        log("websocket-client not installed -> will mock the sensed mood.")
        return None

    log(f"connecting to rosbridge at {ROSBRIDGE_URL} ...")
    try:
        ws = websocket.create_connection(ROSBRIDGE_URL, timeout=CONNECT_TIMEOUT)
    except Exception as e:
        log(f"robot UNREACHABLE ({e.__class__.__name__}: {e}) -> will mock the sensed mood.")
        return None

    heard_text = None
    battery_pct = None
    try:
        ws.settimeout(CONNECT_TIMEOUT)

        # 1) Ask Ruby out loud.
        ws.send(_rb("advertise", topic=TTS_TOPIC, type="std_msgs/msg/String"))
        ws.send(_rb("publish", topic=TTS_TOPIC, msg={"data": QUESTION}))
        log(f'MARS said -> "{QUESTION}"')

        # 2) Subscribe to anything that helps us perceive a state.
        ws.send(_rb("subscribe", topic=CHAT_OUT_TOPIC, throttle_rate=200, queue_length=1))
        ws.send(_rb("subscribe", topic=BATTERY_TOPIC, throttle_rate=1000, queue_length=1))

        # 3) Listen for a short window.
        deadline = time.time() + LISTEN_WINDOW
        while time.time() < deadline:
            try:
                ws.settimeout(max(0.2, deadline - time.time()))
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            if frame.get("op") != "publish":
                continue
            topic, msg = frame.get("topic"), frame.get("msg") or {}
            if topic == CHAT_OUT_TOPIC and msg.get("data"):
                heard_text = str(msg["data"]).strip()
                log(f'heard Ruby (chat_out) -> "{heard_text}"')
            elif topic == BATTERY_TOPIC:
                pct = msg.get("percentage")
                if isinstance(pct, (int, float)):
                    battery_pct = pct if pct > 1 else pct * 100.0
    finally:
        try:
            ws.close()
        except Exception:
            pass

    return perceive(heard_text=heard_text, battery_pct=battery_pct)


def perceive(heard_text=None, battery_pct=None):
    """Simple perception/placeholder: turn whatever we observed into a sensed state.
    This is intentionally lightweight for the demo — a real model would replace it.
    """
    mood, energy, activity = 0.6, "steady", "resting"
    note_bits = ["MARS sensed Ruby on the robot's check-in."]

    if heard_text:
        t = heard_text.lower()
        if any(w in t for w in ("good", "great", "fine", "happy", "well", "okay", "ok")):
            mood, energy = 0.8, "good"
        elif any(w in t for w in ("tired", "sleepy", "low", "down", "sad", "not great", "rough")):
            mood, energy = 0.35, "low"
        note_bits.append(f'she said: "{heard_text}"')
    else:
        note_bits.append("no verbal reply captured; reading ambient/robot signals.")

    if isinstance(battery_pct, (int, float)):
        note_bits.append(f"robot battery {battery_pct:.0f}%.")

    return {
        "mood": round(mood, 2),
        "note": " ".join(note_bits),
        "activity": activity,
        "energy": energy,
        "_source": "robot",
    }


def mock_sense():
    """A sensible MOCKED sensed mood so the demo ALWAYS produces a hydration event."""
    options = [
        {"mood": 0.7, "energy": "good", "activity": "chatting",
         "note": 'MARS check-in (mocked): Ruby smiled and said she felt pretty good today.'},
        {"mood": 0.55, "energy": "steady", "activity": "resting",
         "note": 'MARS check-in (mocked): Ruby was calm and resting, in decent spirits.'},
        {"mood": 0.4, "energy": "low", "activity": "quiet",
         "note": 'MARS check-in (mocked): Ruby seemed a little tired and kept to herself.'},
    ]
    s = random.choice(options)
    s["_source"] = "mock"
    log("using MOCKED sensed mood (robot offline) so the timeline still hydrates.")
    return s


def post_sense(sense):
    """POST the sensed state to Stead so it appears on the timeline as logged_by MARS."""
    url = f"{STEAD_URL}/api/mars/sense"
    payload = {k: sense[k] for k in ("mood", "note", "activity", "energy") if k in sense}
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, data=data, method="POST",
                             headers={"Content-Type": "application/json"})
    log(f"POST {url}  payload={payload}")
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        log(f"Stead accepted the hydration -> {body}")
        return body
    except HTTPError as e:
        log(f"Stead HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except URLError as e:
        log(f"could NOT reach Stead at {url} ({e.reason}). "
            f"Is the server running? Try STEAD_URL=http://localhost:8770")
    return None


def main():
    log("=== MARS -> Stead hydration starting ===")
    log(f"ROSBRIDGE_URL={ROSBRIDGE_URL}")
    log(f"STEAD_URL={STEAD_URL}")

    sense = robot_ask_and_sense()
    if sense is None:
        sense = mock_sense()

    src = sense.get("_source", "?")
    log(f"sensed state [{src}] -> mood={sense['mood']} energy={sense['energy']} "
        f"activity={sense['activity']}")

    body = post_sense(sense)
    if body and body.get("ok"):
        ev = body.get("event") or {}
        log("=== DONE: a care_event is now on Ruby's timeline, logged_by MARS ===")
        log(f'timeline event: [{ev.get("ts")}] {ev.get("title")} — {ev.get("detail")}')
        return 0

    log("=== FINISHED with errors: hydration POST did not confirm (see messages above) ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
