# Stead — Build Day PRD

> *A new project built on the ideas from **Red Rover** (the prior `rubysredrover` voice agent) —
> not that project. Stead is the integrated care companion + consent layer.*

**One-liner:** A care companion for Ruby — a young woman with cerebral palsy — that lets everyone
caring for her **share her day in seconds instead of an hour**, see her health trend over time, and
lets **Ruby do things herself** (like order a pizza) that her language and cognition won't let her do
alone — where every caregiver *and every agent* acts only inside **scoped, revocable, audited consent.**

> The care companion is what the family *feels*. **The consent layer (Bolo) is what makes shared care
> safe — and it's the company.** Care is the wedge; the consent fabric is horizontal.

---

## 1. The problem — the mental load is a second full-time job
Caring for a loved one with cerebral palsy means carrying an exhausting, invisible mental load: mood,
meds, food intake, hygiene, sleep, appointments, and a health story that lives only in people's heads.

Two places it breaks down hardest:
- **The handoff.** When the PCA arrives, it takes *forever* to relay how Ruby is, how her day/night
  went, what she ate, meds, what to watch for. The same briefing, re-told, every shift.
- **The doctor visit.** Mom is asked "how has she been over the last few months?" and has to answer
  from memory — no longitudinal picture of trends.

And Ruby herself is boxed out of ordinary independence: her language and cognitive abilities don't let
her do things most people take for granted — like ordering a pizza — on her own.

## 2. Who it's for
- **Ruby** — the care recipient (cerebral palsy). Her **dignity, safety, and independence** are the point.
- **Mom** — the coordinator, drowning in the mental load; holds the full picture and the keys.
- **Jane (PCA)** — comes during the day; needs *Ruby's current picture fast*, and only her shift's worth.
- **The doctor** — periodic; needs **trends**, not raw data.
- **AI care-agents** — increasingly act on Ruby's / the family's behalf, and need the **same governance
  as a human aide** — scoped, revocable, audited.

## 3. What Stead does — two pillars, one consent fabric

### Pillar 1 — Continuity of care (kills the handoff load)
- **MARS (Innate) companion *senses* Ruby's day** — mood, presence, activity — the part nobody else
  can capture, passively and continuously, from Ruby herself.
- **PCAs log and receive by voice** — hands-busy caregivers *talk*; they don't type.
- **Instant shift handoff:** an agent turns the day's sensing + voice notes into a 20-second summary —
  "slept poorly, ate little at lunch, mood low this afternoon, meds on time, PT at 3."
- **A health record that builds over time** → trends → walk into the doctor and *answer the questions*
  ("appetite down ~30% over 3 weeks; more agitated afternoons").

### Pillar 2 — Ruby's own agency
- **A voice agent lets Ruby do things she can't do alone** — *order a pizza* — by scaffolding around
  her language and cognition. The agent acts **on Ruby's behalf**. This is independence and dignity,
  not a feature.

## 4. The consent layer (why it's safe — and why it's the company)
The thing missing isn't another care app. It's a **consent layer**: scoped, revocable, audited
permission for *anyone or anything* acting on a vulnerable person's behalf. **Access is per-actor on
login**, and **Mom is the owner** who issues grants. **Bolo (@bolospot/mcp)** governs **both**
dimensions at once:
- **Who sees what:** Mom = full · Jane (PCA) = her shift's care view only · doctor = trends only.
- **What agents may *do* on Ruby's behalf — as *parameterized*, owner-held grants Mom controls:**
  - share a **payment method** (credit card) with the phone agent — tokenized, not handed over;
  - scope it to a **specific restaurant** ("Tony's Pizza," not "the internet");
  - set and **change the authorized amount on the fly** (e.g. raise $30 → $40 *while the agent is
    mid-order*, from the app);
  - **revoke any of it instantly.**

- **Revocable in real time, fully audited.** Overstep (wrong vendor, over the cap, after revoke) →
  **HALT → logged live.**

**The app is Mom's live control surface.** Consent is a *dial she turns in real time* — raise the cap,
lower it, revoke — not a one-time checkbox. The agent that hits a limit **pauses and asks**; Mom answers
in the app and it resumes (or halts).

That last bullet is the product in one artifact: **delegating real authority — even a payment
credential — to an agent, narrowly and revocably.** No incumbent lets a resource owner do that.

This is the difference from guardrails (which govern an agent's *behavior*): Bolo governs *authority* —
a perfectly *safe* action you're *not authorized* to take still halts.

## 5. MARS (Innate) — companion, sensor, *and the agent's voice*
Less about the embodiment, more about the **sensing** — but the embodiment is *why the sensing works*:
**Ruby relates to a robot in a way she won't to a disembodied phone**, so she engages, and the robot
perceives her state so she doesn't have to articulate it. MARS is the capture interface on *her* side.

**MARS is also the agent's voice (output, not just input).** Ruby's agent **speaks through the robot** —
the pizza negotiation, and the consent moments, come out of MARS's speaker so the room *hears* it
("ordering from Tony's, $28, on the card Mom shared" … "consent revoked — stopping"). Embodiment is how
the agent's actions-on-her-behalf become present and legible, not a gimmick.

> **Demo safety (not a scope cut):** MARS is in the product and the vision. The agent loop also runs
> over a **recorded/simulated sensor feed**, so a robot glitch on stage never kills the demo. Robot
> live = the wow; recorded feed = the unkillable floor.

## 6. Build-Day scope (NET-NEW today; dependencies allowed)
**The pieces already exist in isolation** — MARS sensing, the `rubysredrover` voice agent, Bolo consent.
**What's net-new today is *actualization*: tying them into one loop that runs end-to-end** — and giving
the agent **a voice through the robot.** The win is the integrated system, not the individual parts.

**Build order — a verifiable tier/spiral** (each tier independently demoable; every higher tier keeps the
lower ones green; `grade.py` is the oracle): **T1 RBAC access → T2 care companion (handoff + tracking) →
T3 the live app → T4 agent acts on Ruby's behalf → T5 voice through MARS.** T1–T3 = the shippable care
companion (required for "done"); T4–T5 = scored stretch.

**In:**
- **Voice agent vocalized through MARS:** the agent's negotiation + consent events are *spoken by the
  robot*, so people hear it live.
- **Mom's control app (required):** per-actor login + Mom's live grant controls — **raise/lower the
  authorized amount on the fly, grant/revoke** — the surface where consent becomes a real-time dial.
- **Companion view:** Ruby's mood + today's timeline + day summary.
- **The instant handoff:** PCA arrives → 20-second day summary (vs. the hour-long verbal briefing).
- **Ruby's agency moment:** Ruby orders a pizza by voice — the agent acts for her, **within scope.**
- **The doctor view:** trends over time + agent helps answer "how has she been?"
- **THE MONEY SHOT:** an actor (agent or aide) attempts an action outside its grant mid-flow → the
  family **REVOKES → it HALTS → audit logs it live.**
- **Permissioning across actors (per-actor login):** **Mom (full + grant admin) · Jane (`care:read`,
  shift-scoped) · Ruby's agent (`order:food` @ restaurant=Tony's, cap=$30, card-on-file)** — each gated
  by Bolo; read-only actors visibly can't act. **Mom can change the cap / revoke live.**
- Synthetic fixtures, **zero PHI** (visibly synthetic).

**Out (explicitly):** real EHR, OAuth, multi-tenant, production hardening, push notifications.

## 7. The 3-minute demo
1. **The load.** *"Caring for someone with cerebral palsy is a second full-time job — and every shift
   starts with the same hour-long handoff."* (Show the companion: Ruby's mood + timeline.)
2. **Handoff in seconds.** Jane the PCA arrives → instant day summary, scoped to her shift (`care:read`,
   no edit, no history beyond today).
3. **Ruby's dignity.** *"Ruby can't order a pizza on her own — now she can."* She asks by voice → the
   agent orders from **Tony's Pizza, under the $30 cap Mom set, on the card Mom shared** — and the whole
   negotiation **comes out of MARS's speaker**, so the room *hears* it. (The room feels this one.)
   - **Grant-up, live:** total comes to **$34 — over the $30 cap.** The agent *pauses and asks.* Mom
     **raises the limit to $40 in the app, on the fly** → it proceeds. Consent is a dial, not a checkbox.
4. **The doctor.** Mom opens trends → agent answers *"appetite's been down three weeks."*
5. 🛑 **The money shot.** The agent reaches outside its grant — a different vendor, or over the cap (or
   Mom just revokes) → *"I pull consent → it stops, instantly, before it acts."* HALT + audit, live.
6. **The altitude.** *"This is the consent layer **everyone and every agent** acting on someone's behalf
   needs — shown where it matters most. The trust fabric the agent economy doesn't have yet."*

## 8. Architecture
```
        Ruby ◄──relates to──► MARS (Innate) ──senses(mood/presence/activity)──┐
                                                                              ▼
  Ruby ──voice──► care-agent (order pizza, on her behalf)            Living care record
  PCA  ──voice──► care-agent (log day / get handoff)        ─────►   (timeline · trends · summary)
                            │                                                 │
                            ▼  every action: check scope                      │ trends/Q&A
                   Bolo (@bolospot/mcp)  ── grant · check · revoke · HALT · relay
                            │                                                 │
                       Stead UI (React) ── actor switch: Mom · Jane · Ruby's agent
                            │
                       Langfuse ── audit → live dashboard
   MARS [live = wow / recorded feed = unkillable demo floor]
```

## 9. Metrics (the scoreboard judges believe)
Handoff time: ~hour → seconds · **actions without a logged grant = 0** · halted-on-revoke (yes) ·
% of agent cycles completed with no human · longitudinal trend captured over time.

## 10. Why now · market · defensibility (the a16z section)
- **Why now:** agents are about to act on our behalf *everywhere*, and consent is still a one-time
  checkbox — not scoped, not revocable, not auditable. The agent economy has **no trust layer.**
- **Market:** every agent that acts for someone needs this — care is the wedge, but the layer is
  horizontal (finance, legal, scheduling, ops). Start where consent matters *most* (vulnerable people)
  and is *most painful* (caregiver load).
- **Why not Auth0:** Auth0 = a *human* logging a *pre-registered app* into a service (static scopes,
  central IdP, consent-at-connect). Agents are *dynamic, ephemeral, peer-to-peer*, need *runtime* scopes
  and **mid-flight revoke.** New primitive.
- **Why not guardrails:** guardrails govern an agent's *behavior* (operator-set). Bolo governs
  *authority/consent* (resource-owner-set, revocable). A *safe* action you're *not authorized* to take
  is the difference — guardrails pass it; Bolo halts it.
- **Defensibility:** neutral, cross-platform, **resource-owner-held revocable consent + identity** — the
  part incumbents won't build because it commoditizes their walls.

## 11. Team & assets
**Alison Cossette** — founder of **Bolo** (the consent layer), robotics background (MARS/Innate), author
of the *agentic-framing* methodology (confidence-gated autonomy, human-as-exception). Prior work:
**`rubysredrover`** — a working voice agent with **real voice + real phone calls** (the Pillar-2 base).
Owns the wedge *and* the layer.

## 12. Roadmap
- **Build Day:** real Bolo + a Claude/Fable-5 care-agent + MARS sensing + the live revoke-halt, on the
  care wedge — both pillars (handoff + Ruby's agency).
- **Next:** multi-caregiver, richer scopes, longitudinal health insights, agent-to-agent relay across
  families/providers, then horizontal (any delegated-agent action).
