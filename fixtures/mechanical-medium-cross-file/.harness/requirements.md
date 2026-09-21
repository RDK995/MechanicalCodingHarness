# Requirements

## Functional Requirements

- [FR1] `quote(subtotal, tax_rate)` threads the caller-provided tax rate through the pricing boundary and returns the correctly taxed total.

## Constraints

- Preserve the public `quote` function and the pricing/tax module boundary.
- Do not replace the caller-provided rate with a global or default.

## Decisions / Clarifications

- Subtotals and rates are numeric; currency rounding is out of scope.

## Open Questions

None.
