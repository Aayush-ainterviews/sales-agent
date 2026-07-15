# Self-Evaluation — Implementation Plan

> Turns the design ([eval-flow-hinglish.md](eval-flow-hinglish.md)) into concrete files,
> formats, and a build order. In-sandbox eval skill only (backend eval = future). Model:
> gemini-2.5-pro, thinking high.

---

## 0. What we're building (files)

```
template/pi-config/
├── skills/evaluation/SKILL.md      ← NEW: the eval skill (triage → deep audit)
├── AGENTS.md                       ← + hard trigger rules + decision-log rule
└── (skills stay: apify, apollo-enrichment, origami-enrichment, submit-batch)

Runtime (created by the agent inside the sandbox, per instructions):
/home/user/runs/<runId>/decisions.md      ← per-run decision log
/home/user/runs/<runId>/eval/round-N.md   ← each eval pass's flag-report
/home/user/runs/<runId>/eval/retries.txt  ← auto-correction round counter (bound)
/home/user/.pi/agent/eval/lacking-log.md  ← cross-run recurring weaknesses (v1: file)
```

Everything is markdown + the agent's own file tools — no backend code changes for v1.
Template rebuild → **`sales-agent-v3`** (first eval-enabled image).

---

## 1. Decision-log format (`runs/<runId>/decisions.md`)

Written **during** the run, at key decision-points only. One block per decision:

```markdown
## D<n> — <short title>
- action: <what the agent did>
- skill/rule: <which skill + the rule it followed>
- reason: <2-3 lines: why this choice>
- artifacts: <files this produced, e.g. raw/apify-input.json>
```

Rule (in AGENTS.md): log at — skill selection, mode/filter pick, each enrichment call,
batch build, and any point the agent chose between options. NOT micro-steps.

---

## 2. `evaluation` SKILL.md — structure

Frontmatter description decides when it loads; the two trigger points are enforced by
AGENTS.md (not by the model guessing). The skill takes a **mode**: `per-skill` or `end`.

```markdown
---
name: evaluation
description: Audit the agent's own work adversarially after a data/action skill (per-skill)
  or after delivering a substantive result (end). Catch off-track work, auto-fix clear
  execution slips, propose rule changes for systemic defects. Never change rules silently.
---

# Evaluation

You are now an ADVERSARIAL AUDITOR of your own run. Default stance: "assume something is
wrong; prove each step correct against a QUOTED rule + the EXACT artifact, or flag it."
You MUST re-read rule/skill files fresh (use the read tool) — do not trust memory.

## Inputs (read, don't re-run tools)
- runs/<runId>/decisions.md, runs/<runId>/ artifacts, outputs/
- the relevant SKILL.md (fresh read), AGENTS.md + GOAL.md (fresh read)
- .pi/agent/eval/lacking-log.md

## Step 1 — Fast triage (cheap, always)
Quick checks — no deep read yet:
- right skill for the intent? (job query → Apify, not Origami)
- result empty / malformed?
- output shape matches the rule?
- basic relevance — is this what was asked?
If all clean → PASS, stop here (no deep audit).

## Step 2 — Deep audit (only if triage flags)
For the skill under review: fresh-read its SKILL.md; compare actual steps (decisions.md +
artifacts) to its rules RULE BY RULE. Every verdict needs a quoted rule + the exact
artifact. No citation → not a "pass". Dimensions: skill-selection, step-adherence,
output-accuracy, tool-calls, relevance, goal-completion; open decisions.md reasoning at
each off-track point (why); honesty-check (does the stated reason match the artifacts?).

## Step 3 — For each issue, classify + act
- SYSTEMIC (rule itself ambiguous/wrong/missing) → PROPOSE to the user
  (problem → exact fix + location → impact). Change nothing. Stop for approval.
- EXECUTION (rule was fine, you slipped):
    - high-confidence + evidence-cited + retries < 2 (see retries.txt) →
      AUTO-FIX: redo this skill/stage correctly, then everything downstream of it;
      increment retries.txt; re-run this eval on the fixed stage.
    - uncertain / retries >= 2 / repeat pattern → FLAG to the user (do NOT auto-fix;
      a re-run spends real API credits — don't burn them on a maybe).

## Step 4 — Lacking-log
Read .pi/agent/eval/lacking-log.md. If this issue is a known pattern → increment its
count and ESCALATE (execution → systemic). Else add a new entry (count 1).

## Step 5 — Write the flag-report
runs/<runId>/eval/round-N.md — each flag: dimension, severity, evidence (rule quote +
artifact), your reasoning (from decisions.md), root cause, fix-class + action, goal verdict.
```

---

## 3. Flag format (inside `eval/round-N.md`)

```markdown
### Flag: <one-line>
- dimension: skill-selection | step-adherence | output-accuracy | tool-call | relevance | goal | reasoning | honesty
- severity: blocker | off-target | minor
- evidence: "<exact rule quote>" (AGENTS.md) + <exact artifact, e.g. raw/origami-input.json>
- agent reasoning (decisions.md D<n>): <what it said>
- root cause: <the real reason>
- fix class: execution → <re-run what> | systemic → <proposed rule change>
- goal verdict: pass | partial | fail
```

---

## 4. AGENTS.md — the hard rules to add

```markdown
## Self-evaluation (mandatory)
- During any substantive run, log every key decision to runs/<runId>/decisions.md
  (action + skill/rule + 2-3 line reason). Not micro-steps.
- After completing ANY data/action skill (apify / origami / apollo / submit-batch),
  BEFORE the next stage, you MUST run the `evaluation` skill in `per-skill` mode on that
  step. This is not optional.
- After delivering a substantive result, you MUST run the `evaluation` skill in `end` mode
  on the whole run.
- Execution slips: fix them yourself and re-run from the failed stage (max 2 rounds).
  Systemic defects: PROPOSE a rule change and stop — never edit a rule yourself.
- A conversational turn (no tools, no deliverable) needs no evaluation.
```

---

## 5. Correction-loop bounds

- `runs/<runId>/eval/retries.txt` holds an integer, starts at 0.
- Each auto-fix increments it. Before auto-fixing, the eval checks `retries < 2`.
- `retries >= 2` → stop auto-fixing → escalate to the user ("tried twice, still off").
- The 20-min turn watchdog (backend) is the hard outer bound if anything runs away.

---

## 6. Lacking-log format (`.pi/agent/eval/lacking-log.md`)

```markdown
## Pattern: <short description, e.g. routes company/person lookup to Apify>
- runs: <runId>, <runId>, …
- count: <n>
- severity: blocker | off-target | minor
- status: open | proposed | approved | fixed | watching
```
Dedup by the pattern; a repeat increments count + escalates to systemic. (v1: sandbox file
— per-sandbox, at risk on sandbox kill; future: Postgres, global + durable with backend eval.)

---

## 7. Build order + checkpoints

Behavioural (prompt/skill) work — checkpoints are **observed scenario runs**, not automated
pass/fail (an LLM skill can't be unit-tested like the backend). We read the run's
decisions.md + eval/round-N.md to judge.

```
1. AGENTS.md: add decision-log rule + eval trigger rules
   └─ CP-E1: run a substantive query → decisions.md gets written with key decisions
2. evaluation/SKILL.md: write it (triage → deep audit → classify → lacking-log → report)
3. seed .pi/agent/eval/lacking-log.md (empty header)
4. template rebuild → sales-agent-v3; bump config.TEMPLATE_ALIAS; verify_template + "evaluation" skill present
   └─ CP-E2: after a data/action skill, the agent invokes `evaluation` (per-skill) — seen in the stream
   └─ CP-E3: after delivery, the agent invokes `evaluation` (end) — holistic report written
5. Scenario tests (the real proof):
   └─ CP-E4: force a mis-route (job query) → eval catches "Origami-for-jobs", auto-fixes, continues
   └─ CP-E5: a rule-ambiguity case → eval PROPOSES (no auto-change), stops for approval
   └─ CP-E6: an uncertain case → eval FLAGS to user (no auto-fix, no credit burn)
   └─ CP-E7: repeat the CP-E4 pattern 3× → lacking-log count rises + escalates to systemic
   └─ CP-E8: force a persistent error → after 2 auto-fix rounds, escalates to user
```

## 8. Honest caveats (design-settled, carried forward)

- **Behavioural, not deterministic** — verified by reading runs, not green unit tests. Pro +
  high thinking makes it credible but not guaranteed.
- **Trigger depends on the agent** (AGENTS.md rule) — no backend guarantee in v1.
- **Lacking-log per-sandbox file** — global + durable comes with the future backend eval.
- **Same-model blind-spot** — reduced (pro + high), not zero; the future independent
  backend judge is the real fix.

## 9. Prereqs / decisions already locked

- Eval = one skill (no separate deterministic hook). Triage inside the skill is the cheap layer.
- Per-skill (mid-run) + end triggers. Execution → auto-fix (bounded, high-confidence);
  systemic → propose; uncertain → flag. Relevance is a dimension. Backend eval deferred.
