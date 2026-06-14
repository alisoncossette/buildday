#!/usr/bin/env python3
"""
order_pizza — Innate/MARS skill that orders pizza through the Stead consent layer.

When executed, this skill POSTs the order to Stead's MARS API. Stead is the
consent/guardrail layer: it decides whether the action proceeds (done), is
blocked (halted), or requires a human OK first (needs_consent). Every order is
persisted to Neon and audited server-side.

The skill_type is the file stem: "order_pizza". Inputs are passed to execute()
as keyword args matching the parameter names below, e.g.:
    {"item": "pepperoni pizza", "amount": 28}

------------------------------------------------------------------------------
DEPLOY (hot-reloads into the running brain_client — no rebuild/restart needed):
    scp robot/skills/order_pizza.py jetson1@<ROBOT_IP>:~/skills/
    # password: goodbot
    # (e.g. ROBOT_IP=10.103.115.110 or the robot's Tailscale IP)

TEST via rosbridge /execute_skill (skill_type MUST match this filename stem):
    # On a machine that can reach rosbridge_server (ws://<ROBOT_IP>:9090):
    wscat -c ws://<ROBOT_IP>:9090
    > {"op":"call_service","service":"/execute_skill",
       "args":{"skill_type":"order_pizza",
               "inputs_json":"{\"item\":\"pepperoni pizza\",\"amount\":28}"}}

    # Or from the robot shell, drive it through the brain_client skill runner /
    # the chat agent ("order a pepperoni pizza"). The skill returns a message +
    # status (done / halted / needs_consent) that the robot can speak back.

STEAD SERVER (this machine):
    local:     http://localhost:8770
    LAN:       http://10.103.115.110:8770
    Tailscale: http://100.122.132.58:8770   (default for robot-side POSTs)
Override on the robot with:  export STEAD_URL=http://<host>:8770
------------------------------------------------------------------------------
"""

import json
import os
import urllib.error
import urllib.request

try:  # match whichever path this robot exposes (skill_types is the shim used by ~/skills/*.py)
    from brain_client.skill_types import Skill, SkillResult
except ImportError:
    from brain_client.skills.types import Skill, SkillResult

# Robot is on the LAN with the laptop running Stead (laptop LAN IP 10.103.115.110:8770).
# Override on the robot with: export STEAD_URL=http://<laptop-ip>:8770
DEFAULT_STEAD_URL = "http://10.103.115.110:8770"
DEFAULT_VENDOR = "Tony's Pizza"
REQUEST_TIMEOUT_S = 15


class OrderPizza(Skill):
    """Order pizza via the Stead MARS consent layer (POST /api/mars/order)."""

    def __init__(self, logger):
        super().__init__(logger)
        self._cancelled = False
        self.stead_url = os.environ.get("STEAD_URL", DEFAULT_STEAD_URL).rstrip("/")

    @property
    def name(self):
        return "order_pizza"

    def guidelines(self):
        return (
            "Order food (pizza) on the user's behalf through the Stead consent "
            "layer. Provide 'item' (what to order, e.g. 'pepperoni pizza') and "
            "'amount' (the dollar cost as a number). Optionally provide 'vendor' "
            f"(defaults to '{DEFAULT_VENDOR}'). Stead may approve the order "
            "(done), block it (halted), or require the human to confirm first "
            "(needs_consent) — relay the spoken message back to the user."
        )

    def execute(self, item: str, amount: float = 0, vendor: str = DEFAULT_VENDOR):
        """
        Place a pizza order through Stead.

        Args:
            item: What to order, e.g. "pepperoni pizza". Sent as the order items.
            amount: Dollar cost of the order (number).
            vendor: The vendor/restaurant name. Defaults to "Tony's Pizza".

        Returns:
            tuple: (result_message, SkillResult) where SkillResult is SUCCESS for
                   an approved order (done), and FAILURE for halted / needs_consent
                   / transport errors. The human-facing wording is in the message.
        """
        self._cancelled = False

        if not item:
            return "No item specified to order.", SkillResult.FAILURE

        vendor = vendor or DEFAULT_VENDOR
        try:
            amount_val = float(amount) if amount is not None else 0.0
        except (TypeError, ValueError):
            amount_val = 0.0

        endpoint = f"{self.stead_url}/api/mars/order"
        payload = {"vendor": vendor, "amount": amount_val, "items": item}

        self.logger.info(
            f"[order_pizza] POST {endpoint} vendor='{vendor}' "
            f"amount={amount_val} items='{item}'"
        )
        self._send_feedback(f"Ordering {item} from {vendor} (${amount_val:.2f})...")

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8")
            except Exception:
                raw = ""
            body = self._parse(raw)
            msg = body.get("message") or f"Stead returned HTTP {e.code}."
            self.logger.error(f"[order_pizza] HTTP {e.code}: {msg}")
            return f"Order failed: {msg}", SkillResult.FAILURE
        except urllib.error.URLError as e:
            self.logger.error(f"[order_pizza] Could not reach Stead at {endpoint}: {e.reason}")
            return (
                f"Could not reach the ordering service at {self.stead_url}: {e.reason}",
                SkillResult.FAILURE,
            )
        except Exception as e:  # noqa: BLE001 — never let a skill crash the runner
            self.logger.error(f"[order_pizza] Unexpected error: {e}")
            return f"Order failed: {e}", SkillResult.FAILURE

        body = self._parse(raw)
        status = (body.get("status") or "").lower()
        # Prefer the spoken phrasing for the robot's voice; fall back to message.
        spoken = body.get("spoken") or body.get("message") or ""
        message = body.get("message") or spoken or raw or "(no message)"

        self.logger.info(f"[order_pizza] Stead status='{status}' message='{message}'")
        if spoken:
            self._send_feedback(spoken)

        out = spoken or message
        if status == "done":
            return f"Order placed: {out}", SkillResult.SUCCESS
        if status == "needs_consent":
            return f"Order needs consent: {out}", SkillResult.FAILURE
        if status == "halted":
            return f"Order halted: {out}", SkillResult.FAILURE
        # Unknown / missing status — surface whatever Stead said.
        return f"Order response ({status or 'unknown'}): {out}", SkillResult.FAILURE

    def cancel(self):
        """
        Cancel the order. Placing an order is a single atomic HTTP POST, so once
        it is in flight it cannot be recalled here — cancellation/refund is a
        Stead-side concern. This satisfies the Skill interface.
        """
        self._cancelled = True
        self.logger.info("[order_pizza] Cancel requested (order POST is atomic).")
        return "Order is an atomic request and cannot be cancelled once sent."

    @staticmethod
    def _parse(raw: str) -> dict:
        """Best-effort JSON parse; return {} on anything non-JSON."""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"message": str(parsed)}
        except (ValueError, TypeError):
            return {"message": raw}
