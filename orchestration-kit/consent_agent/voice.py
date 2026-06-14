"""Stead — the agent's voice THROUGH MARS (Tier 5). SUBJECT to PRD.md.

The agent vocalizes its negotiation and consent moments so the room HEARS them. In offline mode the
Vocalizer records utterances instead of driving the robot, so 'it speaks' is machine-checkable
without putting the physical robot on the critical path.

For the LIVE demo, set ROSBRIDGE_URL (e.g. ws://192.168.1.50:9090) and the Vocalizer will publish the
text to MARS's TTS topic /brain/tts (std_msgs/String) so the robot SPEAKS it; override with
MARS_SAY_TOPIC. If no robot is configured — or the websocket can't be reached — it degrades to
recording only and NEVER crashes. Either way every utterance is captured in `spoken` (the
offline-checkable transcript).
"""
import json
import os


def _rosbridge_say(url, topic, text, timeout=5.0):
    """Best-effort: advertise + publish a std_msgs/String to MARS's TTS `topic` over rosbridge so the
    robot speaks. Returns True on a successful send, False otherwise. Never raises — voice must never
    break a consent decision."""
    try:
        from websocket import create_connection  # websocket-client (optional dep, imported lazily)
    except Exception:
        return False
    ws = None
    try:
        ws = create_connection(url, timeout=timeout)
        ws.send(json.dumps({"op": "advertise", "topic": topic, "type": "std_msgs/msg/String"}))
        ws.send(json.dumps({"op": "publish", "topic": topic, "msg": {"data": text}}))
        return True
    except Exception:
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


class Vocalizer:
    def __init__(self, sink="mars", rosbridge_url=None, topic=None):
        self.sink = sink
        self.rosbridge_url = rosbridge_url or os.environ.get("ROSBRIDGE_URL")
        self.topic = topic or os.environ.get("MARS_SAY_TOPIC", "/brain/tts")
        self._spoken = []

    def say(self, text) -> dict:
        """Speak `text` through MARS's TTS topic over rosbridge if ROSBRIDGE_URL is set (else just
        record). Returns {'sink', 'text', 'live'} where 'live' is True iff it reached the robot."""
        live = False
        if self.rosbridge_url:
            live = _rosbridge_say(self.rosbridge_url, self.topic, text)
        utterance = {"sink": self.sink, "text": text, "live": live}
        self._spoken.append(utterance)
        return dict(utterance)

    @property
    def spoken(self) -> list:
        """Ordered list of utterances emitted (the offline-checkable transcript)."""
        return list(self._spoken)
