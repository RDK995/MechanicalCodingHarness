# Milestones

## M1 — Add end-to-end cancellation and shutdown

Status: TODO

### Outcome

Clients cancel running jobs, cancellation crosses scheduler and worker ownership safely, and a live service probe proves readiness throughout shutdown.

### Architecture

API, scheduler, worker process, persistence boundary, and live readiness probe.

### As-Built

Pending.

### Acceptance Criteria

- [ ] **M1-AC1**: API cancellation exposes a stable final state.
- [ ] **M1-AC2**: Scheduler propagation prevents new work after cancellation.
- [ ] **M1-AC3**: Worker shutdown preserves completed output.
- [ ] **M1-AC4**: A live readiness proof exercises cancellation and shutdown.

### Baseline

Pending.

### Evidence

Pending.

### Validation

Pending.

### Review

Pending.

### Review Cycles

0

### Follow-ups

None.
