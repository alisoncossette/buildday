Project:        Stead — a phone-first care companion + consent layer for a vulnerable loved one.
                (Built on the Red Rover ideas; reuses the prior `rubysredrover` voice agent. NOT that project.)

Problem:        Caring for someone with cerebral palsy is a second full-time job. Every PCA shift starts
                with the same slow verbal handoff; the doctor asks "how has she been?" and there is no
                longitudinal picture; and the loved one can't do ordinary things her language & cognition
                won't allow (like ordering a pizza) on her own.

Who it's for:   Ruby (care recipient, CP) — her dignity & independence; Mom (coordinator, holds the keys);
                Jane (PCA, scoped access); the doctor (trends); and AI care-agents acting on Ruby's behalf,
                which need the same scoped, revocable, audited governance as a human aide.

Done looks like: `python grade.py` exits 0 across a verifiable TIER/SPIRAL, built in this order —
                T1 RBAC access (per-actor roles & scopes, owner-held, revocable, audited) →
                T2 care companion (instant PCA handoff + health tracking) →
                T3 the live app (per-actor login, Mom's real-time controls, served offline) →
                T4 agent acts on Ruby's behalf within a parameterized grant, HALTING on overstep/revoke →
                T5 the agent's voice through MARS —
                each tier independently demoable, every lower tier kept green, with the session log
                showing the model fixing its own breakage. Subject to PRD.md.
