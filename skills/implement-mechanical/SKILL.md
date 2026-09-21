---
name: implement-mechanical
description: Opt-in experimental implementation workflow that drives one agreed milestone through mechanically gated tasks, independent verification, and fresh review with bounded model routing.
context: fork
agent: harness:mechanical-controller
model: sonnet
effort: medium
---

Drive exactly one current milestone using the mechanical controller. This path
is opt-in while it is evaluated; do not invoke `harness:implement` or
`harness:orchestrator`, and do not perform an overall project review.

Require `.harness/requirements.md`, `.harness/milestones.md`, and
`.harness/state.json`. If requirements remain ambiguous, architecture exists but
is not agreed, runtime preflight fails, or the controller returns BLOCKED,
surface that result unchanged rather than falling back to the legacy workflow.

The controller owns the complete one-milestone invocation. Its `DONE`, `SPLIT`,
`CONTINUE`, or `BLOCKED` contract is the skill result. After DONE or SPLIT, stop
even if another milestone remains; start it only in a fresh context.
