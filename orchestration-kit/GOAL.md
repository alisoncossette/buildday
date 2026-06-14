Make `python grade.py` exit 0 — a verifiable TIER/SPIRAL where each tier is an independently demoable
checkpoint and every higher tier keeps the lower ones green. Build order (RBAC first, care companion next):

  T1  RBAC access        per-actor roles & scopes · owner-held grant/check/revoke · audited     [required]
  T2  care companion     instant PCA handoff summary + longitudinal health tracking             [required]
  T3  live app           per-actor login + Mom's real-time controls, served offline             [required]
  T4  act on behalf      agent orders within grant · HALT on overstep/revoke · raise-cap live   [stretch]
  T5  voice through MARS  agent vocalizes negotiation + consent events                          [stretch]
  T6  request & delegate  novel capability: agent/Ruby requests -> owner approves -> minted grant;
                          spawned sub-agent grants ATTENUATE (never widen); deny blocks; audited     [stretch]

Every actor and every agent acts ONLY inside scoped, revocable, owner-held, audited consent — without a
human writing any verdict the rubric measures. Subject to PRD.md.
