export const meta = {
  name: 'build-verify-loop',
  description: 'Project-agnostic build-until-green loop: grade -> build -> adversarially verify -> re-grade, until grade.py exits 0',
  phases: [
    { title: 'Grade', detail: 'run grade.py (the done oracle), read failing checks' },
    { title: 'Build', detail: 'implement minimal real behavior for the red checks' },
    { title: 'Verify', detail: 'independent sub-agents confirm the fix is real, not test-gaming' },
  ],
}

// Swap nothing here between projects — only BRIEF.md / GOAL.md / rubric.json change.
const KIT = 'orchestration-kit'
const MAX_ROUNDS = (args && args.maxRounds) || 6

const GRADE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    done: { type: 'boolean' },
    pct: { type: 'number' },
    failures: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { id: { type: 'string' }, why: { type: 'string' } },
        required: ['id', 'why'],
      },
    },
  },
  required: ['done', 'pct', 'failures'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { real: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['real', 'reason'],
}

function grade(round) {
  return agent(
    `Run \`python grade.py\` inside the ${KIT} directory. Parse its scorecard and ${KIT}/grade-report.json. ` +
    `Return done (true ONLY if it exits 0 / "DONE: True"), the pct, and every failing check with a one-line why. ` +
    `Do NOT fix anything — just report what the grader said.`,
    { label: `grade r${round}`, phase: 'Grade', schema: GRADE_SCHEMA }
  )
}

let last = await grade(0)
let round = 0

while (!last.done && round < MAX_ROUNDS) {
  round++
  const failures = last.failures.map(f => `- ${f.id}: ${f.why}`).join('\n')
  log(`Round ${round}: ${last.pct}% — ${last.failures.length} red check(s)`)

  const diff = await agent(
    `The definition-of-done grader (${KIT}/grade.py) reports these RED checks:\n${failures}\n\n` +
    `Implement the MINIMAL real code to make them pass. Honor the contract in ${KIT}/consent_agent/__init__.py ` +
    `and the tests in ${KIT}/tests/. Do NOT hardcode test return values, weaken/delete assertions, or skip tests — ` +
    `build the actual behavior. After editing, run the relevant tests yourself. ` +
    `Return a terse summary of which files you changed and what each fix does.`,
    { label: `build r${round}`, phase: 'Build' }
  )

  // Adversarial verification: independent agents try to REFUTE that the fix is real.
  // Models are weak at self-critique; a fresh context grades better than self-review.
  const votes = await parallel([0, 1, 2].map(i => () =>
    agent(
      `A builder claims it fixed these checks:\n${failures}\n\nIts summary:\n${diff}\n\n` +
      `Adversarially verify against the ACTUAL code/tests in ${KIT}. Did it implement REAL behavior, or game the tests ` +
      `(hardcoded expected values, weakened/removed assertions, skipped tests, faked the HTTP endpoint, behavior that only ` +
      `passes the exact test inputs)? Default to real=false if uncertain. One-line reason.`,
      { label: `verify r${round}.${i}`, phase: 'Verify', schema: VERDICT_SCHEMA }
    )
  ))

  const realVotes = votes.filter(Boolean).filter(v => v.real).length
  if (realVotes < 2) {
    log(`Round ${round}: verifiers rejected the fix as test-gaming (${realVotes}/3) — forcing a real rebuild`)
    last = {
      done: false, pct: last.pct,
      failures: [{ id: 'integrity', why: 'verifiers say the last change gamed the tests; implement real behavior' }, ...last.failures],
    }
    continue
  }

  last = await grade(round)
}

return { done: last.done, finalPct: last.pct, rounds: round, remaining: last.failures }
