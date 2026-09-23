# Mechanical cutover validation — 2026-09-22

Branch: `feature/mechanical-cutover`. Runtime validation was performed against
the working-tree candidate before PR publication; canonical release remains pending.
Base and frozen legacy source: `bcccdcd781ecdce9cc4c802999ff2b890ecd9669`.
Candidate packaged runtime SHA-256: `c3b0f74fd18dba81ccb1301a1e537de3441426c917753922d1c245ac19ff260f`.

## Local evidence

Each suite below ran with `PYTHONDONTWRITEBYTECODE=1 python3 <suite>`.
All 14 suites passed: 124 tests. Full run elapsed time: 78.1 seconds.

| Suite | Tests | Result |
| --- | ---: | --- |
| `test-agent-guards.py` | 3 | PASS |
| `test-code-navigation.py` | 8 | PASS |
| `test-compare-harness-runs.py` | 6 | PASS |
| `test-cutover.py` | 13 | PASS |
| `test-guard-bash.py` | 9 | PASS |
| `test-harnessctl.py` | 23 | PASS |
| `test-initial-planning.py` | 11 | PASS |
| `test-measure-context.py` | 10 | PASS |
| `test-mechanical-campaign.py` | 6 | PASS |
| `test-mechanical-review.py` | 5 | PASS |
| `test-model-routing.py` | 1 | PASS |
| `test-no-project-review.py` | 4 | PASS |
| `test-runtime-enforcement.py` | 12 | PASS |
| `test-state.py` | 13 | PASS |

- `git diff --check`: passed.
- Python AST parsing for runtime and active development scripts: passed.
- `claude plugin validate .`: passed; optional author metadata and repository-only CLAUDE.md warnings.
- `claude plugin validate <exported-plugin>`: passed; only optional author metadata warning.
- Exported inventory: 8 agents, 6 skills; shared references are under skills, outside recursive agent discovery.
- Frozen legacy archive hash and per-file manifest: checked by test-cutover.
- A scripted passing milestone closes and a separate CLI invocation selects the next TODO milestone.
- Interruption after phase-open recovers through INITIALIZE_RUNTIME before task planning; legacy evidence without tasks is rejected.
- Initial planning covers locking, no-overwrite, malformed plans, ownership/component coverage, write rollback and crash-marker recovery.
- Canonical/compatibility skill wiring and all active plugin-root/dispatch references are checked.

## Decisions and limitations

- Added the missing Claude plugin manifest and included examples in evaluation packaging; the new planner and historical agents reference them.
- Historical source exports without metadata receive a documented namespace-only manifest shim when restored or frozen for evaluation; hashes include the shim for campaign snapshots.
- Moved shared references out of agents/references after native validation revealed that recursive agent discovery treated them as agents.
- Corrected measurement role normalization/limits and coordinator presence; added the already-required context, notification and duplicate-validation gates. Synthetic comparison tests now cover these mechanical limits.
- Retired prose-only test suites and their replacements are mapped in legacy/README.md; test-count reduction is not claimed as unchanged coverage.
- Initial examples now form one matching schema-v1 pair with no legacy task placeholders.
- MVP scope can be chosen before planning. Initial planning does not append scope to an existing plan; the MVP skill records later proposals and blocks activation before altering agreed inputs. Incremental planning remains a follow-up.
- The generic Codex skill validator could not run because PyYAML is unavailable and its accepted metadata differs from these Claude forked skills. Native Claude plugin validation passed for the actual target format.
- During runtime validation, no provider/model invocation, paid campaign run, merge, push, or canonical release was performed. Plugin validation and scripted lifecycle tests do not prove live model behaviour or savings.

## Release gate

Canonical promotion remains pending. Require candidate-specific evidence for all four paired fixture cohorts and five paired field milestones, and a passing `ready_for_canonical` comparator result. Existing frozen campaigns must remain immutable; prepare any new campaign with the pinned legacy ref and the existing per-invocation paid authorization protocol.

## Diagram documentation and PR preparation

`docs/mechanical-workflow.md` adds nine diagrams covering the workflow, ownership,
preflight, task sequence, evidence storage, retries/recovery, routing, and release
gates. All nine parse with Mermaid 11.17.2; README/document local links resolve.
The parser was installed only in a temporary directory, with no repository
package dependency. Python bytecode caches are excluded through `.gitignore`.
Publishing a draft PR for review does not satisfy the canonical release gate.

## PR review fixes — 2026-09-23

- `measure-context.py` accepts `--run-manifest`, validates its schema and run
  identity, and writes the `evaluation` metadata consumed by paired comparison.
  A regression test invokes the real measurement CLI through `measure_run()`
  for both arms, including two synthetic continuations, then compares the reports.
- Cost comparison rejects unpriced or invalid data in either arm before
  calculating savings. Standalone release checking also fails its pricing gate.
  Displayed partial totals remain diagnostic only.
- Response-level pricing handles model changes within a transcript; an unknown
  model or incomplete rate map makes the context unpriced. Exact model rates
  can be supplied alongside family rates.
- Regression coverage includes malformed manifests, measurements without a
  manifest, incomplete price maps, mixed models, both arms, stale partial
  totals, and invalid numeric costs.
- Focused verification: 28 tests pass across measurement, comparison and campaign
  suites. Full verification: all 14 suites pass, 130 tests total, using
  `PYTHONDONTWRITEBYTECODE=1 python3 <test-file>` for every active suite.
- No paid campaign or model invocation was launched. Plugin runtime files and the
  previously recorded packaged runtime hash are unchanged.
