"""Stead data backbone — Ruby's day + the consent audit, on a REAL database.

Dual-backend by design:
  * If DATABASE_URL is set (e.g. a Neon Postgres string) AND psycopg is installed -> Postgres.
  * Otherwise -> local SQLite file (app/stead.db) so the demo always runs with zero setup.

Same public API either way. Two tables:
  1. care_events  — how Ruby's day actually went (meds, meals, mood, visits, notes), with who logged each.
  2. audit        — every consent decision (always-on, LOCAL observability; Langfuse is the external mirror).
"""
import json
import os
from datetime import date as _date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "stead.db"
DATABASE_URL = os.environ.get("DATABASE_URL")
TODAY = "2026-06-13"

try:
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        _PG = True
    else:
        _PG = False
except Exception:
    _PG = False  # psycopg missing -> fall back to SQLite

PK = "id SERIAL PRIMARY KEY" if _PG else "id INTEGER PRIMARY KEY AUTOINCREMENT"


def backend():
    return "postgres (Neon)" if _PG else "sqlite (local)"


def _conn():
    if _PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    import sqlite3
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ph(sql):
    return sql.replace("?", "%s") if _PG else sql


def _run(sql, params=(), fetch=None, many=None):
    with _conn() as c:
        cur = c.cursor()
        if many is not None:
            cur.executemany(_ph(sql), many)
        else:
            cur.execute(_ph(sql), params)
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        if fetch == "one":
            r = cur.fetchone()
            return dict(r) if r else None
        return None


def init_db():
    _run(f"""CREATE TABLE IF NOT EXISTS care_events(
        {PK}, date TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
        title TEXT NOT NULL, detail TEXT, value REAL, logged_by TEXT)""")
    _run(f"""CREATE TABLE IF NOT EXISTS audit(
        {PK}, ts TEXT, actor TEXT, action TEXT, scope TEXT,
        decision TEXT, reason TEXT, vendor TEXT, amount REAL, cap REAL)""")
    # THE SHARED BRAIN: grants + pending consent-requests persist HERE so the app and the agent
    # (separate processes) see the same live consent state.
    _run(f"""CREATE TABLE IF NOT EXISTS grants(
        {PK}, grantee TEXT NOT NULL, scope TEXT NOT NULL, params_json TEXT NOT NULL,
        UNIQUE(grantee, scope))""")
    _run(f"""CREATE TABLE IF NOT EXISTS consent_requests(
        {PK}, requester TEXT NOT NULL, scope TEXT NOT NULL, params_json TEXT NOT NULL,
        status TEXT NOT NULL)""")
    _run(f"""CREATE TABLE IF NOT EXISTS appointments(
        {PK}, date TEXT NOT NULL, time TEXT, title TEXT NOT NULL, kind TEXT,
        who TEXT, notes TEXT)""")
    _run(f"""CREATE TABLE IF NOT EXISTS trend_points(
        {PK}, date TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
        UNIQUE(date, metric))""")
    # MARS event feed (speak / emotion / move) persisted so the on-laptop mock (and a deployed
    # instance) survive restarts and share the same feed — the same shared-brain pattern as grants.
    _run(f"""CREATE TABLE IF NOT EXISTS mars_events(
        {PK}, ts TEXT, kind TEXT NOT NULL, data_json TEXT)""")
    if (_run("SELECT COUNT(*) AS n FROM care_events", fetch="one") or {}).get("n", 0) == 0:
        _seed()
    if (_run("SELECT COUNT(*) AS n FROM appointments", fetch="one") or {}).get("n", 0) == 0:
        _seed_appointments()
    if (_run("SELECT COUNT(*) AS n FROM trend_points", fetch="one") or {}).get("n", 0) == 0:
        _seed_trends()


def _seed():
    rows = [
        ("2026-06-11", "08:05", "meds", "Morning medications", "Lisinopril + vitamin D, taken", None, "Jane (PCA)"),
        ("2026-06-11", "08:45", "meal", "Breakfast", "Oatmeal and fruit, ate most of it", 0.9, "Jane (PCA)"),
        ("2026-06-12", "08:10", "meds", "Morning medications", "Taken on time", None, "Jane (PCA)"),
        ("2026-06-12", "12:30", "meal", "Lunch", "Picked at a sandwich, half left", 0.6, "Jane (PCA)"),
        (TODAY, "08:10", "meds", "Morning medications", "Lisinopril + vitamin D, taken on time", None, "Jane (PCA)"),
        (TODAY, "08:40", "meal", "Breakfast", "Half a piece of toast and tea - not very hungry", 0.4, "Jane (PCA)"),
        (TODAY, "10:30", "activity", "Short walk", "Out to the mailbox and back, then rested", None, "Jane (PCA)"),
        (TODAY, "12:45", "mood", "Midday check-in", "Quiet and a little low today, waved off lunch", 0.0, "Jane (PCA)"),
        (TODAY, "14:00", "visit", "PCA visit ended", "Jane headed out; Ruby settled in for a nap", None, "Jane (PCA)"),
        (TODAY, "17:30", "note", "Family note", "Called Mom - she sounded tired but okay", None, "Mom"),
    ]
    _run("INSERT INTO care_events(date,ts,kind,title,detail,value,logged_by) VALUES(?,?,?,?,?,?,?)", many=rows)


def _seed_appointments():
    """A doctor visit + a PT session this week (relative to TODAY = 2026-06-13, a Saturday)."""
    rows = [
        ("2026-06-15", "10:30", "Dr. Smith — follow-up", "doctor", "Dr. Smith",
         "Routine check; review appetite + meds. Bring the weekly summary."),
        ("2026-06-17", "14:00", "Physical therapy", "pt", "Maya (PT)",
         "Gentle mobility + balance work. 45 minutes."),
        ("2026-06-19", "11:00", "Friend visit — Carol", "social", "Carol",
         "Tea and a chat; Carol is bringing photos."),
    ]
    _run("INSERT INTO appointments(date,time,title,kind,who,notes) VALUES(?,?,?,?,?,?)", many=rows)


def _seed_trends():
    """~3 weeks of synthetic daily points. Appetite DECLINES ~30% across the window; mood drifts
    gently down; meds stay mostly on track. All visibly synthetic (ZERO PHI)."""
    start = _date(2026, 5, 24)  # 21 days through 2026-06-13
    rows = []
    for i in range(21):
        d = (start + timedelta(days=i)).isoformat()
        frac = i / 20.0
        # appetite: ~0.85 -> ~0.60 (about a 30% drop end-to-end), with a tiny wiggle for realism.
        appetite = round(0.85 - 0.255 * frac + (0.02 if i % 3 == 0 else -0.015), 3)
        appetite = max(0.0, min(1.0, appetite))
        # mood: ~0.75 -> ~0.50, gentle decline.
        mood = round(0.75 - 0.25 * frac + (0.02 if i % 2 == 0 else -0.02), 3)
        mood = max(0.0, min(1.0, mood))
        # meds: taken on time except a couple of synthetic misses.
        meds = 0.0 if i in (8, 16) else 1.0
        rows.append((d, "appetite", appetite))
        rows.append((d, "mood", mood))
        rows.append((d, "meds", meds))
    _run("INSERT INTO trend_points(date,metric,value) VALUES(?,?,?)", many=rows)


# --- THE SHARED BRAIN: persistent grants -------------------------------------------------

def get_grant_row(grantee, scope):
    """Return the params dict for a live grant, or None."""
    r = _run("SELECT params_json FROM grants WHERE grantee=? AND scope=?",
             (grantee, scope), fetch="one")
    if not r:
        return None
    try:
        return json.loads(r["params_json"])
    except Exception:
        return {}


def upsert_grant(grantee, scope, params):
    """Create or merge a grant (idempotent). Returns the merged params dict."""
    existing = get_grant_row(grantee, scope) or {}
    merged = dict(existing)
    merged.update(params or {})
    pj = json.dumps(merged)
    if _PG:
        _run("""INSERT INTO grants(grantee,scope,params_json) VALUES(?,?,?)
                ON CONFLICT(grantee,scope) DO UPDATE SET params_json=EXCLUDED.params_json""",
             (grantee, scope, pj))
    else:
        _run("""INSERT INTO grants(grantee,scope,params_json) VALUES(?,?,?)
                ON CONFLICT(grantee,scope) DO UPDATE SET params_json=excluded.params_json""",
             (grantee, scope, pj))
    return merged


def delete_grant(grantee, scope):
    existed = get_grant_row(grantee, scope) is not None
    _run("DELETE FROM grants WHERE grantee=? AND scope=?", (grantee, scope))
    return existed


# --- THE SHARED BRAIN: persistent consent requests ---------------------------------------

def add_request(requester, scope, params, status="pending"):
    """Insert a pending consent request; return its full record (with id)."""
    pj = json.dumps(params or {})
    if _PG:
        row = _run("INSERT INTO consent_requests(requester,scope,params_json,status) "
                   "VALUES(?,?,?,?) RETURNING id", (requester, scope, pj, status), fetch="one")
        rid = row["id"]
    else:
        _run("INSERT INTO consent_requests(requester,scope,params_json,status) VALUES(?,?,?,?)",
             (requester, scope, pj, status))
        rid = (_run("SELECT MAX(id) AS m FROM consent_requests", fetch="one") or {}).get("m")
    return {"id": rid, "requester": requester, "scope": scope,
            "params": params or {}, "status": status}


def get_request(rid):
    r = _run("SELECT id,requester,scope,params_json,status FROM consent_requests WHERE id=?",
             (rid,), fetch="one")
    if not r:
        return None
    try:
        params = json.loads(r["params_json"])
    except Exception:
        params = {}
    return {"id": r["id"], "requester": r["requester"], "scope": r["scope"],
            "params": params, "status": r["status"]}


def set_request_status(rid, status):
    _run("UPDATE consent_requests SET status=? WHERE id=?", (status, rid))


def list_requests(status=None):
    if status:
        rows = _run("SELECT id,requester,scope,params_json,status FROM consent_requests "
                    "WHERE status=? ORDER BY id DESC", (status,), fetch="all")
    else:
        rows = _run("SELECT id,requester,scope,params_json,status FROM consent_requests "
                    "ORDER BY id DESC", fetch="all")
    out = []
    for r in rows:
        try:
            params = json.loads(r["params_json"])
        except Exception:
            params = {}
        out.append({"id": r["id"], "requester": r["requester"], "scope": r["scope"],
                    "params": params, "status": r["status"]})
    return out


# --- Appointments -----------------------------------------------------------------------

def list_appointments():
    return _run("SELECT id,date,time,title,kind,who,notes FROM appointments "
                "ORDER BY date, time", fetch="all")


def add_appointment(date, time, title, kind="care", who="", notes=""):
    if _PG:
        row = _run("INSERT INTO appointments(date,time,title,kind,who,notes) "
                   "VALUES(?,?,?,?,?,?) RETURNING id",
                   (date, time, title, kind, who, notes), fetch="one")
        return row["id"]
    _run("INSERT INTO appointments(date,time,title,kind,who,notes) VALUES(?,?,?,?,?,?)",
         (date, time, title, kind, who, notes))
    return (_run("SELECT MAX(id) AS m FROM appointments", fetch="one") or {}).get("m")


# --- Trends -----------------------------------------------------------------------------

def trend_series():
    """Return {'appetite':[{date,value0to1}], 'mood':[{date,score0to1}], 'meds':[{date,ok}]}
    plus a plain-language summary grounded ONLY in the stored points."""
    rows = _run("SELECT date,metric,value FROM trend_points ORDER BY date", fetch="all")
    appetite, mood, meds = [], [], []
    for r in rows:
        if r["metric"] == "appetite":
            appetite.append({"date": r["date"], "value0to1": round(r["value"], 3)})
        elif r["metric"] == "mood":
            mood.append({"date": r["date"], "score0to1": round(r["value"], 3)})
        elif r["metric"] == "meds":
            meds.append({"date": r["date"], "ok": bool(r["value"])})
    return {"appetite": appetite, "mood": mood, "meds": meds}


def trend_summary(series=None):
    s = series or trend_series()
    parts = []
    ap = s["appetite"]
    if len(ap) >= 2:
        first, last = ap[0]["value0to1"], ap[-1]["value0to1"]
        if first > 0:
            pct = round((first - last) / first * 100)
        else:
            pct = 0
        if pct >= 5:
            parts.append(f"Appetite has declined about {pct}% over the last {len(ap)} days "
                         f"(from {round(first*100)}% to {round(last*100)}% of meals).")
        elif pct <= -5:
            parts.append(f"Appetite has improved about {abs(pct)}% over the last {len(ap)} days.")
        else:
            parts.append("Appetite has stayed roughly steady over the last few weeks.")
    md = s["mood"]
    if len(md) >= 2:
        if md[-1]["score0to1"] < md[0]["score0to1"] - 0.05:
            parts.append("Mood has drifted a little lower across the window.")
        else:
            parts.append("Mood has held fairly steady.")
    misses = [m for m in s["meds"] if not m["ok"]]
    if misses:
        parts.append(f"Medications were on time most days, with {len(misses)} missed dose(s).")
    else:
        parts.append("Medications were taken on time every day.")
    return " ".join(parts) or "Not enough data yet to summarize trends."


def timeline(date=TODAY):
    return _run("SELECT ts,kind,title,detail,value,logged_by FROM care_events WHERE date=? ORDER BY ts",
                (date,), fetch="all")


def add_event(date, ts, kind, title, detail="", value=None, logged_by="Family"):
    _run("INSERT INTO care_events(date,ts,kind,title,detail,value,logged_by) VALUES(?,?,?,?,?,?,?)",
         (date, ts, kind, title, detail, value, logged_by))


def food_trend(days=3):
    rows = _run("SELECT value FROM care_events WHERE kind='meal' AND value IS NOT NULL "
                "ORDER BY date DESC, ts DESC LIMIT ?", (days,), fetch="all")
    vals = [r["value"] for r in rows]
    if len(vals) < 2:
        return None
    recent, older = vals[0], vals[-1]
    if recent < older - 0.1:
        return "declining"
    if recent > older + 0.1:
        return "improving"
    return "steady"


def day_summary(date=TODAY):
    ev = timeline(date)
    meals = [e for e in ev if e["kind"] == "meal" and e["value"] is not None]
    avg = sum(e["value"] for e in meals) / len(meals) if meals else None
    meds = next((e for e in ev if e["kind"] == "meds"), None)
    mood = next((e for e in ev if e["kind"] == "mood"), None)
    meal_label = "No meals logged"
    if avg is not None:
        meal_label = "Light - ate little" if avg < 0.5 else ("Moderate" if avg < 0.8 else "Good appetite")
    return {
        "date": date,
        "meds": ("On time" if meds else "Not logged"),
        "meds_ok": bool(meds),
        "meals": meal_label,
        "meals_ok": (avg is not None and avg >= 0.5),
        "mood": (mood["detail"] if mood else "Not logged"),
        "mood_low": (mood is not None and (mood["value"] or 0) < 0.5),
        "event_count": len(ev),
        "trend": food_trend(),
    }


def write_audit(actor, action, scope, decision, reason="", vendor="", amount=None, cap=None, ts=""):
    _run("INSERT INTO audit(ts,actor,action,scope,decision,reason,vendor,amount,cap) VALUES(?,?,?,?,?,?,?,?,?)",
         (ts, actor, action, scope, decision, reason, vendor, amount, cap))


def recent_audit(n=20):
    return _run("SELECT ts,actor,action,scope,decision,reason,vendor,amount,cap FROM audit "
                "ORDER BY id DESC LIMIT ?", (n,), fetch="all")


# --- MARS event feed (Neon-backed shared brain for the embodiment mock) ------------------

def add_mars_event(kind, data, ts=""):
    _run("INSERT INTO mars_events(ts,kind,data_json) VALUES(?,?,?)",
         (ts, kind, json.dumps(data or {})))


def recent_mars_events(n=25):
    rows = _run("SELECT id,ts,kind,data_json FROM mars_events ORDER BY id DESC LIMIT ?", (n,), fetch="all")
    out = []
    for r in reversed(rows or []):  # chronological ascending for the UI
        try:
            data = json.loads(r["data_json"]) if r.get("data_json") else {}
        except Exception:
            data = {}
        out.append({"seq": r["id"], "ts": r["ts"], "kind": r["kind"], **data})
    return out
