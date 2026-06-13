# Red Rover — Build Day PRD

**One-liner:** Care coordination for a vulnerable loved one, where **everyone *and every agent*
acting on their behalf operates under scoped, revocable consent** — powered by Bolo.

> The care app is the wedge. **The consent layer is the company.** Built on Bolo (@bolospot/mcp).

---

## 1. The problem
Being the care coordinator for a **disabled or elderly loved one is a second full-time job** —
the invisible *mental load* of mood, meds, appointments, aides, and providers who don't talk.

Now agents are arriving to help — and that exposes a dangerous gap: **delegation is all-or-nothing.**
You either hand a part-time aide (or an AI agent) the keys, or you babysit every action. For a
**vulnerable person, that's exactly what you can't get wrong.** There is no scoped, revocable,
auditable way to let someone — or something — act on their behalf.

## 2. Who
- **The coordinator** (Mom) — drowning in the mental load.
- **The care recipient** (Ruby, a disabled child — or an elderly parent) — whose dignity & safety are the point.
- **Part-time caregivers / PCAs** (Jane) — need *some* access, not all.
- **AI care-agents** — increasingly act on the family's behalf, and need the *same* governance as a human aide.

## 3. The insight (the wedge → the layer)
The thing missing isn't another care app. It's a **consent layer**: scoped, revocable, audited
permission for *anyone or anything* acting on a vulnerable person's behalf. The `mom_app` sketch
already proves it — **Mom holds full scopes (`mood:read`, `location:status`, `beacon:trigger`,
`wave:send`); Jane holds only `mood:read`.** Extend that same model to AI agents and you have the
trust fabric the agent economy is missing — first demonstrated where it matters most.

## 4. The product
**Red Rover** — a phone-first care companion (live mood, today's timeline, find-in-house, wave-hello
through the **MARS** robot) where **every actor — Mom, Jane, or an AI care-agent — operates only within
Bolo-granted scopes, revocable in real time, fully audited.**

## 5. Build-Day scope (NET-NEW today; dependencies allowed)
**In:**
- Companion view: mood ring + today's timeline + day summary (from the sketch, rebuilt fresh).
- **The permission story:** switch actor — **Mom (full) · Jane (`mood:read`) · AI Care-Agent (scoped)** — each gated by Bolo scopes; read-only actors visibly can't act.
- **THE MONEY SHOT:** an actor (agent or aide) attempts a scoped action mid-flow → the family **REVOKES consent → it HALTS → audit logs it live.** *(The `permit` consent engine, already working.)*
- Synthetic fixtures, **zero PHI** (visibly synthetic). MARS optional → mock/recorded if no robot.

**Out (explicitly):** real EHR, OAuth, multi-tenant, production hardening, push notifications.

## 6. The 3-minute demo
1. *"Coordinating care for a disabled or elderly loved one is a second full-time job."* (show the companion: Ruby's mood, timeline.)
2. *"Jane, the part-time aide, can see Ruby's mood — but nothing else."* (switch to Jane → read-only banner, actions disabled, scope `mood:read`.)
3. *"Now an AI care-agent helps — same rules. It coordinates a follow-up, within granted scope."* (agent acts.)
4. 🛑 **Revoke mid-flight** → *"I pull consent → it stops, instantly, before it shares anything."* (HALT + audit entry.)
5. **The altitude:** *"This is the consent layer **everyone and every agent** acting on someone's behalf needs — I'm showing it where it matters most. It's the trust fabric the agent economy doesn't have yet."*

## 7. Architecture
```
Red Rover UI (React, from the sketch)  ──►  permit consent engine (net-new today)
        │  scopes/roles                          │  grant · check · revoke · HALT · audit
        ▼                                         ▼
   MARS robot (mood/beacon/wave)            Bolo (@bolospot/mcp)  ── dependency
   [optional / mock / recorded]            scopes · grants · revoke · relay
                                            Langfuse ── audit → live dashboard
```

## 8. Metrics (the scoreboard judges believe)
Actions taken under a live grant · **actions without a logged grant = 0** · halted-on-revoke · % autonomous.

## 9. Why now · market · defensibility (the a16z section)
- **Why now:** agents are about to act on our behalf *everywhere*, and consent is still a one-time checkbox — not scoped, not revocable, not auditable. The agent economy has **no trust layer.**
- **Market:** every agent that acts for someone needs this — care is the wedge, but the layer is horizontal (finance, legal, scheduling, ops). Start where consent matters *most* (vulnerable people) and is *most painful* (caregiver load).
- **Why not Auth0:** Auth0 = a *human* logging a *pre-registered app* into a service (static scopes, central IdP, consent-at-connect). Agents are *dynamic, ephemeral, peer-to-peer*, need *runtime* scopes and **mid-flight revoke.** New primitive.
- **Why not guardrails:** guardrails govern an agent's *behavior* (operator-set). Bolo governs *authority/consent* (resource-owner-set, revocable). A *safe* action you're *not authorized* to take is the difference — guardrails pass it; Bolo halts it.
- **Defensibility:** neutral, cross-platform, **resource-owner-held revocable consent + identity** — the part incumbents won't build because it commoditizes their walls.

## 10. Team & assets
**Alison Cossette** — founder of **Bolo** (the consent layer), robotics background (MARS), author of the
*agentic-framing* methodology (confidence-gated autonomy, human-as-exception). Owns the wedge *and* the layer.

## 11. Roadmap
- **Build Day:** real Bolo + a Claude/Fable-5 care-agent + the live revoke-halt, on the care wedge.
- **Next:** multi-caregiver, richer scopes, agent-to-agent relay across families/providers, then horizontal (any delegated-agent action).
