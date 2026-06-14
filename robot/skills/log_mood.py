#!/usr/bin/env python3
"""
log_mood — Innate/MARS skill that records how Ruby is feeling into Stead.

Call this WHENEVER Ruby says anything about her mood or how she's feeling. The skill
POSTs to Stead's /api/mars/sense, which writes a care_event (logged_by MARS) onto Ruby's
timeline and persists it to Neon — so the family sees, over time, what Ruby tells MARS.

skill_type is the file stem: "log_mood". Inputs map to execute() kwargs, e.g.:
    {"note": "I'm feeling pretty good, just a little tired", "mood": 0.6, "energy": "low"}

DEPLOY (hot-reloads):  scp robot/skills/log_mood.py jetson1@<ROBOT_IP>:~/skills/   (pw: goodbot)
STEAD_URL override:    export STEAD_URL=http://<laptop-ip>:8770   (default below)
"""

import json
import os
import urllib.error
import urllib.request

try:  # match whichever path this robot exposes
    from brain_client.skill_types import Skill, SkillResult
except ImportError:
    from brain_client.skills.types import Skill, SkillResult

DEFAULT_STEAD_URL = "http://10.103.115.110:8770"
REQUEST_TIMEOUT_S = 15

# Rough word -> 0..1 mood score, used when the model passes words instead of a number.
_WORDS = {
    "great": 0.9, "happy": 0.85, "good": 0.8, "okay": 0.6, "ok": 0.6, "fine": 0.6,
    "alright": 0.55, "meh": 0.45, "tired": 0.4, "low": 0.35, "down": 0.3, "sad": 0.25,
    "upset": 0.2, "bad": 0.2, "awful": 0.1, "terrible": 0.1,
}


class LogMood(Skill):
    """Record Ruby's mood / how she's feeling via Stead (POST /api/mars/sense)."""

    def __init__(self, logger):
        super().__init__(logger)
        self._cancelled = False
        self.stead_url = os.environ.get("STEAD_URL", DEFAULT_STEAD_URL).rstrip("/")

    @property
    def name(self):
        return "log_mood"

    def guidelines(self):
        return (
            "Record how Ruby is feeling. Call this WHENEVER Ruby shares anything "
            "about her mood, energy, pain, or how her day is going — even in passing. "
            "Pass 'note' with what she actually said (her words). Optionally pass "
            "'mood' (0.0 sad to 1.0 happy), 'energy' (e.g. 'low'/'good'), and "
            "'activity'. This saves it to her care timeline so her family can see it."
        )

    def _score(self, mood):
        if mood is None:
            return 0.6
        try:
            v = float(mood)
            return max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            return _WORDS.get(str(mood).strip().lower(), 0.6)

    def execute(self, note: str, mood=None, activity: str = "", energy: str = ""):
        """
        Save Ruby's feeling to Stead.

        Args:
            note: What Ruby said about how she feels (her words). Required.
            mood: 0.0 (sad) .. 1.0 (happy), or a feeling word. Defaults to neutral.
            activity: optional, what she's doing.
            energy: optional, e.g. 'low', 'good'.

        Returns:
            (result_message, SkillResult)
        """
        self._cancelled = False
        if not note:
            return "Nothing to record — no note about how Ruby feels.", SkillResult.FAILURE

        payload = {
            "mood": self._score(mood),
            "note": note,
            "activity": activity or "check-in",
            "energy": energy or "",
        }
        endpoint = f"{self.stead_url}/api/mars/sense"
        self.logger.info(f"[log_mood] POST {endpoint} payload={payload}")
        self._send_feedback("Saving how you're feeling to your care log...")

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8")
            except Exception:
                raw = ""
            msg = self._parse(raw).get("message") or f"Stead returned HTTP {e.code}."
            self.logger.error(f"[log_mood] HTTP {e.code}: {msg}")
            return f"Couldn't save it: {msg}", SkillResult.FAILURE
        except urllib.error.URLError as e:
            self.logger.error(f"[log_mood] Could not reach Stead at {endpoint}: {e.reason}")
            return f"Couldn't reach the care log at {self.stead_url}: {e.reason}", SkillResult.FAILURE
        except Exception as e:  # noqa: BLE001 — never crash the runner
            self.logger.error(f"[log_mood] Unexpected error: {e}")
            return f"Couldn't save it: {e}", SkillResult.FAILURE

        body = self._parse(raw)
        if body.get("ok"):
            self._send_feedback("Got it — I've noted how you're feeling.")
            return "Saved Ruby's check-in to her care timeline.", SkillResult.SUCCESS
        return f"Care log responded: {body.get('message') or raw or '(no message)'}", SkillResult.FAILURE

    def cancel(self):
        self._cancelled = True
        self.logger.info("[log_mood] Cancel requested (save is an atomic POST).")
        return "Logging a mood is a single atomic request and cannot be cancelled once sent."

    @staticmethod
    def _parse(raw: str) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"message": str(parsed)}
        except (ValueError, TypeError):
            return {"message": raw}
