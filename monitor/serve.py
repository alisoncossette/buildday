"""Stead — live build monitor. Zero-install (stdlib only).

Serves a dashboard at http://localhost:8800 that polls the build-verify spiral's grade-report.json
and renders the tier ladder live as tiers turn green. This is a BUILD monitor (dev tool), not the
product. Run:  python monitor/serve.py
"""
import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "orchestration-kit"
REPORT = KIT / "grade-report.json"
PORT = 8800
_last_grade = [0.0]


def regrade_if_stale(min_interval=6.0):
    """Actually re-run grade.py so the monitor reflects CURRENT truth, not a frozen report."""
    now = time.time()
    if now - _last_grade[0] < min_interval:
        return
    _last_grade[0] = now
    try:
        subprocess.run([sys.executable, "grade.py"], cwd=str(KIT),
                       capture_output=True, timeout=60)
    except Exception:
        pass

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Stead — build monitor</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;background:#0c0f14;color:#e6edf3;font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
  .wrap{max-width:860px;margin:0 auto;padding:28px 20px}
  h1{font-size:20px;margin:0 0 2px;font-weight:700}
  .sub{color:#8b949e;font-size:13px;margin-bottom:20px}
  .top{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:22px}
  .pct{font-size:46px;font-weight:800;letter-spacing:-1px}
  .bar{flex:1;min-width:200px;height:12px;background:#1c2230;border-radius:8px;overflow:hidden}
  .bar>div{height:100%;background:linear-gradient(90deg,#2ea043,#3fb950);transition:width .6s}
  .badge{padding:5px 11px;border-radius:999px;font-size:13px;font-weight:700;background:#1c2230}
  .done{background:#1a7f37;color:#fff}
  .ladder{display:flex;flex-direction:column;gap:8px;margin:6px 0 26px}
  .tier{display:flex;align-items:center;gap:12px;padding:11px 14px;border-radius:10px;background:#11161f;border:1px solid #1c2230}
  .tier.green{border-color:#2ea043;background:#0f1b12}
  .dot{width:11px;height:11px;border-radius:50%;background:#6e7681;flex:none}
  .tier.green .dot{background:#3fb950;box-shadow:0 0 10px #3fb95088}
  .tn{font-weight:700;width:34px;flex:none}
  .tcount{margin-left:auto;color:#8b949e;font-size:13px}
  .banked{color:#3fb950;font-size:12px;font-weight:700;margin-left:8px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  td,th{text-align:left;padding:7px 8px;border-bottom:1px solid #1c2230;vertical-align:top}
  th{color:#8b949e;font-weight:600}
  .pass{color:#3fb950;font-weight:700}.fail{color:#f85149;font-weight:700}
  .req{color:#d29922}.opt{color:#6e7681}
  .det{color:#8b949e}
  .live{display:inline-block;width:8px;height:8px;border-radius:50%;background:#3fb950;animation:p 1.4s infinite}
  @keyframes p{0%,100%{opacity:.3}50%{opacity:1}}
  .stamp{color:#6e7681;font-size:12px;margin-top:18px}
</style></head><body><div class="wrap">
  <h1>Stead <span class="live"></span></h1>
  <div class="sub">build-verify spiral &mdash; live tier ladder (auto-refresh)</div>
  <div class="top">
    <div class="pct" id="pct">&mdash;</div>
    <div class="bar"><div id="fill" style="width:0%"></div></div>
    <div class="badge" id="state">connecting&hellip;</div>
  </div>
  <div class="ladder" id="ladder"></div>
  <table><thead><tr><th>checks</th><th></th></tr></thead><tbody id="checks"></tbody></table>
  <div class="stamp" id="stamp"></div>
</div>
<script>
const TIERS={1:"RBAC access",2:"care companion",3:"live app",4:"act on behalf",5:"voice / MARS"};
async function tick(){
  try{
    const r=await fetch('/status?t='+Date.now());const d=await r.json();
    if(!d||!d.results){document.getElementById('state').textContent='waiting for grade-report…';return;}
    document.getElementById('pct').textContent=(d.pct??0)+'%';
    document.getElementById('fill').style.width=(d.pct??0)+'%';
    const st=document.getElementById('state');
    st.textContent=d.done?'DONE ✓':('banked T'+(d.banked_tier??0)+' · '+ (d.results.filter(x=>x.required&&!x.ok).length)+' req left');
    st.className='badge'+(d.done?' done':'');
    const tg=d.tiers||{};const lad=document.getElementById('ladder');lad.innerHTML='';
    Object.keys(TIERS).forEach(t=>{
      const checks=d.results.filter(x=>String(x.tier)===t);
      const ok=checks.filter(x=>x.ok).length;const green=tg[t]===true;
      const banked=green && t<=String(d.banked_tier??0);
      const el=document.createElement('div');el.className='tier'+(green?' green':'');
      el.innerHTML='<span class="dot"></span><span class="tn">T'+t+'</span><span>'+TIERS[t]+'</span>'
        +(banked?'<span class="banked">banked</span>':'')
        +'<span class="tcount">'+ok+'/'+checks.length+'</span>';
      lad.appendChild(el);
    });
    const cb=document.getElementById('checks');cb.innerHTML='';
    d.results.forEach(x=>{
      const tr=document.createElement('tr');
      tr.innerHTML='<td><span class="'+(x.ok?'pass':'fail')+'">'+(x.ok?'PASS':'FAIL')+'</span> '
        +'<span class="'+(x.required?'req':'opt')+'">T'+x.tier+'</span> '+x.id
        +(x.ok?'':'<div class="det">'+(x.detail||'')+'</div>')+'</td><td></td>';
      cb.appendChild(tr);
    });
    document.getElementById('stamp').textContent='score '+(d.score??0)+'/'+(d.total??0)+' · refreshed '+new Date().toLocaleTimeString();
  }catch(e){document.getElementById('state').textContent='monitor offline';}
}
tick();setInterval(tick,2500);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/status"):
            regrade_if_stale()
            body = REPORT.read_bytes() if REPORT.exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.encode("utf-8"))


if __name__ == "__main__":
    print(f"Stead build monitor -> http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
