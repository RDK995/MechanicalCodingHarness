---
name: implement-mechanical
description: Execute one milestone through mechanically gated tasks, independent verification and fresh review with bounded model routing.
context: fork
agent: harness:mechanical-controller
model: sonnet
effort: medium
---

Drive exactly one current milestone using the mechanical controller. Require
`.harness/requirements.md`, `.harness/milestones.md`, and `.harness/state.json`.
If planning files are missing, direct the user to `/harness:plan-milestones` and
stop. Do not plan in the execution context.

The controller runs `execution-status` before branch creation or dispatch.
Requirements must have no unresolved questions and architecture, when present,
must be AGREED. Missing runtime capabilities, unfinished legacy work, or a
failed gate must stop execution without falling back to legacy agents.

Return its compact DONE, SPLIT, CONTINUE, COMPLETE, or BLOCKED result unchanged.
A missing terminal field is INTERRUPTED, never success. After DONE or SPLIT,
stop even if another milestone remains. CONTINUE resumes in a fresh context;
COMPLETE terminates without another dispatch or project-level review.
