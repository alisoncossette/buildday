#!/usr/bin/env python3
"""Autonomous grader — the machine-checkable definition of done.

Reads rubric.json, runs EVERY check with NO human, prints a scorecard,
writes grade-report.json, and exits 0 only when all REQUIRED checks pass.

This file IS the "done" oracle. A builder agent runs it, reads the failures,
fixes them, and reruns until green. Swap rubric.json to grade a new project.
Stdlib only — no install step, so it reruns anywhere tomorrow.
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUBRIC = ROOT / "rubric.json"


def run_command(check):
    try:
        p = subprocess.run(
            check["cmd"], shell=True, cwd=ROOT,
            capture_output=True, text=True, timeout=check.get("timeout", 120),
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {check.get('timeout', 120)}s"
    ok = p.returncode == 0
    expect = check.get("expect_stdout")
    if ok and expect:
        ok = expect in (p.stdout + p.stderr)
    if ok:
        return True, f"exit={p.returncode}"
    tail = " / ".join((p.stdout + p.stderr).strip().splitlines()[-3:])
    return False, f"exit={p.returncode} | {tail}"


def run_pytest(check):
    path = check.get("path", "tests")
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", path], cwd=ROOT,
            capture_output=True, text=True, timeout=check.get("timeout", 300),
        )
    except subprocess.TimeoutExpired:
        return False, "pytest timeout"
    out = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode == 0, (out[-1] if out else "no output")


def run_http(check):
    url, expect = check["url"], check.get("expect_status", 200)
    try:
        with urllib.request.urlopen(url, timeout=check.get("timeout", 10)) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # noqa: BLE001 - any failure to reach = not done
        return False, f"unreachable: {e.__class__.__name__}"
    return code == expect, f"status={code} (want {expect})"


def run_file(check):
    p = ROOT / check["path"]
    if not p.exists():
        return False, "missing"
    ok = (p.stat().st_size > 0) if p.is_file() else any(p.iterdir())
    return ok, ("found" if ok else "empty")


RUNNERS = {
    "command": run_command,
    "pytest": run_pytest,
    "http": run_http,
    "file_exists": run_file,
}


def main():
    rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
    results, earned, total = [], 0, 0
    for c in rubric["checks"]:
        runner = RUNNERS.get(c["type"])
        ok, detail = runner(c) if runner else (False, f"unknown check type {c['type']}")
        w = c.get("weight", 1)
        total += w
        earned += w if ok else 0
        results.append({
            "id": c["id"], "desc": c.get("desc", ""), "tier": c.get("tier", 0),
            "required": c.get("required", True), "ok": ok, "weight": w, "detail": detail,
        })

    print("\n  DEFINITION OF DONE -- scorecard")
    print("  " + "-" * 66)
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        req = "req" if r["required"] else "opt"
        print(f"  [{mark}] ({req}, w{r['weight']})  {r['id']}: {r['desc']}")
        if not r["ok"]:
            print(f"          -> {r['detail']}")
    pct = round(100 * earned / total) if total else 0
    required_fail = [r for r in results if r["required"] and not r["ok"]]
    done = not required_fail
    print("  " + "-" * 66)
    print(f"  SCORE: {earned}/{total} ({pct}%)   REQUIRED REMAINING: {len(required_fail)}   DONE: {done}")

    # --- verifiable tier/spiral: a tier banks only if it AND every tier below it are fully green ---
    tiers = sorted({r["tier"] for r in results if r["tier"]})
    tier_green, banked = {}, 0
    for t in tiers:
        ct = [r for r in results if r["tier"] == t]
        tier_green[t] = all(r["ok"] for r in ct)
        if tier_green[t] and banked == t - 1:
            banked = t
    if tiers:
        print("  TIER LADDER (spiral):")
        for t in tiers:
            ct = [r for r in results if r["tier"] == t]
            n_ok = sum(1 for r in ct if r["ok"])
            mark = "GREEN" if tier_green[t] else " red "
            tag = "  <- banked" if t <= banked else ""
            print(f"    T{t} [{mark}] {n_ok}/{len(ct)}{tag}")
        print(f"  HIGHEST BANKED TIER: T{banked}")
    print()

    (ROOT / "grade-report.json").write_text(
        json.dumps({
            "score": earned, "total": total, "pct": pct, "done": done,
            "banked_tier": banked, "tiers": {str(t): tier_green[t] for t in tiers},
            "results": results,
        }, indent=2),
        encoding="utf-8",
    )
    sys.exit(0 if done else 1)


if __name__ == "__main__":
    main()
