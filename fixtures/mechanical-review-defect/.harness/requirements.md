# Requirements

## Functional Requirements

- [FR1] An oversized withdrawal raises `InsufficientStock` without changing on-hand stock or recording a history entry.

## Constraints

- Rejection is atomic: mutation followed by an exception is not acceptable.

## Decisions / Clarifications

- Both stock and history must remain unchanged after rejection.

## Open Questions

None.
