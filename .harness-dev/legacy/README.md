# Frozen legacy workflow

This directory is historical material, outside active plugin discovery.
`manifest.json` identifies the source commit, SHA-256 of `plugin.tar`, and
SHA-256 of every archived file. The tar preserves the complete tracked runtime
(agents, skills, hooks, scripts, examples) before the cutover. Prompt copies use
`.md.txt`; archived tests use `.py.txt` and are not part of the active suite.

The source export did not include `.claude-plugin/plugin.json`. A minimal
namespace manifest is provided separately as `plugin-metadata.json`; it does
not change any archived prompt or code. To finish a project with legacy work
in progress, verify the archive against the manifest, extract into a separate
empty directory, create `.claude-plugin` there, and copy `plugin-metadata.json`
to `.claude-plugin/plugin.json`. Load that directory with `claude --plugin-dir`.
Do not extract over the active checkout. Switch only at a clean milestone
boundary; never let legacy code consume mechanical tasks in progress.

Paired evaluation uses `mechanical-campaign.py prepare --legacy-ref <commit>`.
The same metadata shim is applied only when absent and included in the frozen
plugin tree hash. Existing campaigns and their measured evidence are immutable.

## Test disposition

Archived suites asserted legacy prose, not executable lifecycle behaviour:

| Archived suite | Active replacement/evidence |
| --- | --- |
| orchestrator-inventory | test-cutover: exact active inventory, references, entry points |
| dispatch-collect | test-runtime-enforcement: single foreground permits, no overlaps; test-guard-bash: no polling |
| interrupted-review | runtime dispatch closure/budgets and mechanical result validation; compact INTERRUPTED contracts |
| commit-discipline | test-harnessctl: independently verified task commit/scope; test-mechanical-review: close ordering |
| milestone-complexity | test-harnessctl: split partition and scope; test-initial-planning: coverage |
| compact-returns | strict result validation in test-mechanical-review and bounded ledger output in test-harnessctl |
| validation-ownership | test-harnessctl: fresh worker/verifier/reviewer ledger executions and immutable evidence |

Mixed suites remain active: model-routing keeps its executable Top-detail
validation; agent-guards now inventories bounded mechanical roles; no-project-
review checks the active contracts. Legacy record-only shortcuts, parallel
orchestrator dispatch, and partial prose-report formats are deliberately retired,
not claimed as mechanical features. The mechanical path keeps independent
semantic review and refuses unsupported record-only corrections. Historical
fixture expectations are preserved; the mechanical campaign uses its four
separate named cohorts.
