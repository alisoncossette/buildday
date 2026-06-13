# Build-Until-Green — a project-agnostic orchestration harness

> 📖 **The method/playbook is in [GUIDELINE.md](GUIDELINE.md).** This README is the runnable proof of it.

> The plan that scores the **Orchestration 15%** — regardless of which project you ship.
> Judges grade orchestration from the *brief, the rubric, and the workflow scripts*, and ask:
> **Is "done" verifiable by the model without a human? Is it simple and repeatable? Could another team rerun it tomorrow on a new problem?** This kit answers all three with a yes.

## The idea in one line
You define "done" as **machine-checkable** (`rubric.json`), a single command grades it with **no human** (`grade.py`), and a **dynamic workflow** loops build → adversarially-verify → grade until the grader exits `0`. The session log then *shows* the model catching and fixing its own breakage — which is exactly what the **Autonomy 15%** is graded on.

```
GOAL.md  ──▶  workflow/build-verify-loop.js  ──▶  grade.py  ──▶  exit 0 = done
  (target)        build → verify → grade            (the "done" oracle)
                        ▲                 │
                        └──── red? feed failures back ────┘
```

## What you swap per project (only these three)
| File | What it is | Swap it when |
|------|-----------|--------------|
| `BRIEF.md` | One paragraph: problem, who it's for, what done looks like. | New project. |
| `GOAL.md` | The single hill the model climbs (the `/goal` target). | New project. |
| `rubric.json` | The machine-checkable definition of done — the checks `grade.py` runs. | New project. |

Everything else — `grade.py`, `workflow/build-verify-loop.js`, `verifiers/` — is reusable as-is.

## Run it
```bash
cd orchestration-kit
python grade.py          # grades the current state, prints a scorecard, exits 1 until done
pytest -q tests          # the executable part of the definition of done
```
Then point Claude Code at the workflow to close the loop autonomously:
```
Workflow({ scriptPath: "orchestration-kit/workflow/build-verify-loop.js" })
```

## Rerun on a NEW problem tomorrow (the repeatability story judges ask for)
1. Rewrite `BRIEF.md` (one paragraph) and `GOAL.md` (one target).
2. Edit `rubric.json` — list the checks that mean "done" for the new project (tests, a URL, files).
3. Run the same `grade.py` and the same `build-verify-loop.js`. Nothing else changes.

## How each piece maps to the rubric
- **Orchestration 15%** → `rubric.json` (machine-verifiable done) + `grade.py` (one command, no human) + `build-verify-loop.js` (simple, rerunnable). Swappable inputs = "another team could rerun tomorrow."
- **Autonomy 15%** → the loop builds with **verifier sub-agents** and a **pytest gate**, so the session log shows the *model* (not a human) deciding "done" and self-correcting. Human approval is a deliberate *exception*, escalated only on low confidence — never a per-cycle dependency.
- **Demo 35%** → a green `grade.py` scorecard + a responding URL is a demo that *cannot fail in the room* (it runs offline on a hotspot).
- **Impact 35%** → carried by `BRIEF.md` (the actual problem you point the harness at).

## Files
- `BRIEF.md` — the one-paragraph brief (swap per project).
- `GOAL.md` — the `/goal` target (swap per project).
- `rubric.json` — machine-checkable definition of done (swap per project).
- `grade.py` — the autonomous grader / "done" oracle (reusable).
- `workflow/build-verify-loop.js` — the build→verify→grade dynamic workflow (reusable).
- `verifiers/` — sub-agent prompt specs: anti-gaming correctness verifier + rubric grader (reusable).
- `inspection_agent/` + `tests/` — a worked example contract that starts RED (replace with your project).
