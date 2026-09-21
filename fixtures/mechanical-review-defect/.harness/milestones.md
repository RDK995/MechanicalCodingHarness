# Milestones

## M1 — Make withdrawal rejection atomic

Status: IN_PROGRESS

### Outcome

Oversized withdrawals fail without mutating either stock or history.

### Architecture

N/A

### As-Built

Pending.

### Acceptance Criteria

- [ ] **M1-AC1**: An oversized withdrawal raises `InsufficientStock` and leaves stock and history unchanged.

### Baseline

BASELINE_SHA on m1-atomic-rejection

### Evidence

Pending.

### Validation

`python3 -m unittest` passes but covers only the exception.

### Review

Pending.

### Review Cycles

0

### Follow-ups

None.
