"""Stead — the care-companion PWA, wired to the REAL consent engine + a REAL data store.

Serves an installable PWA (manifest + service worker) at http://localhost:8770. Ruby's day comes from a
SQLite database (app/store.py), NOT hardcoded strings. Every consent decision is persisted to a local audit
(always-on observability); Langfuse is the optional external mirror (set LANGFUSE_* to enable).

Run:  python app/server.py     (stdlib only; consent core must be implemented)
"""
import json
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
APP = Path(__file__).resolve().parent
STATIC = APP / "static"
sys.path.insert(0, str(ROOT / "orchestration-kit"))
sys.path.insert(0, str(APP))


def _load_env(p):
    """Minimal .env loader (no dependency). Real environment wins over the file."""
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env(ROOT / ".env")

from consent_agent import ConsentEngine, StoreBackend
from consent_agent.trace import tracer
from consent_agent.voice import Vocalizer
import store

sys.path.insert(0, str(ROOT / "agent" / "tools"))
try:
    import phone as phone_tool  # real Vapi outbound call (mocks itself when keys are absent)
except Exception:
    phone_tool = None

PORT = 8770
OWNER, AGENT, PCA, DOC = "mom", "agent:ruby", "jane:pca", "doc:smith"
TODAY = "2026-06-13"

store.init_db()
# THE SHARED BRAIN: grants + pending requests live in store.py, so a cap change / revoke here is
# seen by the agent (a separate process) on its next check. InMemoryBackend stays the pytest default.
consent = ConsentEngine(owner=OWNER, backend=StoreBackend(store))
# Idempotent baseline grants (create_grant merges, so re-running the server is safe).
consent.grant(OWNER, AGENT, "order:food", vendor="Tony's Pizza", cap=30, payment="card-on-file",
              card=os.environ.get("STEAD_CARD_NUMBER"), card_exp=os.environ.get("STEAD_CARD_EXP"),
              card_cvv=os.environ.get("STEAD_CARD_CVV"), card_zip=os.environ.get("STEAD_CARD_ZIP"))
consent.grant(OWNER, PCA, "care:read")
consent.grant(OWNER, DOC, "care:trends")  # Dr. Smith: longitudinal trends only, no daily handoff

# MARS voice (Vocalizer): real rosbridge if ROSBRIDGE_URL is set, else a recorded no-op.
mars = Vocalizer(sink="mars")

LABELS = {OWNER: "Mom", PCA: "Jane · PCA", AGENT: "Ruby's agent", DOC: "Dr. Smith"}
CTYPES = {".html": "text/html", ".json": "application/json", ".js": "text/javascript", ".svg": "image/svg+xml"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _can_view_today(actor):
    """The daily handoff is the owner + the on-shift PCA. The doctor is longitudinal-only (trends),
    so although care:trends technically subsumes care:today, we keep the doctor OUT of the daily view
    to match the pinned contract (doc:smith -> care:trends only)."""
    if actor == DOC:
        return False
    return consent.can_view(actor, "care:today")


def _can_view_trends(actor):
    """Trends are gated to the owner and the doctor only."""
    return actor == OWNER or (actor == DOC and consent.can_view(actor, "care:trends"))


def state(actor):
    can = _can_view_today(actor)
    grant = consent._backend.get_grant(AGENT, "order:food")
    day = store.day_summary(TODAY) if can else None
    return {
        "actor": actor, "label": LABELS.get(actor, actor),
        "can_view_today": can,
        "can_log": actor in (OWNER, PCA),
        "is_owner": actor == OWNER,
        "day": day,
        "grant": ({"vendor": grant.get("vendor"), "cap": grant.get("cap")} if grant else None),
    }


def _ctx(ev, summ):
    lines = [f"- {e['ts']} {e['title']}: {e['detail']} (by {e['logged_by']})" for e in ev]
    head = (f"Meds: {summ['meds']}. Meals: {summ['meals']}. Mood: {summ['mood']}. "
            f"Appetite trend: {summ['trend']}.")
    return head + "\n" + "\n".join(lines)


def claude_answer(q, ctx):
    """Use Claude when ANTHROPIC_API_KEY is set; otherwise return None and let the fallback speak."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        model = os.environ.get("STEAD_MODEL", "claude-sonnet-4-6")
        sysp = ("You are Stead, a warm, calm care companion talking to a family member about their elderly "
                "parent Ruby. Answer ONLY from the day's facts given. 2-4 short, reassuring, plain sentences. "
                "If something needs attention, say so gently and suggest one concrete next step. "
                "Never invent medical facts.")
        m = client.messages.create(model=model, max_tokens=300, system=sysp,
                                   messages=[{"role": "user", "content": f"Ruby's day:\n{ctx}\n\nFamily asks: {q}"}])
        return "".join(b.text for b in m.content if getattr(b, "type", "") == "text").strip()
    except Exception:
        return None


def fallback_answer(q, summ, ev):
    ql = q.lower()
    meds = next((e for e in ev if e["kind"] == "meds"), None)
    if any(w in ql for w in ("med", "pill", "medication")):
        return (f"Yes — Ruby took her {meds['title'].lower()} on time today ({meds['detail']}), logged by "
                f"{meds['logged_by']}." if meds else "No medications have been logged yet today.")
    if any(w in ql for w in ("eat", "food", "appetite", "hungry", "meal", "lunch", "dinner")):
        t = f" That's the third day her appetite has dipped." if summ["trend"] == "declining" else ""
        return (f"Her appetite was on the lighter side today — {summ['meals'].lower()}.{t} "
                f"Nothing alarming, but it's worth keeping an eye on. Want me to order an easy dinner she likes?")
    if any(w in ql for w in ("mood", "feel", "spirit", "happy", "sad")):
        return f"Mood-wise, {summ['mood'].lower()}. Jane noted she kept to herself this afternoon."
    if any(w in ql for w in ("worry", "worried", "okay", "ok", "concern", "doctor")):
        flag = "Her eating has been trending down for a few days" if summ["trend"] == "declining" else "Things look steady"
        return (f"{flag}, and her mood was a little low today, but meds are on track and she's resting. "
                f"If you'd like, I can draft a short note for her doctor or set a reminder to check in tomorrow.")
    # default: a warm recap of the day
    return (f"Ruby had a fairly quiet day. She took her medications on time, her appetite was {summ['meals'].lower()}, "
            f"and her mood was {summ['mood'].lower()}. "
            + ("Her eating has been slipping a little this week — want me to help with dinner?"
               if summ["trend"] == "declining" else "Overall a calm, steady day."))


def suggestions(summ):
    s = ["Did she take her meds?", "Is she eating enough?", "How's her mood?"]
    s.append("Order an easy dinner" if summ and summ["trend"] == "declining" else "Should I be worried?")
    return s


def _grant_cap():
    """Current live cap for Ruby's agent (None if no grant)."""
    g = consent._backend.get_grant(AGENT, "order:food")
    return (g.get("cap") if g else None)


def _requests_view():
    """Owner-facing list of grant-up asks: {id,requester,vendor,amount,cap,status}."""
    out = []
    for r in consent.requests:
        p = r.get("params") or {}
        out.append({
            "id": r.get("id"),
            "requester": r.get("requester"),
            "vendor": p.get("vendor"),
            "amount": p.get("amount"),
            "cap": p.get("cap"),
            "status": r.get("status"),
        })
    return out


def _doc_answer(q):
    """Doctor / longitudinal Q&A grounded ONLY in stored trends + history (ZERO PHI, all synthetic)."""
    series = store.trend_series()
    summary = store.trend_summary(series)
    ctx = (f"Longitudinal trends for Ruby (synthetic, {len(series['appetite'])} days):\n{summary}\n"
           f"Appetite points: {[p['value0to1'] for p in series['appetite']]}\n"
           f"Mood points: {[p['score0to1'] for p in series['mood']]}")
    ans = claude_answer(q, ctx)
    if ans:
        return ans
    return summary


def _is_longitudinal(q):
    ql = (q or "").lower()
    return any(w in ql for w in ("trend", "over time", "this week", "past week", "weeks",
                                 "lately", "been feeling", "has she been", "longitudinal",
                                 "declining", "history", "month"))


def speak_through_mars(text):
    """Speak `text` through MARS (real rosbridge if ROSBRIDGE_URL is set, else recorded). Never raises."""
    try:
        with tracer.span("mars.say", actor=AGENT, text=text):
            u = mars.say(text)
        return u
    except Exception:
        return {"sink": "mars", "text": text, "live": False}


def _place_order_call(vendor, amount, items="a pepperoni pizza"):
    """Real outbound voice order, on Ruby's behalf — runs in a thread so the HTTP response is instant.
    Only ever reached after consent.act() returned 'done' AND VAPI_TARGET_PHONE is set."""
    target = os.environ.get("VAPI_TARGET_PHONE")
    if not (phone_tool and target):
        return
    addr = os.environ.get("STEAD_DELIVERY_ADDRESS", "42 Elm Street, Williston")
    cb = os.environ.get("STEAD_CALLBACK_PHONE", "802-555-1212")
    # The card lives IN the consent grant (Bolo) — revoking the grant revokes the card. Env is a fallback.
    g = consent._backend.get_grant(AGENT, "order:food") or {}
    card = g.get("card") or os.environ.get("STEAD_CARD_NUMBER")
    pay = ""
    if card:
        exp = g.get("card_exp") or os.environ.get("STEAD_CARD_EXP", "")
        cvv = g.get("card_cvv") or os.environ.get("STEAD_CARD_CVV", "")
        zip_ = g.get("card_zip") or os.environ.get("STEAD_CARD_ZIP")
        pay = (f" When they ASK for payment, pay by credit card — number {card}, expiration {exp}, "
               f"security code {cvv}{', billing zip ' + zip_ if zip_ else ''}. Give these ONLY when asked.")
    sysp = (f"You are Stead, a warm assistant ordering food by phone on behalf of Ruby. You want {items} "
            f"from {vendor} for delivery, total at or under ${amount:.0f}. After your opening line, LET THEM "
            f"LEAD — just answer their questions naturally as they come. Have these ready to give WHEN ASKED: "
            f"the name is 'Ruby'; the delivery address is '{addr}'; the callback number is {cb}.{pay} "
            f"Confirm the order and an ETA, thank them, and end the call. Be warm and brief. If the total "
            f"would exceed ${amount:.0f}, say you need to check and do not commit.")
    first = f"Hi! I'd like to order {items}, please."
    with tracer.span("vapi.call", actor=AGENT, scope="order:food", vendor=vendor, amount=amount):
        try:
            res = phone_tool.place_call(target, sysp, first)
            store.write_audit(AGENT, "call", "order:food", res.get("status", "queued"),
                              vendor=vendor, amount=amount, ts=_now())
        except Exception as e:
            store.write_audit(AGENT, "call", "order:food", "error", reason=str(e)[:120],
                              vendor=vendor, amount=amount, ts=_now())


def _run_order(vendor, amount, items="a pizza"):
    """Shared order path for /api/order and /api/mars/order. Returns a dict per the contract:
      within grant   -> {status:"done", message}
      over cap       -> {status:"needs_consent", request:{id,vendor,amount,cap},
                         message:"Stead is asking to raise the cap to $AMOUNT"}
      no grant/revoked or wrong vendor -> {status:"halted", message, reason}
    OVER CAP parks a pending consent request (request_access) instead of halting outright."""
    with tracer.span("api.order", actor=AGENT, scope="order:food", vendor=vendor, amount=amount):
        d = consent.act(AGENT, "order:food", vendor=vendor, amount=amount)
    store.write_audit(AGENT, "order", "order:food", d["status"], reason=d.get("reason", ""),
                      vendor=vendor, amount=amount, ts=_now())

    if d["status"] == "done":
        # Log the order to Ruby's day immediately, so it shows in the app whether or not a call fires.
        store.add_event(TODAY, datetime.now().strftime("%H:%M"), "order", f"Ordered {items}",
                        f"${amount:.0f} at {vendor}, on the card Mom shared (within her grant).",
                        None, "Ruby's agent")
        if phone_tool and os.environ.get("VAPI_TARGET_PHONE"):
            threading.Thread(target=_place_order_call, args=(vendor, amount, items), daemon=True).start()
            msg = (f"✅ Authorized ${amount:.0f} at {vendor} — \U0001f4de calling now in Ruby's "
                   f"voice to place the order.")
        else:
            msg = (f"✅ Authorized: ${amount:.0f} at {vendor}, on the card Mom shared. "
                   f"(Set VAPI_TARGET_PHONE in .env to ring a real phone.)")
        return {"status": "done", "message": msg}

    # Over cap (a live grant exists for this vendor, just not enough headroom) -> ask to raise it.
    if d.get("reason") == "over cap":
        cap = _grant_cap()
        with tracer.span("api.order.request", actor=AGENT, scope="order:food",
                         vendor=vendor, amount=amount, cap=cap):
            req = consent.request_access(AGENT, "order:food", vendor=vendor, amount=amount, cap=amount)
        store.write_audit(AGENT, "request", "order:food", "pending", reason="over cap",
                          vendor=vendor, amount=amount, cap=cap, ts=_now())
        return {
            "status": "needs_consent",
            "request": {"id": req["id"], "vendor": vendor, "amount": amount, "cap": cap},
            "message": f"Stead is asking to raise the cap to ${amount:.0f}",
        }

    reason = d.get("reason", "outside the live grant")
    msg = f"\U0001f6d1 HALTED: {reason}. Ask Mom to adjust consent."
    return {"status": "halted", "message": msg, "reason": reason}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _actor(self):
        if "actor=" in self.path:
            return unquote(self.path.split("actor=")[1].split("&")[0])
        return OWNER

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            return self._send(200, state(self._actor()))
        if path == "/api/timeline":
            actor = self._actor()
            if not _can_view_today(actor):
                return self._send(200, {"scoped_out": True, "events": []})
            return self._send(200, {"events": store.timeline(TODAY)})
        if path == "/api/audit":
            return self._send(200, {"audit": store.recent_audit()})
        if path == "/api/requests":
            actor = self._actor()
            # Owner sees pending grant-up asks; others see an empty list (not their decision to make).
            if actor != OWNER:
                return self._send(200, {"requests": []})
            return self._send(200, {"requests": _requests_view()})
        if path == "/api/appointments":
            actor = self._actor()
            if not (actor in (OWNER, PCA) or actor == DOC):
                return self._send(200, {"appointments": []})
            return self._send(200, {"appointments": store.list_appointments()})
        if path == "/api/trends":
            actor = self._actor()
            if not _can_view_trends(actor):
                return self._send(200, {"ok": False, "scoped_out": True})
            series = store.trend_series()
            return self._send(200, {"ok": True, "trends": series,
                                    "summary": store.trend_summary(series)})
        name = "index.html" if path == "/" else path.lstrip("/")
        f = STATIC / name
        if f.exists() and f.is_file():
            return self._send(200, f.read_bytes(), CTYPES.get(f.suffix, "application/octet-stream"))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        if self.path == "/api/ask":
            actor = body.get("actor", OWNER)
            q = (body.get("q") or "").strip()
            # Doctor (or any longitudinal question) is answered ONLY from stored trends/history.
            if actor == DOC or (_is_longitudinal(q) and _can_view_trends(actor)):
                if not _can_view_trends(actor):
                    return self._send(200, {"answer": "Ruby's trends aren't in this role's scope.",
                                            "suggestions": []})
                with tracer.span("ask.longitudinal", actor=actor, question=q):
                    ans = _doc_answer(q)
                return self._send(200, {"answer": ans, "suggestions": [
                    "How has her appetite trended?", "Any missed medications?",
                    "How is her mood over the last few weeks?"]})
            if not _can_view_today(actor):
                return self._send(200, {"answer": "Ruby's daily details aren't in this role's scope.", "suggestions": []})
            ev, summ = store.timeline(TODAY), store.day_summary(TODAY)
            with tracer.span("ask", actor=actor, question=q):
                ans = claude_answer(q, _ctx(ev, summ)) or fallback_answer(q, summ, ev)
            return self._send(200, {"answer": ans, "suggestions": suggestions(summ)})

        if self.path == "/api/checkin":
            actor = body.get("actor", OWNER)
            if actor not in (OWNER, PCA):
                return self._send(403, {"error": "This role cannot log care notes."})
            store.add_event(TODAY, datetime.now().strftime("%H:%M"), body.get("kind", "note"),
                            body.get("title", "Note"), body.get("detail", ""),
                            body.get("value"), LABELS.get(actor, actor))
            return self._send(200, {"ok": True})

        if self.path == "/api/cap":
            amount = float(body.get("amount", 30))
            with tracer.span("api.cap", actor=OWNER, scope="order:food", cap=amount):
                if consent._backend.has_grant(AGENT, "order:food"):
                    consent.update_grant(OWNER, AGENT, "order:food", cap=amount)
                else:
                    consent.grant(OWNER, AGENT, "order:food", vendor="Tony's Pizza", cap=amount, payment="card-on-file")
            store.write_audit(OWNER, "cap", "order:food", "updated", cap=amount, ts=_now())
            return self._send(200, {"ok": True, "cap": amount})

        if self.path == "/api/revoke":
            with tracer.span("api.revoke", actor=OWNER, scope="order:food"):
                consent.revoke(OWNER, AGENT, "order:food")
            store.write_audit(OWNER, "revoke", "order:food", "revoked", ts=_now())
            return self._send(200, {"ok": True})

        if self.path == "/api/order":
            vendor = body.get("vendor", "Tony's Pizza")
            amount = float(body.get("amount", 0))
            return self._send(200, _run_order(vendor, amount))

        # --- Consent requests: owner approves/denies grant-up asks (shared brain) ---
        if self.path == "/api/requests/approve":
            actor = body.get("actor", OWNER)
            if actor != OWNER:
                return self._send(403, {"error": "Only Mom can approve consent."})
            rid = body.get("id")
            overrides = {}
            if body.get("cap") is not None:
                overrides["cap"] = float(body["cap"])
            try:
                with tracer.span("api.approve", actor=OWNER, scope="order:food", request=rid):
                    rec = consent.approve_request(OWNER, rid, **overrides)
            except (ValueError, KeyError):
                return self._send(404, {"error": "unknown request"})
            grant = consent._backend.get_grant(AGENT, "order:food")
            store.write_audit(OWNER, "approve", "order:food", "approved",
                              cap=(grant or {}).get("cap"), vendor=(grant or {}).get("vendor"),
                              ts=_now())
            return self._send(200, {"ok": True, "grant": grant})

        if self.path == "/api/requests/deny":
            actor = body.get("actor", OWNER)
            if actor != OWNER:
                return self._send(403, {"error": "Only Mom can deny consent."})
            rid = body.get("id")
            try:
                with tracer.span("api.deny", actor=OWNER, scope="order:food", request=rid):
                    consent.deny_request(OWNER, rid)
            except (ValueError, KeyError):
                return self._send(404, {"error": "unknown request"})
            store.write_audit(OWNER, "deny", "order:food", "denied", ts=_now())
            return self._send(200, {"ok": True})

        # --- Appointments (mom/jane may add; doctor/owner/pca may view) ---
        if self.path == "/api/appointments":
            actor = body.get("actor", OWNER)
            if actor not in (OWNER, PCA):
                return self._send(403, {"error": "This role cannot add appointments."})
            aid = store.add_appointment(
                body.get("date", TODAY), body.get("time", ""), body.get("title", "Appointment"),
                body.get("kind", "care"), body.get("who", ""), body.get("notes", ""))
            return self._send(200, {"ok": True, "id": aid})

        # --- MARS: Ruby's robot sensing + voice ---
        if self.path == "/api/mars/sense":
            detail_bits = []
            for k in ("mood", "note", "activity", "energy"):
                if body.get(k) not in (None, ""):
                    detail_bits.append(f"{k}: {body.get(k)}")
            detail = "; ".join(detail_bits) or "Ruby checked in."
            val = body.get("mood")
            try:
                val = float(val) if val is not None else None
            except (TypeError, ValueError):
                val = None
            ts = datetime.now().strftime("%H:%M")
            store.add_event(TODAY, ts, "mood", "Ruby checked in with MARS", detail, val, "MARS")
            ev = {"ts": ts, "kind": "mood", "title": "Ruby checked in with MARS",
                  "detail": detail, "value": val, "logged_by": "MARS"}
            store.write_audit("MARS", "sense", "care:today", "logged", reason=detail[:120], ts=_now())
            return self._send(200, {"ok": True, "event": ev})

        if self.path == "/api/mars/ask":
            question = (body.get("question") or "Hi Ruby, how are you feeling today?").strip()
            u = speak_through_mars(question)
            store.write_audit("MARS", "speak", "care:today", "spoken", reason=question[:120], ts=_now())
            return self._send(200, {"ok": True, "spoken": u})

        if self.path == "/api/mars/order":
            vendor = body.get("vendor", "Tony's Pizza")
            amount = float(body.get("amount", 0))
            items = body.get("items") or "a pizza"
            res = _run_order(vendor, amount, items=items)
            # MARS narrates the moment out loud so the room hears it. On 'done' the real Vapi call has
            # already been kicked off inside _run_order (_place_order_call); we announce it as we dial.
            if res["status"] == "done":
                line = (f"Calling {vendor} now to order {items} for Ruby, up to ${amount:.0f}.")
            elif res["status"] == "needs_consent":
                line = (f"That's ${amount:.0f} at {vendor}, over the ${_grant_cap() or 0:.0f} limit. "
                        f"I'm asking Mom to approve before I order.")
            else:
                line = f"I can't order from {vendor} right now — {res.get('reason', 'consent is needed')}."
            spoken = speak_through_mars(line)
            res["spoken"] = spoken
            return self._send(200, res)

        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"Stead PWA -> http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
