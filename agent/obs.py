"""Stead — thin Langfuse observability wrapper (degrades to offline JSONL).

What-happened gets captured no matter what:
  - If the `langfuse` SDK *and* keys are present, spans/scores go to Langfuse.
  - If either is missing, we NO-OP gracefully (never crash, no hard import) and
    still append a local record to evidence/traces.jsonl so the run is auditable
    offline (matches the offline-by-default ethos of stead_agent.py).

Env:  LANGFUSE_PUBLIC_KEY  LANGFUSE_SECRET_KEY  LANGFUSE_HOST (optional)

API:
    with span("order_food", vendor="Tony's", amount=28) as s:
        ...                       # do work
        s.update(status="done")   # optional extra meta merged into the record
    score("consent_respected", 1.0, agent="agent:ruby")
    flush()                       # before process exit

Both span() and score() are best-effort: any backend error is swallowed after
the local record is written. stdlib only; langfuse is optional.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# evidence/traces.jsonl lives at the repo root (this file is agent/obs.py).
_TRACES = Path(__file__).resolve().parent.parent / "evidence" / "traces.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(kind: str, name: str, data: dict) -> None:
    """Append one event to evidence/traces.jsonl. Never raises."""
    try:
        _TRACES.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": _now(), "kind": kind, "name": name, **data}
        with _TRACES.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass  # observability must never break the agent


def _client():
    """Return a live Langfuse client, or None if SDK/keys are absent."""
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pub and sec):
        return None
    try:
        from langfuse import Langfuse  # optional dependency
    except Exception:
        return None
    try:
        kwargs = {"public_key": pub, "secret_key": sec}
        host = os.environ.get("LANGFUSE_HOST")
        if host:
            kwargs["host"] = host
        return Langfuse(**kwargs)
    except Exception:
        return None


_LF = _client()  # resolved once at import; None => pure offline mode


class _Span:
    """Handle yielded by span(); .update() merges meta into the local record
    and (when live) the Langfuse span."""

    def __init__(self, name: str, meta: dict, lf_span):
        self.name = name
        self.meta = dict(meta)
        self._lf = lf_span

    def update(self, **meta):
        self.meta.update(meta)
        if self._lf is not None:
            try:
                self._lf.update(metadata=self.meta)
            except Exception:
                self._lf = None
        return self


@contextmanager
def span(name: str, **meta):
    """Context manager wrapping a unit of work. Records start/end/duration and
    any error to traces.jsonl, mirroring to Langfuse when available."""
    t0 = time.perf_counter()
    lf_span = None
    if _LF is not None:
        try:
            lf_span = _LF.start_span(name=name, metadata=meta)
        except Exception:
            lf_span = None
    handle = _Span(name, meta, lf_span)
    err = None
    try:
        yield handle
    except Exception as e:  # capture, record, then re-raise
        err = repr(e)
        raise
    finally:
        ms = round((time.perf_counter() - t0) * 1000, 2)
        _record("span", name, {"duration_ms": ms, "error": err, "meta": handle.meta})
        if lf_span is not None:
            try:
                if err:
                    lf_span.update(level="ERROR", status_message=err)
                lf_span.end()
            except Exception:
                pass


def score(name: str, value, **meta):
    """Record a numeric/categorical score (e.g. consent_respected=1.0)."""
    _record("score", name, {"value": value, "meta": meta})
    if _LF is not None:
        try:
            _LF.create_score(name=name, value=value, metadata=meta or None)
        except Exception:
            pass


def flush():
    """Flush buffered Langfuse events. Safe to call in offline mode."""
    if _LF is not None:
        try:
            _LF.flush()
        except Exception:
            pass
