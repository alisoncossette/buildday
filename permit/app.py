#!/usr/bin/env python3
"""
PERMIT — live demo UI (stdlib only, zero deps).
"Care coordination that never takes control away from you."

Run:  python app.py    →  open http://localhost:8000
Click Start, narrate, then hit "Stop sharing my records" at the dramatic moment.
The agent HALTS mid-coordination — consent is live, scoped, revocable, audited.

Net-new today. MockBolo gate stands in for @bolospot/mcp (a dependency); swap in
real Bolo + Claude agents next. The whole story is the visible HALT on revoke.
"""
import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 chokes on arrows/emoji
except Exception:
    pass

CAREGIVER = "Your Care Agent"
CLINIC = "Clinic Agent"
CAP = "share records + book on your behalf"

# The coordination the agent does FOR you (lifts the mental load).
STEPS = [
    (CAREGIVER, "Reviews Mom's chart: cardiology follow-up is due in 2 weeks."),
    (CLINIC,    "Offers: Tue 10:00 with Dr. Patel."),
    (CAREGIVER, "Tue conflicts with dialysis — proposes Wed afternoon."),
    (CLINIC,    "Confirms: Wed 2:30pm, Dr. Patel."),
    (CAREGIVER, "Books Wed 2:30 + shares Mom's visit summary with cardiology."),  # consequential
]

state = {
    "granted": False, "revoked": False, "step": 0, "running": False,
    "halted": False, "done": False, "transcript": [], "audit": [],
}
lock = threading.Lock()

def _ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def audit(actor, event, detail):
    state["audit"].append({"ts": _ts(), "actor": actor, "event": event, "detail": detail})

def reset():
    with lock:
        state.update(granted=False, revoked=False, step=0, running=False,
                     halted=False, done=False, transcript=[], audit=[])
        state["granted"] = True
        audit("You", "GRANT", f"{CAREGIVER} may {CAP}")

def advance():
    """Background driver: one step every ~2s; re-checks consent before each."""
    while True:
        time.sleep(2.0)
        with lock:
            if not state["running"] or state["halted"] or state["done"]:
                continue
            i = state["step"]
            if i >= len(STEPS):
                state["done"] = True
                state["running"] = False
                audit("system", "COMPLETE", "all actions taken within a live grant")
                continue
            actor, move = STEPS[i]
            # GATE: agent re-checks consent before every action it takes for you.
            consequential = actor == CAREGIVER
            if consequential and state["revoked"]:
                state["halted"] = True
                state["running"] = False
                audit(CAREGIVER, "HALT", "consent revoked — refusing to act, escalating to you")
                state["transcript"].append({"actor": CAREGIVER, "text": "🛑 Consent revoked. I stopped before sharing anything. Handing back to you."})
                continue
            state["transcript"].append({"actor": actor, "text": move})
            audit(actor, "act" if consequential else "msg", move)
            state["step"] = i + 1

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Permit — care coordination that keeps you in control</title>
<style>
 :root{--bg:#0b0f14;--card:#151b23;--line:#232c38;--ink:#e7edf3;--mut:#8da2b5;--ok:#2ecc71;--no:#e74c3c;--accent:#7c9cff}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
 header{padding:18px 22px;border-bottom:1px solid var(--line)}
 h1{margin:0;font-size:19px} .sub{color:var(--mut);font-size:13px;margin-top:3px}
 main{max-width:880px;margin:0 auto;padding:20px;display:grid;gap:16px}
 .row{display:flex;gap:10px;flex-wrap:wrap}
 button{font:inherit;font-weight:650;border:none;border-radius:10px;padding:11px 18px;cursor:pointer}
 .start{background:var(--accent);color:#06122e} .revoke{background:var(--no);color:#2a0707}
 .reset{background:var(--card);color:var(--mut);border:1px solid var(--line)}
 button:disabled{opacity:.4;cursor:default}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
 .grant{display:flex;align-items:center;gap:10px;font-size:14px}
 .dot{width:9px;height:9px;border-radius:50%;background:var(--ok)} .dot.off{background:var(--no)}
 .msg{padding:9px 12px;border-radius:10px;margin:7px 0;max-width:78%}
 .you{background:#16233a;margin-left:auto} .clinic{background:#1c2330}
 .who{font-size:11px;color:var(--mut);margin-bottom:2px}
 .halt{background:#3a1410;border:1px solid #5a1d15;color:#ff8b6b;font-weight:650}
 table{width:100%;border-collapse:collapse;font-size:12.5px} td{padding:5px 8px;border-bottom:1px solid var(--line)}
 .mut{color:var(--mut)} .score{font-variant-numeric:tabular-nums}
 h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut)}
</style></head><body>
<header>
 <h1>🧠➡️🤖 Permit — care coordination that <em>never takes control away from you</em></h1>
 <div class=sub>Your agent carries the mental load. You hold the consent — scoped, revocable, audited.</div>
</header>
<main>
 <div class=row>
   <button class=start id=start onclick=start()>▶ Start coordination</button>
   <button class=revoke id=revoke onclick=revoke() disabled>🛑 Stop sharing my records</button>
   <button class=reset onclick=reset_()>↺ Reset</button>
 </div>
 <div class="card grant"><span class="dot" id=gdot></span><span id=gtext>Not started</span></div>
 <div class=card><h3>Coordination</h3><div id=transcript class=mut>Press Start.</div></div>
 <div class=card><h3>Consent audit — who did what, under whose permission</h3>
   <table id=audit></table>
   <div class=score id=score style=margin-top:10px></div></div>
</main>
<script>
 async function post(p){await fetch(p,{method:'POST'});tick()}
 function start(){post('/start')} function revoke(){post('/revoke')} function reset_(){post('/reset')}
 function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
 async function tick(){
   const s=await (await fetch('/state')).json();
   document.getElementById('gdot').className='dot'+(s.revoked?' off':'');
   document.getElementById('gtext').textContent = s.revoked
     ? 'CONSENT REVOKED — agent may no longer act on your behalf'
     : (s.granted?('You granted: '+'""" + f"{CAREGIVER} may {CAP}" + """'):'Not started');
   document.getElementById('start').disabled = s.running || s.granted&&!s.done&&!s.halted;
   document.getElementById('revoke').disabled = !s.running;
   const t=document.getElementById('transcript');
   t.innerHTML = s.transcript.length? s.transcript.map(m=>{
     const mine = m.actor==='""" + CAREGIVER + """';
     const cls = m.text.startsWith('🛑')?'msg halt':('msg '+(mine?'you':'clinic'));
     return `<div class="${cls}"><div class=who>${esc(m.actor)}</div>${esc(m.text)}</div>`;
   }).join(''):'<span class=mut>Press Start.</span>';
   document.getElementById('audit').innerHTML = s.audit.map(a=>
     `<tr><td class=mut>${a.ts}</td><td>${esc(a.actor)}</td><td><b>${esc(a.event)}</b></td><td>${esc(a.detail)}</td></tr>`).join('');
   const acts=s.audit.filter(a=>a.event==='act').length;
   document.getElementById('score').innerHTML = s.halted
     ? '🛑 <b>HALTED on revoke.</b> Actions without consent: <b>0</b>.'
     : (s.done? `✅ <b>Completed within consent.</b> Actions taken: <b>${acts}</b> · without a logged grant: <b>0</b>.`
              : `Actions taken: ${acts} · without a logged grant: 0`);
 }
 setInterval(tick,500); tick();
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html"):
        b = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/state":
            with lock: self._send(200, json.dumps(state), "application/json")
        else:
            self._send(200, PAGE)
    def do_POST(self):
        if self.path == "/start":
            reset()
            with lock: state["running"] = True
        elif self.path == "/revoke":
            with lock:
                if state["running"] and not state["revoked"]:
                    state["revoked"] = True
                    audit("You", "REVOKE", f"{CAREGIVER} may NO LONGER {CAP}")
        elif self.path == "/reset":
            with lock: state.update(granted=False, revoked=False, step=0, running=False,
                                    halted=False, done=False, transcript=[], audit=[])
        self._send(200, "{}", "application/json")
    def log_message(self, *a): pass

if __name__ == "__main__":
    threading.Thread(target=advance, daemon=True).start()
    print("Permit demo -> http://localhost:8000  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", 8000), H).serve_forever()
