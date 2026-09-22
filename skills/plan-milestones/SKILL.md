---
name: plan-milestones
description: Create initial milestones and structured state from agreed requirements before mechanical implementation; preserve existing plans.
context: fork
agent: harness:milestone-planner
model: sonnet
effort: medium
---

Prepare the initial milestone plan using the milestone-planner agent. Require
agreed `.harness/requirements.md` and AGREED architecture when present. Planning
ends before implementation; return PLANNED, ALREADY_PLANNED, or BLOCKED.

The planning procedure and proposal contract are in
`${CLAUDE_PLUGIN_ROOT}/skills/plan-milestones/references/planning.md`.
After PLANNED or ALREADY_PLANNED, start `/harness:implement` in a fresh context.
