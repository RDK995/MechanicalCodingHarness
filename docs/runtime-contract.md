# Mechanical runtime contract

The active execution entry points are `implement` and the compatibility command
`implement-mechanical`. Both fork `harness:mechanical-controller` on Sonnet for
one milestone. `plan-milestones` forks a separate bounded Sonnet planner before
execution. Legacy execution prompts are archived outside plugin discovery.

| Owner | Responsibility |
| --- | --- |
| Initial planner | Read agreed inputs and propose observable vertical slices; no product edits. |
| `harnessctl init-plan` | Validate coverage/IDs, lock, publish state and view, detect interrupted initialization. |
| `harnessctl execution-status` | Validate inputs/state and reject unfinished legacy work before branch creation or dispatch. |
| Mechanical controller | Follow next-action; dispatch scoped roles and return compact results. |
| Mechanical worker | Implement one task; run its exact oracle through the ledger. |
| Mechanical verifier | Inspect the scoped diff independently and freshly validate owned criteria. |
| Mechanical reviewer | Fresh semantic review of the frozen milestone diff and affected interfaces. |
| Mechanical advisor | Bounded Opus decision support for a named trigger; never overrides failed gates. |
| As-built agent | Record observed architecture under `.harness/as-built/` when required. |
| Runtime canary/hooks | Prove foreground dispatch and enforce permits, budgets, and scope. |

Workers/verifiers default to Haiku; routing may select Mid/Sonnet or Top/Opus
according to persisted task scope and retries. Review has a Sonnet floor and
follows relevant substantive routing. Advisor calls are limited to two per
milestone and the reasons enumerated in `agents/mechanical-controller.md`.
Hard role caps and foreground settings live in agent frontmatter. Soft stop
instructions provide headroom; they do not replace runtime enforcement.

## Gates and lifecycle

Execution requires agreed requirements, AGREED architecture when present, a Git
baseline, a clean substantive worktree, and
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`. A live denied Bash hook probe and a
foreground canary must complete before product work. Canary closure belongs to
`preflight-complete`, never generic `dispatch-complete`.

Every dispatch consumes one persisted permit; concurrent/background dispatch is
rejected. Scope and result identity are checked mechanically. Acceptance requires
separate worker/verifier evidence for the same workspace. Worker, verifier,
milestone and reviewer validation are fresh; the historical ledger purpose
`orchestrator` alone permits diagnostic reuse. It does not name an active agent.

Review freezes base/head and produces a strict result. Passing review and an
as-built record (or a permitted not-required result) precede phase closure.
`phase-close` owns the closing commit and selection of the next milestone.
The controller stops afterward. `advance-milestone` repairs a completed/deferred
current pointer without editing completed history. COMPLETE dispatches nothing.
No project-wide review is added.

## Recovery and compatibility

An initialization marker prevents execution of a partly published plan. Restore
a validated pair before removing it; never remove it merely to bypass the gate.
Two file replacements are not a crash-atomic transaction. Identical initial
proposals are idempotent; conflicts and partial pairs preserve existing files.

A valid existing pair is retained by the planning skill. Milestones without
state use `migrate-state.py` once, with explicit ownership if inference is
ambiguous. Missing views or inconsistent state stop execution. No automatic
force, replan, or task-evidence conversion is permitted.

Unfinished legacy tasks/reviews require the pinned legacy plugin until a clean
boundary. Completed history is readable. Historical navigator events remain
readable but no new navigator dispatch or budget is created.

If a milestone opened but runtime initialization was interrupted, the entry
gate returns INITIALIZE_RUNTIME and requires hook/canary preflight before task
planning. On other interruptions, inspect persisted dispatch state; consume an already-written
valid artifact where possible, then close the event exactly once. Missing
terminal fields never imply success. Retry and continuation budgets persist
across fresh contexts. Failed preflight and exhausted budgets fail closed.

## Release evidence

Python tests and plugin validation establish local contracts, not real model
accuracy or cost savings. The canonical release requires the existing comparator
promotion gates and candidate evidence described in
[mechanical-cutover-plan.md](mechanical-cutover-plan.md). The archived V1 contract
remains recoverable in Git; it is not an active execution specification.
