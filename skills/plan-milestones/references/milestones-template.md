# `.harness/milestones.md` template

Each milestone uses this exact structure:

```markdown
# Milestones

## M1 — <Outcome>

Status: TODO

### Outcome

### Architecture

### As-Built

### Acceptance Criteria
- [ ]

### Baseline

### Evidence

### Validation

### Review

### Review Cycles
0

### Follow-ups
```

Repeat the `## M<n> — <Outcome>` block for each milestone.

## State ownership

Milestone states are TODO, IN_PROGRESS, REVIEW, DONE, BLOCKED, and DEFERRED.
`harnessctl init-plan` initializes TODO milestones with stable criterion IDs and
no tasks. `state.json` is authoritative; the Markdown file is a compact view.
The controller never writes either file directly. Mechanical commands own
transitions, split/deferred records, evidence, review-cycle accounting, and
completion. Do not add a competing status or copy reports into this view.

The renderer uses these headings in order. Empty evidence/validation/review
sections start as Pending; completion is established by structured evidence
and gates, not those placeholder paragraphs. Use state and result artifact
paths for current task/routing/review details. Preserve acceptance text and IDs.

Architecture lists the agreed component IDs advanced by this milestone, or N/A
when no architecture is agreed. As-built output is recorded before the closing
commit, not after DONE. Baseline commit/branch in state define the milestone
review diff. Execution requires Git and a clean substantive worktree; it does
not automatically commit pre-existing product edits.

Do not manually archive, reorder or rewrite existing milestone sections. The
initial planner preserves a valid existing pair. A conflicting or partial pair
requires explicit recovery, and the validator must pass before execution.
