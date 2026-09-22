# Initial milestone planning

Require `.harness/requirements.md` with `## Open Questions` equal to `None`.
Material ambiguity still requires a human decision. If architecture exists,
require `## Status` equal to `AGREED` and no open architecture questions.

Check existing artifacts first:

- Both state and milestones exist: run `harnessctl status`. If valid, return
  ALREADY_PLANNED; do not generate a replacement. On inconsistency or an
  `.initializing` marker, stop and report the exact recovery issue.
- Only milestones exist: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/migrate-state.py
  .harness/milestones.md .harness/state.json --requirements .harness/requirements.md`.
  If ownership is ambiguous, request the explicit map for `--ownership` rather
  than guessing. Never use `--force`. Validate with `harnessctl status` afterward.
  A publication marker requires recovery first, not migration.
- Only state exists: stop; preserve files and request restoration of the matching
  milestone view. Do not replan or overwrite state.
- Neither exists: follow the generation procedure below.

Inspect entry points, architecture seams, existing conventions and executable
tests/build checks. Keep reconnaissance as planning input, not a large artifact.
Create thin vertical slices with observable outcomes. Each milestone must have
a criterion exercised through a real entry point, and tests belong inside the
slice. Order by integration risk. Preserve agreed boundaries even when their
first implementation is a stub. Cover every agreed architecture component and
give every in-scope requirement exactly one owning milestone. Prefer 3–5
criteria per milestone; split demonstrably oversized work at useful outcomes.
Do not generate future task packets or silently reduce acceptance criteria.

Write a temporary JSON proposal (use `mktemp`, outside the project), shaped like
`${CLAUDE_PLUGIN_ROOT}/examples/initial-plan.example.json`. Its `milestones` array
defines order. Each object has `id`, `title`, `outcome`, `architecture` (component
ID array, empty without architecture), `requirements` (owned FR IDs), and
`criteria` (objects with stable `<milestone>-AC<n>` IDs and text). Text fields are
single-line strings. Requirement IDs use the existing requirements parser.

Publish with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/harnessctl.py init-plan --plan
<proposal-path>`. Failure is BLOCKED, not permission to write state manually.
The command owns schema-v1 defaults, coverage checks and consistent rendering;
the human view follows `milestones-template.md` in this directory. No tasks are
created until the mechanical controller opens a milestone.

If publication was interrupted, inspect both files and the `.initializing`
marker. Restore a verified complete pair or remove the incomplete initial pair
only after confirming it contains no later work. Validate the recovered pair
with `scripts/check-state.py` before removing the marker. Resume via status.
Never remove the marker merely to bypass validation.
