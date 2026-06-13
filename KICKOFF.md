# 🚀 Red Rover — Build Day Kickoff Brief

**Project:** Red Rover · **Builder:** Alison Cossette (solo) · **Event:** Fable 5 Build Day
**Built on:** Bolo (@bolospot/mcp) + Claude (Fable 5) · **Demo:** localhost web app (runs on laptop)

---

## What we're building (one sentence)
**A care-coordination companion where every actor — a human aide *or* an AI agent — acts only within
scoped, revocable consent.** The care app is the wedge; **the consent layer is the company.**

## Why (the problem)
Coordinating care for a **disabled or elderly loved one is a second full-time job** — and as agents
arrive to help, delegation is **all-or-nothing**: hand over the keys, or babysit every action. For a
vulnerable person, there's no safe, scoped, revocable way to let *anyone or anything* act on their behalf.

## Why it wins THIS room (a16z + Fable 5)
- **Not an app — a layer** the whole agent economy needs (authority/consent for anything acting on your behalf).
- **Defensible:** not Auth0 (dynamic agents · runtime scopes · *mid-flight revoke*); not guardrails (authority, not behavior); **resource-owner-held + revocable.**
- **The depth (your sharpest point):** real agent management **isn't binary — consent tracks *shifting intent*.** The agent re-checks authority against its *current* intent every step; drift past the granted envelope → halt/escalate. *That* is what makes it more than an approve button.

## The demo (3 minutes — the money shot)
1. Companion: Ruby's live mood + timeline.
2. Switch actor: **Mom (full) → Jane the aide (`mood:read` only, can't act) → AI care-agent (scoped).**
3. The agent coordinates a follow-up — within its grant.
4. 🛑 **Revoke mid-flight → it HALTS before sharing → audit logs it live.**
5. Altitude: *"This is the consent layer every agent acting on someone's behalf needs — shown where it matters most."*

## Scope (ruthless — solo, today)
**IN:** companion view · scoped-actor switch · **the revoke-halt** · audit + scoreboard · synthetic data (no PHI) · **real Bolo**.
**OUT:** EHR, OAuth, multi-tenant, production hardening, push notifications, the full intent-drift engine (pitch it, don't build it today).

## Critical path
1. **Prove real Bolo** live (`lookup_handle` → `create_grant` → `check_access` → `revoke_grant`). ← gate.
2. Swap the demo's mock → **real Bolo**.
3. **Scoped-actor view** (Mom / Jane / Agent), read-only visibly can't act.
4. **Claude/Fable-5 agent** does the coordination (not scripted).
5. **Langfuse** → audit becomes a live dashboard.
6. **Demo-freeze + record backup video (~hour 6).** Submit.

## Definition of done
The **revoke-halt fires live on real Bolo**, on screen, with an audit trail — and it's **recorded.** Everything else is upside.

## Scoreboard (say this to judges)
Actions under a live grant · **actions without a logged grant = 0** · halted-on-revoke · % autonomous.

## Assets in hand
Working `permit` demo (halt verified) · this PRD/brief · **Bolo** (yours) · MARS (optional embodiment) · the *agentic-framing* method.

## Next 3 actions (right now)
1. **Test Bolo live** (need `BOLO_API_KEY` set for the MCP).
2. Wire Bolo into `permit/app.py`.
3. Mine `bolo` + `robohacks` sketches for the **intent-shift** model (reference only — submission stays net-new).
