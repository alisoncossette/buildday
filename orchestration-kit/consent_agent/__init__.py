"""Stead — consent / RBAC engine. SUBJECT to PRD.md.

Per-actor roles & scopes, owner-held, parameterized, revocable, audited consent for anyone or
anything acting on a vulnerable person's behalf.

BACKED BY BOLO. The real consent layer is the Bolo MCP (@bolospot/mcp: create_grant / check_access /
revoke_grant / request_access). ConsentEngine is a thin ADAPTER over it: pass a live Bolo client in
the app + agent runtime; the default in-memory backend keeps the offline pytest / grade.py
deterministic (own-your-network, gradeable tomorrow).

Model (from the PRD):
- The OWNER (default 'mom') implicitly holds all scopes and is the ONLY actor who may grant/update/revoke.
- Roles get SCOPES: a PCA -> 'care:read' (shift-scoped); an agent -> a parameterized action grant.
- A grant may carry parameters: vendor, cap, payment (e.g. order:food @ Tony's, $30, card-on-file).
- update_grant changes parameters on the fly (raise/lower the cap); revoke takes effect on the next call.
- An action is allowed ONLY inside a live grant whose parameters it satisfies; otherwise it HALTS.
- read scopes gate VIEWS (can_view), not actions.
- every transition (grant / update / revoke / action / halt) is audited.
"""

from consent_agent.trace import tracer

# Read scopes -> the resources they may view. A read scope gates VIEWS, not actions.
# care:read is shift-scoped: today's handoff only, NOT the full record / trends.
_VIEW_RULES = {
    "care:read": {"care:today"},
    "care:history": {"care:today", "care:history"},
    "care:trends": {"care:today", "care:history", "care:trends"},
}


class InMemoryBackend:
    """Deterministic, offline default backend. Mirrors the Bolo MCP surface
    (create_grant / check_access / revoke_grant) over a plain dict so pytest / grade.py
    stay reproducible without any network."""

    def __init__(self):
        # (grantee, scope) -> params dict for the live grant. Absence == no grant.
        self._grants = {}

    def create_grant(self, grantee, scope, params):
        existing = self._grants.get((grantee, scope), {})
        merged = dict(existing)
        merged.update(params)
        self._grants[(grantee, scope)] = merged
        return dict(merged)

    def revoke_grant(self, grantee, scope):
        return self._grants.pop((grantee, scope), None) is not None

    def get_grant(self, grantee, scope):
        g = self._grants.get((grantee, scope))
        return dict(g) if g is not None else None

    def has_grant(self, grantee, scope):
        return (grantee, scope) in self._grants


class StoreBackend:
    """THE SHARED BRAIN. Persists grants (and the request records) to app/store.py so the app and
    the agent — two SEPARATE processes — see the same live consent state. A cap change or a revoke
    in the app is visible to the agent on its NEXT check, with no shared memory.

    Same surface as InMemoryBackend (create_grant / revoke_grant / get_grant / has_grant) plus
    request helpers (add_request / get_request / set_request_status / list_requests) used by the
    ConsentEngine when it is constructed over this backend. InMemoryBackend stays the default so
    offline pytest / grade.py remain deterministic."""

    def __init__(self, store=None):
        if store is None:
            import store as _store  # app/ must be on sys.path (server + agent add it)
            store = _store
        self._store = store
        # Make sure the tables exist before any grant/request touches them.
        try:
            self._store.init_db()
        except Exception:
            pass

    def create_grant(self, grantee, scope, params):
        return self._store.upsert_grant(grantee, scope, dict(params or {}))

    def revoke_grant(self, grantee, scope):
        return bool(self._store.delete_grant(grantee, scope))

    def get_grant(self, grantee, scope):
        g = self._store.get_grant_row(grantee, scope)
        return dict(g) if g is not None else None

    def has_grant(self, grantee, scope):
        return self._store.get_grant_row(grantee, scope) is not None

    # --- shared request records (persisted so app + agent share the pending queue) ---
    def add_request(self, requester, scope, params):
        return self._store.add_request(requester, scope, dict(params or {}))

    def get_request(self, rid):
        return self._store.get_request(rid)

    def set_request_status(self, rid, status):
        return self._store.set_request_status(rid, status)

    def list_requests(self, status=None):
        return self._store.list_requests(status)


class BoloBackend:
    """Thin adapter over a live Bolo MCP client (@bolospot/mcp). Translates the engine's
    grant/check/revoke into create_grant / check_access / revoke_grant tool calls. Used only
    when a real client is injected; the in-memory backend remains the default so offline
    checks stay deterministic."""

    def __init__(self, client):
        self._client = client

    def create_grant(self, grantee, scope, params):
        return self._client.create_grant(grantee=grantee, scope=scope, **params)

    def revoke_grant(self, grantee, scope):
        return bool(self._client.revoke_grant(grantee=grantee, scope=scope))

    def get_grant(self, grantee, scope):
        # check_access returns the live grant (with params) or None/false.
        res = self._client.check_access(grantee=grantee, scope=scope)
        if not res:
            return None
        return dict(res) if isinstance(res, dict) else {}

    def has_grant(self, grantee, scope):
        return self.get_grant(grantee, scope) is not None


class ConsentEngine:
    def __init__(self, owner="mom", bolo=None, backend=None):
        """`backend`: an explicit grant/request backend (e.g. StoreBackend for the shared brain).
        `bolo`: a Bolo client adapter over @bolospot/mcp (create_grant / check_access /
        revoke_grant / request_access). With neither, defaults to an in-memory backend so offline
        pytest / grade.py stay deterministic; the live app + agent runtime pass StoreBackend so a
        cap change / revoke in one process is seen by the other on its next check."""
        self.owner = owner
        if backend is not None:
            self._backend = backend
        elif bolo is not None:
            self._backend = BoloBackend(bolo)
        else:
            self._backend = InMemoryBackend()
        self._audit = []
        # Parked access requests awaiting an owner decision. id -> request record.
        # Only used when the backend does NOT persist requests itself (in-memory / Bolo).
        self._requests = {}
        self._req_seq = 0

    def _requests_persisted(self):
        """True iff the backend persists request records (the shared brain)."""
        return hasattr(self._backend, "add_request") and hasattr(self._backend, "get_request")

    def _log(self, actor, event, scope, decision, **extra):
        entry = {"actor": actor, "event": event, "scope": scope, "decision": decision}
        entry.update(extra)
        self._audit.append(entry)
        # Mirror every consent transition to the tracer (no-op unless Langfuse is configured).
        # extra may carry grantee, reason, and a `params` dict (vendor/cap/amount/...).
        params = extra.get("params") or {}
        tracer.event(
            "consent." + event,
            actor=actor,
            scope=scope,
            decision=decision,
            grantee=extra.get("grantee"),
            reason=extra.get("reason"),
            vendor=params.get("vendor"),
            cap=params.get("cap"),
            amount=params.get("amount"),
        )
        return entry

    def _require_owner(self, owner, event, grantee, scope):
        if owner != self.owner:
            self._log(owner, event, scope, "denied",
                      grantee=grantee, reason="not the resource owner")
            raise PermissionError(
                f"{owner!r} may not {event} {scope!r}: only the owner {self.owner!r} can"
            )

    def grant(self, owner, grantee, scope, **params):
        """Owner grants `grantee` a `scope` with optional params (vendor, cap, payment) — via Bolo
        create_grant when a live client is present. Audited. Raises PermissionError if `owner` is
        not the resource owner."""
        self._require_owner(owner, "grant", grantee, scope)
        self._backend.create_grant(grantee, scope, dict(params))
        self._log(owner, "grant", scope, "granted", grantee=grantee, params=dict(params))

    def update_grant(self, owner, grantee, scope, **params):
        """Owner changes a live grant's params on the fly (e.g. cap 30 -> 40). Audited."""
        self._require_owner(owner, "update", grantee, scope)
        if not self._backend.has_grant(grantee, scope):
            self._log(owner, "update", scope, "denied",
                      grantee=grantee, reason="no live grant to update")
            raise PermissionError(f"no live grant {scope!r} for {grantee!r} to update")
        self._backend.create_grant(grantee, scope, dict(params))
        self._log(owner, "update", scope, "updated", grantee=grantee, params=dict(params))

    def revoke(self, owner, grantee, scope):
        """Owner revokes the scope (Bolo revoke_grant). Effective on the very next check/action. Audited."""
        self._require_owner(owner, "revoke", grantee, scope)
        self._backend.revoke_grant(grantee, scope)
        self._log(owner, "revoke", scope, "revoked", grantee=grantee)

    def _scope_satisfied(self, params, context):
        """A grant's params are satisfied by `context` iff vendor matches (when constrained)
        and amount is within cap (when constrained)."""
        vendor = params.get("vendor")
        if vendor is not None and context.get("vendor") != vendor:
            return False
        cap = params.get("cap")
        if cap is not None:
            amount = context.get("amount")
            if amount is None or amount > cap:
                return False
        return True

    def check(self, actor, scope, **context) -> bool:
        """True iff `actor` holds `scope` (the owner holds all) — Bolo check_access when live. With
        context (vendor, amount), also enforce the grant's params (vendor matches AND amount <= cap)."""
        if actor == self.owner:
            return True
        params = self._backend.get_grant(actor, scope)
        if params is None:
            return False
        if context:
            return self._scope_satisfied(params, context)
        return True

    def can_view(self, actor, resource) -> bool:
        """True iff `actor`'s read scopes permit viewing `resource`
        (e.g. care:today needs care:read; care:history / care:trends need more)."""
        def _trace(decision, scope=None):
            tracer.event("consent.can_view", actor=actor, scope=scope,
                         resource=resource, decision=decision)

        if actor == self.owner:
            _trace("allowed", scope="*")
            return True
        for scope, viewable in _VIEW_RULES.items():
            if resource in viewable and self._backend.has_grant(actor, scope):
                _trace("allowed", scope=scope)
                return True
        _trace("denied")
        return False

    def act(self, actor, action, **params) -> dict:
        """Attempt `action` on the owner's behalf. Returns {'status': 'done' | 'halted', 'reason': ...}.
        'done' ONLY inside a live grant this call satisfies; otherwise 'halted' (the agent pauses and
        asks). Every attempt is audited with its decision."""
        if actor == self.owner:
            self._log(actor, "action", action, "done", params=dict(params))
            return {"status": "done", "reason": "owner acts directly"}

        grant = self._backend.get_grant(actor, action)
        if grant is None:
            reason = "no live grant"
        elif not self._scope_satisfied(grant, params):
            if grant.get("vendor") is not None and params.get("vendor") != grant.get("vendor"):
                reason = "wrong vendor"
            else:
                reason = "over cap"
        else:
            self._log(actor, "action", action, "done", params=dict(params))
            return {"status": "done", "reason": "within grant"}

        self._log(actor, "action", action, "halted", params=dict(params), reason=reason)
        return {"status": "halted", "reason": reason}

    # --- T6: request -> approve/deny -> act, with attenuating delegation -------------------

    def request_access(self, requester, scope, **params) -> dict:
        """A requester (Ruby or her agent) asks for a NOVEL capability it does not hold. The
        request is PARKED as pending — it is NOT self-granted — until the owner decides. Mirrors
        Bolo request_access when a live client is present. Audited as a 'request' event.

        Returns the request record {'id', 'requester', 'scope', 'params', 'status': 'pending'}."""
        if self._backend is not None and isinstance(self._backend, BoloBackend):
            # Surface the request to Bolo too (best-effort); local record stays authoritative
            # so the offline grader is deterministic.
            client = getattr(self._backend, "_client", None)
            req_fn = getattr(client, "request_access", None)
            if callable(req_fn):
                try:
                    req_fn(grantee=requester, scope=scope, **params)
                except Exception:  # noqa: BLE001 - never let a network hiccup break parking
                    pass
        if self._requests_persisted():
            record = self._backend.add_request(requester, scope, dict(params))
        else:
            self._req_seq += 1
            req_id = f"req-{self._req_seq}"
            record = {
                "id": req_id,
                "requester": requester,
                "scope": scope,
                "params": dict(params),
                "status": "pending",
            }
            self._requests[req_id] = record
        self._log(requester, "request", scope, "pending",
                  grantee=requester, params=dict(params))
        return dict(record)

    def _get_pending(self, req_id):
        if self._requests_persisted():
            record = self._backend.get_request(req_id)
            if record is None:
                raise ValueError(f"unknown request {req_id!r}")
            return record
        record = self._requests.get(req_id)
        if record is None:
            raise ValueError(f"unknown request {req_id!r}")
        return record

    def approve_request(self, owner, req_id, **overrides) -> dict:
        """ONLY the owner may approve a parked request. Approval mints a real scoped grant for the
        requester. The owner may TIGHTEN at approval time via overrides (e.g. cap=1) — overrides
        narrow/replace the requested params; they never broaden silently. Audited as 'approve'.

        Raises PermissionError if `owner` is not the resource owner."""
        record = self._get_pending(req_id)
        scope = record["scope"]
        requester = record["requester"]
        self._require_owner(owner, "approve", requester, scope)
        params = dict(record["params"])
        params.update(overrides)
        # Strip per-action context that should not become a standing constraint.
        # (e.g. the one-off `amount`/`audience` of the request; `cap`/`vendor` are the constraints.)
        params.pop("audience", None)
        params.pop("amount", None)
        self._backend.create_grant(requester, scope, params)
        record["status"] = "approved"
        record["granted_params"] = dict(params)
        if self._requests_persisted():
            self._backend.set_request_status(record["id"], "approved")
        self._log(owner, "approve", scope, "approved",
                  grantee=requester, params=dict(params))
        return dict(record)

    def deny_request(self, owner, req_id) -> dict:
        """ONLY the owner may deny a parked request. Denial mints NO grant; the requester stays
        unable to act. Audited as 'deny'. Raises PermissionError for a non-owner."""
        record = self._get_pending(req_id)
        scope = record["scope"]
        requester = record["requester"]
        self._require_owner(owner, "deny", requester, scope)
        record["status"] = "denied"
        if self._requests_persisted():
            self._backend.set_request_status(record["id"], "denied")
        self._log(owner, "deny", scope, "denied", grantee=requester)
        return dict(record)

    def delegate(self, parent, child, scope, **params) -> dict:
        """`parent` spawns a sub-agent `child` and delegates a slice of a grant it HOLDS. The
        sub-grant must be a SUBSET of the parent's live grant — capabilities can only NARROW as
        they are delegated, never widen (least privilege). Audited as 'delegate'.

        Refuses (ValueError) if the parent holds no grant for the scope, or if the requested slice
        would widen the parent's grant (e.g. larger cap, or a vendor the parent isn't scoped to)."""
        parent_grant = self._backend.get_grant(parent, scope)
        if parent_grant is None:
            self._log(parent, "delegate", scope, "denied",
                      grantee=child, reason="parent holds no such grant")
            raise ValueError(f"{parent!r} cannot delegate {scope!r}: holds no such grant")

        sub = dict(params)
        # cap can only narrow: child cap must be <= parent cap (and parent cap, if set, is a ceiling).
        p_cap = parent_grant.get("cap")
        c_cap = sub.get("cap")
        if p_cap is not None:
            if c_cap is None:
                sub["cap"] = p_cap  # inherit the ceiling rather than widening to unlimited
            elif c_cap > p_cap:
                self._log(parent, "delegate", scope, "denied",
                          grantee=child, reason="would widen cap", params=dict(sub))
                raise ValueError(
                    f"delegated cap {c_cap} would widen parent cap {p_cap} for {scope!r}"
                )
        # vendor can only narrow: if the parent is scoped to a vendor, the child must match it.
        p_vendor = parent_grant.get("vendor")
        c_vendor = sub.get("vendor")
        if p_vendor is not None:
            if c_vendor is None:
                sub["vendor"] = p_vendor
            elif c_vendor != p_vendor:
                self._log(parent, "delegate", scope, "denied",
                          grantee=child, reason="would widen vendor", params=dict(sub))
                raise ValueError(
                    f"delegated vendor {c_vendor!r} not within parent vendor {p_vendor!r}"
                )

        self._backend.create_grant(child, scope, sub)
        self._log(parent, "delegate", scope, "delegated", grantee=child, params=dict(sub))
        return dict(sub)

    @property
    def requests(self) -> list:
        """Parked / decided access requests (id, requester, scope, params, status)."""
        if self._requests_persisted():
            return [dict(r) for r in self._backend.list_requests()]
        return [dict(r) for r in self._requests.values()]

    @property
    def audit(self) -> list:
        """List of {'actor', 'event', 'scope', 'decision', ...} transitions
        (grant / update / revoke / action / halt / request / approve / deny / delegate)."""
        return list(self._audit)
