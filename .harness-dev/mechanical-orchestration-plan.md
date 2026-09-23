# Mechanical Orchestration Implementation Plan

## Decision

Build from the stable `main` harness. The unfinished `harness-v3-wip` branch is
reference material only: useful ideas may be ported independently, but its
schema, controller, stash, and migration path are not prerequisites.

The production `/harness:implement` workflow remains unchanged until an opt-in
implementation has passed the accuracy and efficiency campaign.

## Objective

Move deterministic coordination out of the Opus orchestrator and into small,
tested commands while retaining model judgement where no reliable oracle
exists. The intended steady state is:

- scripts own state transitions, task lifecycle, validation reuse, budgets,
  evidence checks, and completion gates;
- Sonnet owns routine phase coordination;
- Opus is consulted only for bounded architecture, security, concurrency,
  ambiguous decomposition, contradictory evidence, or repeated oracle failure;
- workers, verifiers, and milestone reviewers remain fresh and independent;
- no project-level review is introduced.

## Delivery sequence

### 1. Mechanical state and phase foundation

Add an opt-in `harnessctl` command against the current schema.

- Lock state before mutation and replace files atomically.
- Run the existing state/requirements/view validator before and after changes.
- Open one milestone mechanically and register a structured task plan.
- Derive a compact next action from persisted state.
- Keep `state.json` authoritative and update the Markdown status view in the
  same guarded operation.
- Make commands idempotent where replay is safe and fail closed otherwise.

This phase does not alter any agent or skill prompt.

### 2. Atomic task lifecycle

Add structured worker and verifier result contracts and these transitions:

- `record-worker-result`
- `record-verifier-result`
- `accept-task`
- `reject-task`

The command validates task identity, scope, commit, changed paths, evidence
artifacts, and retry allowance. Models still judge implementation and evidence;
scripts reject stale or malformed claims.

Implementation status: complete on `feature/mechanical-orchestration`. The
opt-in command now snapshots scoped work, records immutable worker/verifier
results, applies the retry ladder, rejects stale or out-of-scope evidence, and
commits only independently verified paths.

### 3. Validation ledger

Record validation by immutable key:

`HEAD + command + environment fingerprint + purpose`

Reuse duplicate orchestrator checks, but require a fresh independent reviewer
execution. Store full output in evidence files and return only bounded summaries.

Implementation status: complete on `feature/mechanical-orchestration`. Validation
is keyed by commit, argv, environment/workspace fingerprint, and purpose. Only
orchestrator diagnostics may be reused; worker, verifier, milestone, and reviewer
executions are always fresh and agent results must reference an intact ledger
record.

### 4. Runtime enforcement

Turn prompt rules with mechanical oracles into hooks and preflight checks:

- deny background dispatch, foreground sleep/polling, unsafe Git operations,
  and out-of-scope writes;
- persist dispatch, retry, review-cycle, and consultation budgets across
  context re-entry;
- fail closed when runtime capabilities are missing;
- add a live foreground-dispatch canary rather than trusting configuration.

Implementation status: complete on `feature/mechanical-orchestration`. Runtime
preflight now requires both a live Bash-hook denial and a one-turn foreground
agent return. Dispatches consume one-use persisted permits, cannot overlap or run
in the background, and worker Write/Edit calls are checked against task scope.
Retry, review, advisor, role-dispatch, and continuation limits live in state and
fail closed across context re-entry.

### 5. Thin controller and bounded judgement

Introduce an opt-in controller only after the commands above own the lifecycle.

- Run routine coordination on Sonnet.
- Keep its prompt to phase routing, dispatch, and compact contract handling.
- Invoke Opus only for named reason codes with at most two consultations per
  phase.
- Give subagents minimum tools and on-demand references; omit inherited project
  instructions where the role does not need them.
- Preserve independent worker, verifier, and reviewer contexts.

Opus consultation triggers:

- `JUDGMENT_NO_ORACLE`
- architecture, security, or difficult concurrency decisions
- competing task seams with materially different risk
- contradictory evidence or worker/verifier disagreement
- two failed attempts against a reliable oracle

Implementation status: complete on `feature/mechanical-orchestration`. The
opt-in `implement-mechanical` skill now delegates one milestone to a bounded
Sonnet controller. Scoped Haiku workers and verifiers remain independent, the
fresh milestone review has a Sonnet floor and follows substantive routing to
Opus, and Opus advice is limited to the named triggers with two persisted
consultations. Strict result contracts, foreground permits, review/correction
gates, completion ordering, and fail-closed human escalation are exercised by
the zero-cost contract suite. The stable `implement` path is unchanged.

### 6. Navigation

Prefer code-intelligence/LSP definitions and references over broad file reads.
Do not integrate Graft: its measured treatments increased cost and omitted
relevant files. A future experiment may test a narrow structured operation such
as callers or blast radius, but it must not inject an authoritative context
packet.

Implementation status: complete on `feature/mechanical-orchestration`. A
read-only `code-nav.py` adapter now requests definitions and references from an
available language server over bounded stdio JSON-RPC. When no server is
available or it fails, the adapter returns bounded exact-word locations marked
`TEXT_FALLBACK`, `TEXT_OCCURRENCES_ONLY`, and non-authoritative. The mechanical
controller, worker, verifier, and reviewer use those locations before broad
search and still inspect the cited code themselves. Path escape, response size,
timeouts, and generated context packets fail closed; Graft remains excluded.

### 7. Evaluation and promotion

Run the frozen fixture campaign plus representative milestones:

1. zero-cost contract tests;
2. known-path fixture;
3. medium cross-file fixture;
4. oversized-milestone split;
5. planted review defect;
6. five field milestones.

Release gates:

- no false pass or hidden-test regression;
- independent task verification and milestone review remain intact;
- median estimated cost falls by at least 35%;
- median token traffic falls by at least 20%;
- controller/parent share remains below 15%;
- orchestrator median is at most 22 turns;
- no orchestrator context exceeds 160k tokens;
- zero foreground polling, notification re-entry, or hard-limit violations;
- no duplicate broad validation, state corruption, or unowned requirement.

Promote in two steps: make the new path explicitly opt-in, then replace the
canonical workflow only after five successful field milestones. Each delivery
phase must remain independently revertible.

Implementation status: evaluation infrastructure complete; paid evidence is
in progress. The zero-cost suite has 152 passing tests. Four paired fixture cohorts
are frozen under guarded campaign `3dcc06a9-8b83-41f0-b3bc-89b920ab8950` with
immutable legacy/mechanical plugin hashes, clean worktrees, hidden graders,
transcript-derived measurement, and paired promotion gates. The campaign
requires an exact per-invocation authorization. The known-path legacy arm is
complete after two invocations: estimated $6.28, 2,190,042 token traffic, and
48.2% parent traffic. It passed the fixture, hidden, review, state, ownership,
and no-false-pass checks but did not retain acceptable independent-verification
evidence. Its paired mechanical treatment has completed one invocation at an
estimated $0.49 and 934,687 token traffic across two controller allocations.
That frozen treatment exposed a preflight regression: generic dispatch closure
could clear the active canary before `preflight-complete`, while `next-action`
allowed planning despite incomplete preflight. No further invocation will run
against that snapshot. The state machine, runtime guard, recovery path, and
controller command contract are corrected with 156 passing zero-cost tests.
Replacement campaign `3aa11cb9-6d14-489a-a87d-80f3921994a2` is frozen and
passes preflight; its legacy hash is unchanged and its mechanical hash is
`acd42d4d71280fe845360c0840d3b4225271c44ff567083aad2176634af361f7`.
Five paired field milestones remain mandatory before canonical promotion.
