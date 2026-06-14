"""Stead — long-term memory for Ruby's agent (mem0, with an offline SQLite fallback).

RubyMemory is the agent's recall layer: it remembers Ruby's PREFERENCES ("decaf after 6pm"),
MOODS ("seemed low this morning"), and PATTERNS over time — which is also the drift BASELINE the
care loop compares against. It is a thin, swappable adapter:

  - LIVE: the mem0 hosted memory layer via the mem0 Python SDK (`pip install mem0ai`,
    `from mem0 import MemoryClient`), keyed off MEM0_API_KEY. add() lets mem0 extract + dedupe
    facts; search() does semantic recall. See https://docs.mem0.ai/platform/quickstart
  - OFFLINE (default when no key / SDK / network): a local SQLite store at STEAD_MEMORY_DB
    (default ./.stead/ruby_memory.db) with naive keyword scoring, so the demo runs on a hotspot.

Either way the surface is the same three calls — add(text, metadata), search(query) -> list,
recent(n) -> list — so the agent never knows or cares which backend is live.

Env:
  MEM0_API_KEY     enable the hosted mem0 backend (else local fallback)
  MEM0_USER_ID     whose memory this is (default "ruby")
  STEAD_MEMORY_DB  path for the offline SQLite store (default ./.stead/ruby_memory.db)

Usage:
    mem = RubyMemory()                                  # auto-selects backend
    mem.add("Ruby prefers decaf after 6pm", {"kind": "preference"})
    hits = mem.search("what does Ruby drink in the evening?")
    for h in mem.recent(5):
        print(h["text"], h["metadata"])
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

DEFAULT_USER_ID = "ruby"
_DEFAULT_DB = os.path.join(".stead", "ruby_memory.db")


class RubyMemory:
    """Ruby's long-term memory. Prefers the hosted mem0 SDK; falls back to local SQLite offline.

    The three methods below are the entire contract the agent depends on; the chosen backend is
    an implementation detail behind `self._live` (True iff a real mem0 client is wired up)."""

    def __init__(
        self,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        """Wire up the best available backend.

        Args:
            user_id: Whose memory this is (default env MEM0_USER_ID or "ruby"). mem0 scopes
                add/search/get_all by user_id; the local fallback stores it per row.
            api_key: mem0 API key; defaults to env MEM0_API_KEY. Absent -> local fallback.
            db_path: SQLite path for the offline store (default env STEAD_MEMORY_DB or
                ./.stead/ruby_memory.db).
            client: Inject a pre-built mem0 MemoryClient (tests / a self-hosted Memory instance).
                Must expose add()/search()/get_all(); bypasses api_key discovery.
        """
        self.user_id = user_id or os.environ.get("MEM0_USER_ID", DEFAULT_USER_ID)
        self.db_path = db_path or os.environ.get("STEAD_MEMORY_DB", _DEFAULT_DB)
        self._client = client
        self._live = False

        if self._client is not None:
            self._live = True
        else:
            key = api_key or os.environ.get("MEM0_API_KEY") or os.environ.get("MEM0_APIKEY")
            if key:
                self._client = self._connect_mem0(key)
                self._live = self._client is not None

        # The local store always exists so recent()/search() degrade gracefully even when live.
        self._init_local()

    # --- backend wiring -------------------------------------------------------------------

    def _connect_mem0(self, api_key: str) -> Optional[Any]:
        """Build a hosted mem0 client, or return None to fall back offline (no SDK / network)."""
        try:
            from mem0 import MemoryClient  # type: ignore
        except Exception:
            # SDK not installed — stay offline rather than crash the demo.
            return None
        try:
            return MemoryClient(api_key=api_key)
        except Exception:
            return None

    def _init_local(self) -> None:
        """Create the offline SQLite store (idempotent)."""
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._db() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   TEXT NOT NULL,
                    text      TEXT NOT NULL,
                    metadata  TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                )
                """
            )

    def _db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    @property
    def backend(self) -> str:
        """'mem0' when the hosted layer is live, else 'local' (offline SQLite)."""
        return "mem0" if self._live else "local"

    # --- public surface -------------------------------------------------------------------

    def add(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Remember a fact about Ruby — a preference, a mood note, an observed pattern.

        Args:
            text: The memory in plain language, e.g. "Ruby prefers decaf after 6pm".
            metadata: Optional tags, e.g. {"kind": "preference"} or {"kind": "mood", "day": "..."}.
                These flow to mem0 as metadata and are stored verbatim in the local fallback.

        Returns:
            {"backend": "mem0"|"local", "stored": True, ...} — best-effort echo of what landed.
        """
        meta = dict(metadata or {})
        # Always mirror to the local store so recent() and offline search keep working.
        local_id = self._local_add(text, meta)

        if self._live:
            try:
                # mem0 add() takes a messages list and scopes by user_id; metadata is passed through.
                res = self._client.add(
                    [{"role": "user", "content": text}],
                    user_id=self.user_id,
                    metadata=meta,
                )
                return {"backend": "mem0", "stored": True, "result": res}
            except Exception as exc:  # noqa: BLE001 - never let recall break the agent
                # Live add failed (network/quota); the local mirror above already has it.
                return {"backend": "local", "stored": True, "id": local_id,
                        "note": f"mem0 add failed, kept locally: {exc}"}
        return {"backend": "local", "stored": True, "id": local_id}

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Semantic recall: the memories most relevant to `query`, best first.

        Args:
            query: A natural-language question, e.g. "what does Ruby drink in the evening?".
            limit: Max memories to return.

        Returns:
            A list of {"text", "metadata", "score", "created_at", "id"} dicts (normalized across
            backends). Empty list if nothing matches.
        """
        if self._live:
            try:
                # mem0 search() filters by user_id; returns dicts with id/memory/metadata/score.
                raw = self._client.search(
                    query, filters={"user_id": self.user_id}, limit=limit
                )
                return self._normalize_mem0(raw)
            except Exception:  # noqa: BLE001 - degrade to the local store on any failure
                pass
        return self._local_search(query, limit)

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        """The `n` most recently added memories (newest first) — the at-a-glance state of Ruby.

        Useful as the drift baseline: compare today's moods/patterns against what's remembered.
        """
        if self._live:
            try:
                raw = self._client.get_all(user_id=self.user_id)
                items = self._normalize_mem0(raw)
                # mem0 get_all is not guaranteed ordered; sort newest-first when timestamps exist.
                items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
                return items[:n]
            except Exception:  # noqa: BLE001
                pass
        return self._local_recent(n)

    # --- mem0 response normalization ------------------------------------------------------

    @staticmethod
    def _normalize_mem0(raw: Any) -> List[Dict[str, Any]]:
        """Flatten a mem0 add/search/get_all payload into the common record shape.

        mem0 has shipped both a bare list and a {"results": [...]} envelope across versions, so
        accept either; map mem0's `memory` field to our `text`.
        """
        if isinstance(raw, dict):
            items = raw.get("results", raw.get("memories", []))
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append(
                {
                    "id": it.get("id"),
                    "text": it.get("memory") or it.get("text") or "",
                    "metadata": it.get("metadata") or {},
                    "score": it.get("score"),
                    "created_at": it.get("created_at"),
                }
            )
        return out

    # --- local SQLite fallback ------------------------------------------------------------

    def _local_add(self, text: str, metadata: Dict[str, Any]) -> int:
        with self._db() as con:
            cur = con.execute(
                "INSERT INTO memories (user_id, text, metadata, created_at) VALUES (?, ?, ?, ?)",
                (self.user_id, text, json.dumps(metadata), time.time()),
            )
            return int(cur.lastrowid)

    def _local_recent(self, n: int) -> List[Dict[str, Any]]:
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (self.user_id, n),
            ).fetchall()
        return [self._row(r) for r in rows]

    def _local_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Naive keyword overlap scoring — no embeddings, but deterministic and offline.

        Score = fraction of the query's distinct word-stems that appear in the memory text.
        Good enough to surface "decaf after 6pm" for "what does Ruby drink in the evening".
        """
        terms = self._tokens(query)
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM memories WHERE user_id = ?", (self.user_id,)
            ).fetchall()
        scored: List[Dict[str, Any]] = []
        for r in rows:
            rec = self._row(r)
            words = self._tokens(rec["text"])
            if not terms:
                rec["score"] = 0.0
            else:
                hits = sum(1 for t in terms if t in words)
                rec["score"] = hits / len(terms)
            if rec["score"] > 0 or not terms:
                scored.append(rec)
        scored.sort(key=lambda m: (m["score"], m["created_at"] or 0), reverse=True)
        return scored[:limit]

    @staticmethod
    def _tokens(text: str) -> set:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        try:
            meta = json.loads(r["metadata"])
        except Exception:
            meta = {}
        return {
            "id": r["id"],
            "text": r["text"],
            "metadata": meta,
            "score": None,
            "created_at": r["created_at"],
        }


if __name__ == "__main__":  # tiny offline smoke test (no key needed)
    mem = RubyMemory(db_path=os.path.join(".stead", "smoke_memory.db"))
    print("backend:", mem.backend)
    mem.add("Ruby prefers decaf coffee after 6pm", {"kind": "preference"})
    mem.add("Ruby seemed low and quiet this morning", {"kind": "mood", "day": "2026-06-14"})
    mem.add("Ruby lights up when her sister calls on Sundays", {"kind": "pattern"})
    print("search ->", mem.search("what does Ruby drink in the evening?"))
    print("recent ->", [m["text"] for m in mem.recent(3)])
