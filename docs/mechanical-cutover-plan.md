# Mechanical workflow cutover plan

Status: implemented and locally validated on `feature/mechanical-cutover`; canonical promotion evidence remains outstanding.
Prepared: 2026-09-21.

## Outcome and scope

Make `/harness:implement` execute the mechanical workflow, with a separate
initial planning command and no dependency on the legacy execution agents.
Keep requirements roasting, architecture agreement, MVP scoping, independent
verification, milestone review, and conditional as-built recording.

This plan extends `.harness-dev/mechanical-orchestration-plan.md`. Its existing
evaluation/promotion gates still apply. Preparing the cutover does not establish
that those gates passed. This document defines the four requested changes;
implementation should record task progress and evidence in
`.harness-dev/progress.md` without marking historical work complete.

## Current dependencies

- `skills/implement-mechanical/SKILL.md` requires requirements, milestones, and
  state to exist. Its controller plans tasks within one existing milestone.
- Initial milestone generation currently belongs to `agents/orchestrator.md`
  and `agents/references/planning.md`.
- `scripts/harnesslib/state.py:next_action` can return `LEGACY_ORCHESTRATION`.
  Removing the legacy agent does not make those projects resumable mechanically.
- Mechanical execution directly uses `agents/as-built.md`,
  `agents/runtime-canary.md`, and shared navigation/review references.
- `scripts/harnesslib/runtime.py` still permits a navigator role, although the
  mechanical controller does not dispatch it.
- Many tests assert legacy prompt text. The evaluation campaign freezes its
  legacy plugin from `main`, which will cease to be a stable baseline after
  promotion unless that source is pinned.

## 1. Extract initial milestone planning

Deliver `/harness:plan-milestones` as a forked skill backed by a bounded Sonnet
`milestone-planner` agent. The agent performs targeted reconnaissance and
proposes milestone outcomes; Python validates and persists the plan. It does
not implement code or register detailed tasks for future milestones.

Files:

- Add `skills/plan-milestones/SKILL.md` and `agents/milestone-planner.md`.
- Extract the applicable generation guidance into
  `skills/plan-milestones/references/planning.md` and relocate the milestone
  template to that reference directory. Keep the original copies until cutover.
- Add `scripts/harnesslib/planning.py` and a `harnessctl init-plan --plan <file>`
  command; reuse state validation, requirement parsing, locking, and rendering
  conventions instead of creating a second state schema.
- Add `.harness-dev/test-initial-planning.py` and a minimal planning input
  example. Initial milestone tasks must be empty, unlike the illustrative
  legacy task in `examples/state.example.json`.

The proposal is a JSON object with a `milestones` array. Each entry contains
`id`, `title`, `outcome`, `architecture`, `requirements`, and `criteria` (stable
criterion `id` and `text`). Array order determines delivery order. The command
derives schema-v1 state and the Markdown view, with TODO milestones, PENDING
criteria, empty tasks/evidence/reviews, and the first milestone current.

Require agreed, unambiguous requirements and AGREED architecture when present.
Every in-scope requirement has exactly one owner. Reject duplicate IDs, unknown
requirement/component references, empty criteria, and missing coverage. Planning
guidance retains vertical slices, real-entry-point criteria, architecture seams,
and component coverage. Semantic feasibility remains the planner's judgement;
structural coverage belongs to the command.

Persistence must validate the proposed pair before publication, acquire the
same project lock as state mutation, and refuse to overwrite existing planning
artifacts. An identical valid existing pair may return ALREADY_PLANNED without
writes. A conflicting or partial pair returns an explicit recovery error.
Stage writes and restore on ordinary write failure; interrupted publication
must be detected on re-entry and never permit execution against a partial pair.
Do not claim two separate file replacements are a crash-atomic transaction.

Existing artifacts follow these rules:

| Project condition | Behaviour |
| --- | --- |
| Neither milestone file nor state exists | Generate and validate a complete proposal. |
| Both exist and validate | Return ALREADY_PLANNED without replanning. |
| Milestones exist, state is missing | Use the existing migration command, require explicit ownership when ambiguous, then validate; no automatic `--force`. |
| State exists, milestones are missing or inconsistent | Stop with the exact recovery requirement; preserve files. |

Acceptance: a requirements-only fixture produces a valid pair with full
ownership; invalid proposals publish neither file; reruns preserve existing
bytes; conflicting concurrent initialization cannot overwrite the winner;
partial-publication recovery fails closed; no product code or Git history is
changed. Test both architecture-present and architecture-absent inputs.

## 2. Make mechanical execution the default

Replace `skills/implement/SKILL.md` with the thin mechanical entry contract.
Keep `implement-mechanical` as a compatibility entry point with the same
controller and semantics; neither entry point dispatches the other skill.
Keep milestone preparation explicit: missing planning files direct the user to
`/harness:plan-milestones` and stop, avoiding an extra planner inside the
controller's execution context.

One invocation runs one current milestone, or returns CONTINUE, SPLIT, or
BLOCKED. DONE and SPLIT end that invocation. Continuation resumes persisted
state in a fresh context. A completed project returns COMPLETE after state and
ownership validation, without dispatching agents or running a project review.

Update `agents/mechanical-controller.md` to handle all reachable terminal and
recovery actions explicitly, including COMPLETE, ADVANCE_MILESTONE,
HUMAN_REQUIRED, and LEGACY_ORCHESTRATION. Where advancement is required, use
the existing mechanically owned transition or add a narrowly tested command;
never make the controller edit state directly.

Compatibility policy: resume valid mechanical state; accept clean TODO
milestones with empty tasks; reject unfinished legacy tasks/reviews before
branch creation or dispatch. Check legacy records even where next_action would
currently return OPEN_PHASE or DISPATCH_REVIEWER before inspecting their tasks.
Explain that unfinished legacy work must finish using the pinned legacy plugin
before switching at a clean milestone boundary. Preserve completed history and
all evidence. Do not fabricate ledger evidence or silently convert task status.
Automatic conversion of in-flight legacy work is outside this cutover.

Preserve current mechanical requirements: clean substantive worktree, usable
Git baseline, runtime preflight, one-use permits, independent validation,
bounded routing/retries, fresh review, and conditional as-built recording.
Document these where they differ from the legacy README's dirty-tree and
non-Git behaviour. No silent fallback to legacy execution.

Acceptance: test default and compatibility entry contracts; COMPLETE dispatches
nothing; preflight failure cannot reach work; interruption/continuation retains
budgets; split stops before child execution; legacy in-flight states are rejected
without mutation. Exercise a complete milestone and its next fresh invocation.

Prepare this change in an isolated branch. Before merging the default switch,
require the comparator's `ready_for_canonical: true` for the candidate or an
explicitly documented evidence equivalence for metadata-only changes. Record
candidate commit/hash and evidence paths. Existing gates require all four
fixture pairs and five field pairs, accuracy, independence, cost/token savings,
and runtime limits. This plan does not authorize new paid evaluation runs.

## 3. Retire legacy execution files

Ship retirement in the same cutover release as the new default, after step 1
works and step 2 passes its gate. Preserve a full immutable legacy plugin
snapshot outside active discovery paths, with its source commit, file manifest,
hash, and restoration instructions under `.harness-dev/legacy/README.md`.
Archive legacy-only prompts as `.md.txt` under `.harness-dev/legacy/` so they
are clearly historical, not active agents or skills.

| Artifact | Disposition |
| --- | --- |
| `agents/orchestrator.md`, `worker.md`, `verifier.md`, `reviewer.md`, `navigator.md` | Archive and remove from active agents. |
| Original `skills/implement/SKILL.md` | Archive before replacing in step 2. |
| `agents/references/fix-cycle.md` | Archive after confirming no active consumer. |
| `agents/references/planning.md` | Archive original after generation guidance is extracted. |
| `skills/implement/references/milestones-template.md` | Move active template to the planning skill; update all consumers. |
| `skills/implement/references/engineering-practices.md` | Audit obligations; retain needed guidance in a short mechanical-worker reference, then archive original. |
| All `agents/mechanical-*.md`, `runtime-canary.md`, `as-built.md` | Keep active. |
| Code-navigation and mechanical-review references | Keep active. |
| Requirements, architecture and MVP skills and their references | Keep active; update legacy terminology/links. |
| State validators, migration, navigation, hooks, ledger and runtime modules | Keep; they are shared/current infrastructure. |

Remove the unused navigator dispatch role and budget for new runtimes. Preserve
readability of historical events/state; reject new navigator dispatches. Do not
rename historical ledger purposes such as `orchestrator` merely to remove a
word: they describe persisted evidence and are not an agent dependency.

Acceptance: every active path reference resolves; active contracts cannot
dispatch an archived agent; retained safety/engineering obligations are mapped
to mechanical enforcement or explicit guidance; historical state still validates;
the frozen legacy plugin remains usable for finishing legacy projects and
paired comparison. Verify the packaged discovery inventory in a local runtime.

## 4. Align documentation, tests and evaluation tooling

Update README with the sequence roast requirements → optional architecture →
optional MVP → plan milestones → implement. Explain one-milestone contexts,
compatibility command, initial migration, legacy-project cutover, and actual
Git/runtime prerequisites. Update `docs/runtime-contract.md`, `CLAUDE.md`,
current upstream skill references, and examples to the active architecture.
Add a supersession pointer to `docs/implementation-plan.md`; retain historical
build records rather than rewriting their claimed results.

Audit every `.harness-dev/test-*.py`:

- Keep behavioural tests for state, task lifecycle, review, runtime enforcement,
  navigation, ownership, correction, and measurement.
- Rewrite mixed legacy prompt tests to assert the equivalent active contract
  and add behavioural checks where an executable oracle exists.
- Archive tests that solely inventory retired prose with the legacy snapshot;
  they must not silently become the evidence for mechanical behaviour.
- Add an explicit active-file/dispatch inventory and reference integrity check.

Affected suites include orchestrator inventory, model routing, milestone
complexity, dispatch/collect, interrupted review, commit discipline, no-project
review, agent guards, and validation ownership. Examine each test's intent
before moving or replacing it.

Change `mechanical-campaign.py prepare` to take a required explicit
`--legacy-ref <commit>` for new campaigns; resolve it to an immutable SHA and
record it alongside the archive hash. Keep existing frozen campaigns immutable
and readable. Update campaign tests and the runbook. Future legacy comparisons
must not silently archive the newly mechanical `main` as their control arm.
Keep arm labels and comparator gates stable.

Acceptance: documented commands and active references agree with implementation;
new campaigns record and use the supplied legacy SHA; existing frozen campaigns
still load; all retained top-level test suites pass; removed tests have an
explicit disposition rather than unexplained lost coverage.

## Verification and release sequence

1. Implement step 1 and its focused tests while both entry points remain.
2. Prepare steps 2–4 together on the cutover branch, with focused validation
   after each coherent change. Pin the legacy baseline before changing defaults.
3. Run the full local suite once the candidate is complete:

   ```bash
   for test_file in .harness-dev/test-*.py; do
     PYTHONDONTWRITEBYTECODE=1 python3 "$test_file" || exit 1
   done
   git diff --check
   ```

4. Verify plugin inventory and command wiring; record these separately from
   Python contract tests. Run the existing guarded evaluation procedure only
   within its separately authorized paid-run process.
5. Record passing promotion evidence and ship the default switch, retirement,
   and associated docs/tests together. If promotion is not yet proven, leave
   that release pending with the legacy default intact.

Rollback restores the pinned full plugin snapshot or reverts the cutover
release. It never rewrites project state or discards evidence. Projects with
mechanical work in flight continue with a matching mechanical plugin until a
safe boundary; the legacy workflow must not consume those active records.

## Completion checklist

- [x] Planning works from requirements without any legacy execution agent.
- [x] Default and compatibility implementation commands use mechanical execution.
- [x] Existing project compatibility and recovery paths are covered by tests.
- [x] Legacy execution files are absent from active discovery.
- [x] Shared agents, references and semantic quality checks remain intact.
- [x] Documentation, examples, test inventory and frozen baselines are aligned.
- [x] Full local suite and runtime wiring checks have recorded results.
- [ ] Canonical promotion evidence passes the existing comparator gates.

Implementation evidence: `.harness-dev/mechanical-cutover-validation.md` records
124 passing tests, native package validation, the candidate runtime hash, and
remaining release requirements. The candidate is prepared for draft PR review; canonical release remains unmerged.

Implementation adjustments: shared references moved to
`skills/implement/references/` to avoid recursive agent discovery; plugin metadata
and example packaging were restored; measurement now recognizes mechanical role
names and limits. MVP expansion is explicitly blocked before active-input writes
because incremental planning was not included in this initial-plan cutover.

Next release task: obtain the existing candidate-specific promotion evidence;
no paid campaign invocation is authorized by this implementation record.
