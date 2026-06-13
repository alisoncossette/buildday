Problem:        Agents increasingly act AS us — including in our own (cloned) voice — and there's
                no scoped, revocable, owner-held consent. Once an agent has your voice, you can't
                cleanly pull it back, and it's all-or-nothing.
Who it's for:   Anyone whose voice/likeness an agent speaks as — starting with families coordinating
                care for a disabled or elderly loved one, and the loved ones being spoken for.
Done looks like: every required check in rubric.json is green via `python grade.py` — a voice agent
                that speaks as the owner ONLY within a live grant and SHIFTS to a generic voice the
                instant consent is revoked — with the session log showing the model fixing its own breakage.
