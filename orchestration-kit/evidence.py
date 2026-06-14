#!/usr/bin/env python3
"""Stead -- TIER EVIDENCE iterator.

Not a green light. Receipts.

For EVERY check in rubric.json this:
  1. runs the tier's REAL check command (same runners as grade.py) and captures
     the exact command, exit code, pass/fail, and the actual stdout/stderr;
  2. collects the concrete ARTIFACTS that satisfy the tier -- the real files
     (path + 1-line description) that implement it, plus short excerpts;
  3. emits a CLICKABLE evidence page (evidence/index.html) you drill into, and a
     machine-readable evidence/evidence.json alongside it.

Re-runnable: `python orchestration-kit/evidence.py` regenerates everything from
the CURRENT repo state. Every run reflects what is actually built right now.

Stdlib only. ASCII to stdout (Windows cp1252 console); the HTML it writes is UTF-8.
It does NOT start servers -- it curls the one already running on :8770 for T3.
"""
import html
import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # orchestration-kit/
REPO = ROOT.parent                              # build-day/
RUBRIC = ROOT / "rubric.json"
OUT_DIR = ROOT / "evidence"
OUT_HTML = OUT_DIR / "index.html"
OUT_JSON = OUT_DIR / "evidence.json"

MAX_OUT = 6000          # truncation budget for captured stdout/stderr
MAX_EXCERPT = 40        # lines of a file excerpt to inline


# --------------------------------------------------------------------------- #
# check runners -- mirror grade.py so the evidence matches the grade exactly,  #
# but capture the FULL command + raw output (grade.py only keeps a tail).      #
# --------------------------------------------------------------------------- #
def _truncate(text):
    text = text or ""
    if len(text) <= MAX_OUT:
        return text, False
    head = text[: MAX_OUT // 2]
    tail = text[-MAX_OUT // 2:]
    return head + "\n...[truncated]...\n" + tail, True


def run_command(check):
    cmd = check["cmd"]
    try:
        p = subprocess.run(
            cmd, shell=True, cwd=ROOT,
            capture_output=True, text=True, timeout=check.get("timeout", 120),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "cmd": cmd, "exit": None,
                "stdout": "", "stderr": f"timeout after {check.get('timeout', 120)}s",
                "truncated": False}
    ok = p.returncode == 0
    expect = check.get("expect_stdout")
    if ok and expect:
        ok = expect in (p.stdout + p.stderr)
    out, t1 = _truncate(p.stdout)
    err, t2 = _truncate(p.stderr)
    return {"ok": ok, "cmd": cmd, "exit": p.returncode,
            "stdout": out, "stderr": err, "truncated": t1 or t2}


def run_pytest(check):
    path = check.get("path", "tests")
    cmd = [sys.executable, "-m", "pytest", "-v", path]
    try:
        p = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            timeout=check.get("timeout", 300),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "cmd": " ".join(cmd), "exit": None,
                "stdout": "", "stderr": "pytest timeout", "truncated": False}
    out, t1 = _truncate(p.stdout)
    err, t2 = _truncate(p.stderr)
    return {"ok": p.returncode == 0, "cmd": " ".join(cmd), "exit": p.returncode,
            "stdout": out, "stderr": err, "truncated": t1 or t2}


def run_file(check):
    rel = check["path"]
    p = ROOT / rel
    if not p.exists():
        return {"ok": False, "cmd": f"test -e {rel}", "exit": 1,
                "stdout": "", "stderr": f"{rel}: missing", "truncated": False}
    if p.is_file():
        ok = p.stat().st_size > 0
        detail = f"{rel}: {p.stat().st_size} bytes"
    else:
        ok = any(p.iterdir())
        detail = f"{rel}: {'non-empty dir' if ok else 'empty dir'}"
    return {"ok": ok, "cmd": f"test -s {rel}", "exit": 0 if ok else 1,
            "stdout": detail, "stderr": "" if ok else f"{rel}: empty",
            "truncated": False}


RUNNERS = {"command": run_command, "pytest": run_pytest, "file_exists": run_file}


# --------------------------------------------------------------------------- #
# artifacts -- the real files that DELIVER each check. Paths are repo-relative #
# and verified to exist before being attached (so the evidence never lies).    #
# excerpt = (path, start_line, n_lines) inlined as proof of the actual code.   #
# --------------------------------------------------------------------------- #
# Each entry: (repo_relative_path, one-line description, excerpt_spec_or_None)
# excerpt_spec = (start_line_1based, n_lines)
ARTIFACTS = {
    "consent-contract": [
        ("orchestration-kit/consent_agent/__init__.py",
         "ConsentEngine: owner-held, parameterized, revocable, audited consent (Bolo-shaped)",
         (83, 30)),
    ],
    "rbac-access": [
        ("orchestration-kit/tests/test_rbac.py",
         "T1 RBAC test suite: per-actor scopes, owner-only grant/revoke, audit", None),
        ("orchestration-kit/consent_agent/__init__.py",
         "grant/check/revoke + can_view + audit log that the RBAC tests exercise",
         (106, 30)),
    ],
    "care-companion": [
        ("orchestration-kit/tests/test_care.py",
         "T2 tests: handoff summary + longitudinal trends, gated by RBAC", None),
        ("orchestration-kit/consent_agent/care.py",
         "CareLog: shift handoff_summary() + longitudinal trends()", (21, 25)),
    ],
    "app-surface": [
        ("app/server.py",
         "The real Stead PWA server on :8770, wired to the live ConsentEngine", (1, 35)),
        ("app/static/index.html", "Installable PWA shell (the 'Stead' UI body)", None),
        ("app/static/manifest.json", "PWA manifest -> Add to Home Screen / PWABuilder", None),
        ("app/static/sw.js", "Service worker -> offline serve", None),
    ],
    "act-on-behalf": [
        ("orchestration-kit/tests/test_agent.py",
         "T4 tests: act within grant, HALT on wrong-vendor/over-cap/after-revoke, raise-cap-on-fly",
         None),
        ("orchestration-kit/consent_agent/__init__.py",
         "act() -> done|halted with reason; update_grant() raises cap on the fly", (165, 28)),
    ],
    "voice-through-mars": [
        ("orchestration-kit/tests/test_voice.py",
         "T5 tests: agent vocalizes negotiation + consent events (offline transcript)", None),
        ("orchestration-kit/consent_agent/voice.py",
         "Vocalizer.say(): speak through MARS / record offline transcript", (10, 13)),
    ],
    "self-correction-evidence": [
        ("orchestration-kit/evidence/self-correction.md",
         "Captured loop log: a fresh-context verifier caught test-gaming, loop self-corrected",
         None),
        ("orchestration-kit/workflow/build-verify-loop.js",
         "The grade->build->verify->re-grade loop that produced the self-correction", None),
    ],
}

# For T3 (app-surface) we also probe the live PWA endpoints as extra receipts.
PWA_PROBES = [
    ("GET /", "http://localhost:8770/", "html-contains:Stead"),
    ("GET /manifest.json", "http://localhost:8770/manifest.json", "json"),
    ("GET /api/state?actor=mom", "http://localhost:8770/api/state?actor=mom", "json"),
    ("GET /api/state?actor=jane:pca", "http://localhost:8770/api/state?actor=jane:pca", "json"),
    ("GET /api/state?actor=agent:ruby", "http://localhost:8770/api/state?actor=agent:ruby", "json"),
]


def probe_pwa():
    """Curl the already-running server for T3 receipts. Never starts a server."""
    results = []
    for label, url, mode in PWA_PROBES:
        rec = {"label": label, "url": url, "ok": False, "detail": "", "body": ""}
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                code = r.status
                ctype = r.headers.get("Content-Type", "")
                raw = r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            rec["detail"] = f"HTTP {e.code}"
            results.append(rec)
            continue
        except Exception as e:  # noqa: BLE001
            rec["detail"] = f"unreachable: {e.__class__.__name__}"
            results.append(rec)
            continue
        snippet = raw.strip()
        if mode == "html-contains:Stead":
            rec["ok"] = "Stead" in raw
            rec["detail"] = f"HTTP {code} {ctype} | {len(raw)} bytes | contains 'Stead': {rec['ok']}"
            rec["body"] = snippet[:800]
        else:  # json
            try:
                parsed = json.loads(raw)
                rec["ok"] = code == 200
                rec["detail"] = f"HTTP {code} {ctype} | valid JSON"
                rec["body"] = json.dumps(parsed, indent=2)[:1200]
            except Exception:
                rec["ok"] = code == 200
                rec["detail"] = f"HTTP {code} {ctype} | non-JSON"
                rec["body"] = snippet[:800]
        results.append(rec)
    return results


def collect_artifacts(check_id):
    out = []
    for spec in ARTIFACTS.get(check_id, []):
        rel, desc, excerpt = spec
        p = REPO / rel
        rec = {"path": rel, "desc": desc, "exists": p.exists(),
               "abs": str(p), "size": None, "excerpt": None, "excerpt_range": None}
        if p.exists() and p.is_file():
            try:
                rec["size"] = p.stat().st_size
            except OSError:
                pass
            if excerpt:
                start, n = excerpt
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                    chunk = lines[start - 1: start - 1 + min(n, MAX_EXCERPT)]
                    rec["excerpt"] = "\n".join(chunk)
                    rec["excerpt_range"] = f"lines {start}-{start + len(chunk) - 1}"
                except OSError:
                    pass
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# build the evidence model                                                     #
# --------------------------------------------------------------------------- #
def build():
    rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
    checks = rubric["checks"]
    items = []
    for c in checks:
        runner = RUNNERS.get(c["type"])
        if runner:
            res = runner(c)
        else:
            res = {"ok": False, "cmd": "", "exit": None, "stdout": "",
                   "stderr": f"unknown check type {c['type']}", "truncated": False}
        item = {
            "id": c["id"],
            "type": c["type"],
            "tier": c.get("tier", 0),
            "desc": c.get("desc", ""),
            "required": c.get("required", True),
            "weight": c.get("weight", 1),
            "claim": c.get("desc", ""),
            "command": res["cmd"],
            "exit_code": res["exit"],
            "ok": res["ok"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "truncated": res["truncated"],
            "artifacts": collect_artifacts(c["id"]),
        }
        if c["id"] == "app-surface":
            item["pwa_probes"] = probe_pwa()
        items.append(item)

    # group by tier
    tiers = {}
    for it in items:
        tiers.setdefault(it["tier"], []).append(it)

    tier_summaries = []
    banked = 0
    for t in sorted(tiers):
        cs = tiers[t]
        green = all(x["ok"] for x in cs)
        if green and banked == t - 1:
            banked = t
        tier_summaries.append({
            "tier": t,
            "green": green,
            "n_ok": sum(1 for x in cs if x["ok"]),
            "n": len(cs),
            "required": any(x["required"] for x in cs),
        })

    model = {
        "name": rubric.get("name", "Stead"),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "n_pass": sum(1 for x in items if x["ok"]),
        "n_fail": sum(1 for x in items if not x["ok"]),
        "n_total": len(items),
        "banked_tier": banked,
        "tiers": tier_summaries,
        "checks": items,
    }
    return model


# --------------------------------------------------------------------------- #
# render HTML (UTF-8; emoji allowed here, NOT in stdout)                        #
# --------------------------------------------------------------------------- #
def _file_href(rel):
    abs_path = (REPO / rel).resolve()
    uri = abs_path.as_uri()  # file:///C:/...
    return uri


def _esc(s):
    return html.escape(s if s is not None else "")


def render_html(model):
    rows = []
    for tsum in model["tiers"]:
        t = tsum["tier"]
        cs = [c for c in model["checks"] if c["tier"] == t]
        badge = "GREEN" if tsum["green"] else "red"
        tcls = "green" if tsum["green"] else "red"
        req = "required" if tsum["required"] else "stretch"
        banked = " &middot; banked" if t <= model["banked_tier"] else ""
        rows.append(f'<section class="tier {tcls}">')
        rows.append(
            f'<h2><span class="dot {tcls}"></span>T{t} '
            f'<span class="pill {tcls}">{badge}</span> '
            f'<span class="meta">{tsum["n_ok"]}/{tsum["n"]} checks &middot; {req}{banked}</span></h2>'
        )
        for c in cs:
            ccls = "green" if c["ok"] else "red"
            status = "PASS" if c["ok"] else "FAIL"
            rows.append('<details class="check">')
            rows.append(
                f'<summary><span class="pill {ccls}">{status}</span> '
                f'<code>{_esc(c["id"])}</code> '
                f'<span class="wt">w{c["weight"]} &middot; {"req" if c["required"] else "opt"}</span>'
                f'<span class="claim">{_esc(c["desc"])}</span></summary>'
            )
            rows.append('<div class="body">')

            # claim
            rows.append('<h4>What it claims</h4>')
            rows.append(f'<p class="claimtext">{_esc(c["claim"])}</p>')

            # command + exit
            rows.append('<h4>Command run</h4>')
            rows.append(f'<pre class="cmd">$ {_esc(c["command"])}</pre>')
            exit_disp = "(none)" if c["exit_code"] is None else str(c["exit_code"])
            rows.append(
                f'<p class="exit">exit code: <b class="{ccls}">{exit_disp}</b> '
                f'&rarr; <b class="{ccls}">{status}</b></p>'
            )

            # output
            out = c["stdout"].strip()
            err = c["stderr"].strip()
            if out:
                rows.append('<h4>stdout</h4>')
                rows.append(f'<pre class="out">{_esc(out)}</pre>')
            if err:
                rows.append('<h4>stderr</h4>')
                rows.append(f'<pre class="err">{_esc(err)}</pre>')
            if not out and not err:
                rows.append('<p class="muted">(no output captured)</p>')
            if c["truncated"]:
                rows.append('<p class="muted">output truncated for display</p>')

            # PWA probes for T3
            if c.get("pwa_probes"):
                rows.append('<h4>Live PWA receipts (server already running on :8770)</h4>')
                for pr in c["pwa_probes"]:
                    pcls = "green" if pr["ok"] else "red"
                    pstatus = "OK" if pr["ok"] else "FAIL"
                    rows.append('<details class="probe">')
                    rows.append(
                        f'<summary><span class="pill {pcls}">{pstatus}</span> '
                        f'<code>{_esc(pr["label"])}</code> '
                        f'<a href="{_esc(pr["url"])}" target="_blank">{_esc(pr["url"])}</a></summary>'
                    )
                    rows.append(f'<p class="muted">{_esc(pr["detail"])}</p>')
                    if pr["body"]:
                        rows.append(f'<pre class="out">{_esc(pr["body"])}</pre>')
                    rows.append('</details>')

            # artifacts
            rows.append('<h4>Files that prove it</h4>')
            if not c["artifacts"]:
                rows.append('<p class="muted">(no mapped artifacts)</p>')
            for a in c["artifacts"]:
                acls = "green" if a["exists"] else "red"
                mark = "OK" if a["exists"] else "MISSING"
                href = _file_href(a["path"])
                size = f' &middot; {a["size"]} bytes' if a["size"] is not None else ""
                rows.append('<div class="artifact">')
                rows.append(
                    f'<div class="artrow"><span class="pill {acls}">{mark}</span> '
                    f'<a href="{_esc(href)}">{_esc(a["path"])}</a>{size}</div>'
                )
                rows.append(f'<div class="artdesc">{_esc(a["desc"])}</div>')
                if a["excerpt"]:
                    rows.append(
                        f'<div class="excerpt-label">excerpt &middot; {_esc(a["excerpt_range"])}</div>'
                    )
                    rows.append(f'<pre class="excerpt">{_esc(a["excerpt"])}</pre>')
                rows.append('</div>')

            rows.append('</div>')      # .body
            rows.append('</details>')  # .check
        rows.append('</section>')

    body = "\n".join(rows)
    pass_n, fail_n = model["n_pass"], model["n_fail"]
    css = """
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c232c;--ink:#e6edf3;--mut:#8b949e;
--green:#2ea043;--greend:#1a4d2a;--red:#f85149;--redd:#5a1f1f;--line:#30363d;--accent:#58a6ff;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
header{padding:28px 32px 16px;border-bottom:1px solid var(--line);}
header h1{margin:0 0 6px;font-size:24px;}
header .sub{color:var(--mut);font-size:14px;}
.scoreline{margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.score{padding:6px 12px;border-radius:8px;font-weight:600;font-size:13px;border:1px solid var(--line);}
.score.pass{background:var(--greend);color:#7ee787}
.score.fail{background:var(--redd);color:#ffa198}
.score.bank{background:#1f2a44;color:#79c0ff}
main{padding:20px 32px 60px;max-width:1000px;}
.tier{margin:18px 0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--panel);}
.tier.green{border-left:4px solid var(--green);}
.tier.red{border-left:4px solid var(--red);}
.tier h2{margin:0;padding:14px 18px;font-size:17px;display:flex;align-items:center;gap:10px;
background:var(--panel2);border-bottom:1px solid var(--line);}
.tier h2 .meta{color:var(--mut);font-size:13px;font-weight:400;margin-left:auto;}
.dot{width:11px;height:11px;border-radius:50%;display:inline-block;}
.dot.green{background:var(--green)} .dot.red{background:var(--red)}
.pill{font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;letter-spacing:.4px;}
.pill.green{background:var(--green);color:#06210f} .pill.red{background:var(--red);color:#2b0808}
.check{border-top:1px solid var(--line);}
.check>summary{cursor:pointer;padding:12px 18px;display:flex;align-items:center;gap:10px;
flex-wrap:wrap;list-style:none;}
.check>summary::-webkit-details-marker{display:none}
.check>summary::before{content:"\\25B8";color:var(--mut);font-size:12px;}
.check[open]>summary::before{content:"\\25BE";}
.check>summary code{color:var(--accent);font-size:13px;}
.check>summary .wt{color:var(--mut);font-size:12px;}
.check>summary .claim{color:var(--mut);font-size:13px;flex-basis:100%;margin-left:24px;}
.body{padding:6px 22px 20px 24px;}
.body h4{margin:16px 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);}
.claimtext{margin:0;color:var(--ink);}
pre{background:#0a0e14;border:1px solid var(--line);border-radius:8px;padding:12px 14px;
overflow:auto;font:12.5px/1.5 SFMono-Regular,Consolas,Menlo,monospace;white-space:pre-wrap;
word-break:break-word;}
pre.cmd{color:#79c0ff;background:#0b1830;border-color:#1f3a5f;}
pre.err{border-color:#5a2a2a;color:#ffb4ad;}
.exit b.green{color:#7ee787} .exit b.red{color:#ffa198}
.muted{color:var(--mut);font-size:13px;}
.artifact{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:#0f141b;}
.artrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.artrow a{color:var(--accent);text-decoration:none;font-size:13.5px;}
.artrow a:hover{text-decoration:underline;}
.artdesc{color:var(--mut);font-size:13px;margin:4px 0 0 2px;}
.excerpt-label{color:var(--mut);font-size:11px;margin:8px 0 3px;text-transform:uppercase;letter-spacing:.5px;}
pre.excerpt{margin:0;background:#0a0e14;}
.probe{margin:6px 0;border:1px solid var(--line);border-radius:8px;padding:6px 10px;background:#0f141b;}
.probe>summary{cursor:pointer;display:flex;gap:8px;align-items:center;flex-wrap:wrap;list-style:none;}
.probe>summary code{color:var(--accent);font-size:12.5px;}
.probe>summary a{color:var(--mut);font-size:12px;text-decoration:none;}
.foot{color:var(--mut);font-size:12px;margin-top:30px;border-top:1px solid var(--line);padding-top:14px;}
"""
    page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stead -- Tier Evidence</title>
<style>{css}</style>
</head><body>
<header>
  <h1>\U0001f9fe Stead &mdash; Tier Evidence</h1>
  <div class="sub">Not a green light. Receipts. Click any tier &rarr; check to drill into the real command, exit code, output, and the files that prove it.</div>
  <div class="scoreline">
    <span class="score pass">{pass_n} PASS</span>
    <span class="score fail">{fail_n} FAIL</span>
    <span class="score bank">Highest banked tier: T{model["banked_tier"]}</span>
    <span class="muted">{model["n_total"]} checks &middot; generated {_esc(model["generated"])}</span>
  </div>
</header>
<main>
{body}
<div class="foot">Regenerate from current repo state: <code>python orchestration-kit/evidence.py</code> &middot; source: <code>rubric.json</code></div>
</main>
</body></html>
"""
    return page


# --------------------------------------------------------------------------- #
# main                                                                          #
# --------------------------------------------------------------------------- #
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[evidence] reading rubric.json and running every tier check...")
    model = build()

    OUT_JSON.write_text(json.dumps(model, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(model), encoding="utf-8")

    # ASCII-only scorecard to stdout (cp1252-safe).
    print("\n  STEAD TIER EVIDENCE -- receipts, not theater")
    print("  " + "-" * 64)
    for tsum in model["tiers"]:
        cs = [c for c in model["checks"] if c["tier"] == tsum["tier"]]
        mark = "GREEN" if tsum["green"] else " red "
        print(f"  T{tsum['tier']} [{mark}] {tsum['n_ok']}/{tsum['n']}")
        for c in cs:
            st = "PASS" if c["ok"] else "FAIL"
            arts = sum(1 for a in c["artifacts"] if a["exists"])
            print(f"      [{st}] {c['id']:<24} exit={c['exit_code']}  artifacts={arts}/{len(c['artifacts'])}")
            if not c["ok"]:
                tail = (c["stderr"] or c["stdout"]).strip().splitlines()
                if tail:
                    print(f"             -> {tail[-1][:90]}")
    print("  " + "-" * 64)
    print(f"  PASS={model['n_pass']}  FAIL={model['n_fail']}  highest_banked=T{model['banked_tier']}")
    print(f"  HTML: {OUT_HTML}")
    print(f"  JSON: {OUT_JSON}")
    print()


if __name__ == "__main__":
    main()
