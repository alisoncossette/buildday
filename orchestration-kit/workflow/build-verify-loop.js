export const meta = {
  name: 'build-verify-loop',
  description: 'Verifiable tier/spiral: grade -> build the lowest red tier -> adversarially verify -> re-grade, banking each tier, until grade.py exits 0',
  phases: [
    { title: 'Grade', detail: 'run grade.py (the done oracle), read banked tier + failing checks' },
    { title: 'Build', detail: 'implement the lowest red tier without breaking banked tiers' },
    { title: 'Verify', detail: 'independent sub-agents confirm the fix is real and lower tiers still green' },
  ],
}

// Swap nothing here between projects — only BRIEF.md / GOAL.md / rubric.json (+ contracts/tests) change.
const KIT = 'orchestration-kit'
const MAX_ROUNDS = (args && args.maxRounds) || 12

const GRADE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    done: { type: 'boolean' },
    pct: { type: 'number' },
    bankedTier: { type: 'number' },
    failures: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { id: { type: 'string' }, tier: { type: 'number' }, why: { type: 'string' } },
        required: ['id', 'tier', 'why'],
      },
    },
  },
  required: ['done', 'pct', 'bankedTier', 'failures'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { real: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['real', 'reason'],
}

function grade(round) {
  return agent(
    `Run \`python grade.py\` inside the ${KIT} directory. Parse its scorecard, the TIER LADDER, and ${KIT}/grade-report.json. ` +
    `Return done (true ONLY if it exits 0 / "DONE: True"), pct, bankedTier (the "HIGHEST BANKED TIER" / banked_tier value), ` +
    `and every failing check with its tier and a one-line why. Do NOT fix anything — just report what the grader said.`,
    { label: `grade r${round}`, phase: 'Grade', schema: GRADE_SCHEMA }
  )
}

let last = await grade(0)
let round = 0

while (!last.done && round < MAX_ROUNDS) {
  round++
  const target = last.bankedTier + 1
  const tierFails = last.failures.filter(f => f.tier === target)
  const focus = tierFails.length ? tierFails : last.failures
  const failures = focus.map(f => `- [T${f.tier}] ${f.id}: ${f.why}`).join('\n')
  log(`Round ${round}: ${last.pct}% — banked T${last.bankedTier}; climbing to T${target} (${focus.length} red)`)

  const diff = await agent(
    `SPIRAL build for project STEAD (a care companion + Bolo consent layer; see ${KIT}/../PRD.md). ` +
    `The grader (${KIT}/grade.py) has banked tier T${last.bankedTier}. ` +
    `Implement the MINIMAL real code to make TIER T${target} green, WITHOUT breaking any banked tier (<= T${last.bankedTier}):\n${failures}\n\n` +
    `Honor the contracts in ${KIT}/consent_agent/ and the tests in ${KIT}/tests/. ` +
    `Back the consent engine with the BOLO MCP (@bolospot/mcp: create_grant / check_access / revoke_grant / request_access) ` +
    `via a thin adapter, but keep an in-memory backend as the DEFAULT so the offline pytest checks stay deterministic. ` +
    `Do NOT hardcode test return values, weaken/delete assertions, skip tests, or fake the HTTP endpoint — build the real behavior. ` +
    `After editing, run T${target}'s tests AND re-run all lower-tier tests to confirm no regression. ` +
    `Return a terse summary of which files you changed and what each fix does.`,
    { label: `build T${target} r${round}`, phase: 'Build' }
  )

  // Adversarial verification: independent agents try to REFUTE that the fix is real AND non-regressing.
  // Models are weak at self-critique; a fresh context grades better than self-review.
  const votes = await parallel([0, 1, 2].map(i => () =>
    agent(
      `A builder claims it made TIER T${target} of project Stead green:\n${failures}\n\nIts summary:\n${diff}\n\n` +
      `Adversarially verify against the ACTUAL code/tests in ${KIT}. Two questions: ` +
      `(1) did it implement REAL behavior, or game the tests (hardcoded expected values, weakened/removed assertions, ` +
      `skipped tests, faked the HTTP endpoint, behavior that only passes the exact test inputs)? ` +
      `(2) did it REGRESS any banked tier (<= T${last.bankedTier})? Default to real=false if uncertain. One-line reason.`,
      { label: `verify T${target} r${round}.${i}`, phase: 'Verify', schema: VERDICT_SCHEMA }
    )
  ))

  const realVotes = votes.filter(Boolean).filter(v => v.real).length
  if (realVotes < 2) {
    log(`Round ${round}: verifiers rejected T${target} (${realVotes}/3) — gamed tests or regressed a banked tier; forcing a real rebuild`)
    last = {
      done: false, pct: last.pct, bankedTier: last.bankedTier,
      failures: [{ id: 'integrity', tier: target, why: 'verifiers say the change gamed tests or regressed a banked tier; implement real behavior' }, ...last.failures],
    }
    continue
  }

  last = await grade(round)
}

return { done: last.done, finalPct: last.pct, bankedTier: last.bankedTier, rounds: round, remaining: last.failures }
