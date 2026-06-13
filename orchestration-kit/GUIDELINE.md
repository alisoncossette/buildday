# The Build-Until-Green Orchestration Guideline

> **A repeatable method for orchestrating any one-day build so the *model* — not a human —
> decides when it's done, and the session log proves it.**
> This is the plan you bring to a build day *before* you've chosen a project. Swap three files
> and the same machine runs on tomorrow's problem.

It is designed to score two rubric axes directly:
- **Orchestration (15%)** — "done" is machine-verifiable, the setup is simple, and another team could rerun it tomorrow.
- **Autonomy (15%)** — the log shows the model self-verifying and self-correcting with few human interventions.

The runnable proof of everything below lives beside this file in `orchestration-kit/`.

---

## Your day, in order

**0. Frame before you build (the plan-before-code gate).** Before any implementation, fill a
one-page plan and confirm it: the **goal**, the **loop(s)** the agent runs, its **state**, the
**tests/evals** that define success, the **metrics** to watch, and the **demo plan** — plus the
two always-on layers: **observability** (so you can see what the agent did) and **agent
permissions** (so it can't do something it shouldn't). This gate is *mandatory for any agent
build* and it stops you from coding a system whose "done" you can't measure. Its output flows
straight into the three files below.

**1. Write the three files** — `BRIEF.md`, `GOAL.md`, `rubric.json` (templates below).
**2. Start red** — run `python grade.py`; confirm it fails, so a real target exists.
**3. Run the loop** — `build-verify-loop.js` builds → verifies → re-grades, unattended, until green.
**4. Capture the self-correction moment** — drop the verifier-caught-a-failure excerpt into `evidence/self-correction.md`.
**5. Harden the demo** — green on a hotspot with venue wifi off; record a 60–90s backup video.
**6. Submit early** — public repo, built-today history clean, 30-min buffer.

> Step 0 is the gate; **steps 1–6 are the rest of this guideline.** Don't start step 1 until the
> one-page plan from step 0 is filled and confirmed — that confirmation is what keeps the whole
> day measurable.

---

## TL;DR — the method in six steps

1. **Write "done" as a machine, not a wish.** Encode the definition of done as checks a script runs (`rubric.json`): tests, a responding URL, files that must exist.
2. **Make one command the only judge.** `grade.py` runs every check with no human and exits `0` only when done. This is the *single source of truth* for "are we finished."
3. **Start red on purpose.** Before any feature exists, the grader should fail. That red scorecard is the target the loop climbs.
4. **Let a workflow close the loop.** `build-verify-loop.js` runs *grade → build → adversarially-verify → re-grade* until the grader goes green.
5. **Verify in a fresh context, not by self-review.** Independent verifier sub-agents try to *refute* each fix (did it game the test?). Two of three must agree the fix is real before it counts.
6. **Let the log be the evidence.** Because the stop condition is the grader's exit code and the fixes are checked by separate agents, the transcript naturally shows the model catching its own breakage. That *is* the Autonomy score.

---

## Why this scores (artifact → rubric)

| Judge's question | The artifact that answers it |
|---|---|
| Is "done" verifiable by the model *without a human*? | `rubric.json` + `grade.py` (exit code is the verdict) |
| Is the orchestration simple and repeatable? | One grader command + one workflow; three swappable inputs |
| Could another team rerun the setup tomorrow on a new problem? | Swap `BRIEF.md` / `GOAL.md` / `rubric.json`; everything else is unchanged |
| Did the *model* catch failures (a test/check/verifier), not a human? | Verifier sub-agents + pytest gate, visible in the session log |
| Did it run long stretches without steering? | The loop runs unattended until green or budget exhausted |

---

## The three files you swap per project

Everything else in the kit is reusable as-is. These three are the only per-project work.

### 1. `BRIEF.md` — one paragraph (carries the **Impact 35%** story)
```
Problem:        <the broken process, or the tool you wish existed>
Who it's for:   <the specific real person who feels this pain>
Done looks like: every required check in rubric.json is green via `python grade.py`,
                 with the log showing the model fixing its own breakage.
```

### 2. `GOAL.md` — the single hill the model climbs (your `/goal` target)
```
Make `python grade.py` exit 0 with every required check green,
without a human writing any verdict the rubric measures.
```

### 3. `rubric.json` — the machine-checkable definition of done
A list of checks. `grade.py` runs each one and tallies a weighted score; required checks must all pass.

| `type` | What it asserts | Key fields |
|---|---|---|
| `file_exists` | a path exists and is non-empty | `path` |
| `pytest` | a test suite passes (exit 0) | `path` |
| `http` | a URL returns an expected status (works offline) | `url`, `expect_status` |
| `command` | any shell command exits 0 (optionally contains text) | `cmd`, `expect_stdout` |

Each check also takes `id`, `desc`, `weight`, and `required` (true/false). Example:
```json
{ "id": "state-machine", "type": "pytest", "path": "tests",
  "desc": "the core workflow's state transitions pass",
  "weight": 4, "required": true }
```

**Rule of thumb:** make the *behavior that proves your impact* a `pytest` check, make *"it's live and works offline"* an `http` check, and make *"the model self-corrected"* a `file_exists` check pointing at a logged excerpt.

---

## The loop

```
        ┌──────────────────────────────────────────────┐
        │                                              │
   grade.py ──red?──▶ builder agent ──▶ 3 verifier agents ──▶ re-grade
   (the judge)        (implements      (refute the fix:        │
        ▲              minimal real     gamed the test?)        │
        │              behavior)         2/3 must pass          │
        └────────────────── green? stop ◀──────────────────────┘
```

Run it:
```bash
# point Claude Code at the workflow; it runs unattended until grade.py is green
Workflow({ scriptPath: "orchestration-kit/workflow/build-verify-loop.js" })
```

The verifier sub-agents are specified in `verifiers/` — a correctness/anti-gaming checker and
a rubric grader that is the *only* agent allowed to declare "done" (and only from the exit code).

---

## How the session log earns the Autonomy 15%

You don't *claim* autonomy — you make it fall out of the mechanics:
- The **stop condition is a machine** (grade.py exit code), so no human says "looks done."
- **Fixes are graded by separate agents**, so the log contains an explicit moment of a verifier
  rejecting a fix as test-gaming and forcing a real rebuild. Capture that excerpt into
  `evidence/self-correction.md` — the rubric has a check that looks for it.
- **Human approval is an exception, not a gate.** If your project has a human-in-the-loop step,
  the *agent* escalates only the low-confidence cases; the routine ones auto-resolve. Show the
  human moment **once** as designed oversight, never per cycle.

---

## Pre-flight checklist

- [ ] `BRIEF.md` names a **specific** user and problem (not "users" / "businesses").
- [ ] `GOAL.md` is one sentence and machine-checkable.
- [ ] `rubric.json` has ≥1 `pytest` check (the behavior), ≥1 `http` check (it's live, offline), and the self-correction evidence check.
- [ ] `python grade.py` runs and is **red** before you build (a real target exists).
- [ ] The build-verify workflow runs unattended and turns it **green**.
- [ ] `grade.py` passes on a **personal hotspot with wifi off in the demo path** (own your network).
- [ ] New public repo; commit history starts today (built-today line is clean).
- [ ] A 60–90s backup demo video is recorded (the demo that can't fail in the room).

---

## Rerun on a new problem tomorrow (the repeatability story)

1. Rewrite `BRIEF.md` (one paragraph) and `GOAL.md` (one target).
2. Edit `rubric.json` — list the checks that mean "done" for the new project.
3. Run the same `grade.py` and the same `build-verify-loop.js`. **Nothing else changes.**

That sentence — *"swap three files, rerun the same harness"* — is the literal answer to the
judges' "could another team rerun this tomorrow?" Say it in the demo.

---

## Anti-patterns that lose points

- **Test-gaming.** A fix that hardcodes the expected value or weakens an assertion. The
  adversarial verifiers exist to catch exactly this; never disable them to "go faster."
- **Blurred prior work = instant DQ.** Treat any pre-existing backend as *infrastructure*
  (like Postgres/AWS). The submitted, demoed artifact is the orchestration layer built today.
  Say one honest sentence about what's prior and only demo today's behavior.
- **A security framing.** Cyber/command-firewall framings get routed to a different model and
  lose the long-horizon edge this method is built on. Frame the work as the *job to be done*,
  not as "blocking bad commands."
- **A dashboard as the main feature** (banned) — the dashboard is a *view* of the loop, never the point.
- **Putting fragile hardware on the critical path.** If a robot/mic is unreliable, it's B-roll;
  the loop running over a recorded/simulated feed is the load-bearing demo.

---

## Proof it runs

`orchestration-kit/` is this guideline made real and already executed:
- `python grade.py` prints a scorecard and exits `1` at **1/9 (11%)** today — correctly red, because
  the example behavior is unbuilt on purpose.
- The four `pytest` transition checks fail with `NotImplementedError`; the `http` check reports the
  URL down. That red state is the target the build-verify workflow climbs to green.

Swap the three files and this same machine grades *your* project.
