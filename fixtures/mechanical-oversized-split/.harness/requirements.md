# Requirements

## Functional Requirements

- [FR1] The API accepts cancellation for a running job and exposes the final cancellation state.
- [FR2] The scheduler owns cancellation propagation and prevents new work after cancellation.
- [FR3] Workers shut down safely while preserving already completed output.
- [FR4] A live readiness probe demonstrates cancellation and shutdown behavior across the running service.

## Constraints

- Cancellation crosses the API, scheduler, worker, and live-service boundaries.
- Implementation and live proof must not share one unreviewable change set.

## Decisions / Clarifications

- Each boundary must be delivered as an independently observable vertical slice.

## Open Questions

None.
