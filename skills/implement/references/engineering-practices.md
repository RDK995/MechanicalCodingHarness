# Engineering practices for scoped mechanical work

For changed behaviour, identify or write an observable test and confirm its
expected failure before implementation where feasible. Make the smallest
complete change, then run the exact packet oracle through the validation ledger.
Refactor only within the task scope and rerun validation after changes.

Preserve public APIs unless the packet authorizes changes. Follow repository
conventions. Do not weaken tests, add speculative abstractions, introduce
unjustified dependencies, or repair unrelated failures. The independent verifier
checks task evidence; the fresh milestone reviewer owns broader validation.
