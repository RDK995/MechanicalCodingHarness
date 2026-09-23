# Mechanical orchestration evaluation runbook

This campaign compares the pinned legacy workflow with the opt-in mechanical
workflow on four frozen fixtures. Preparation, preflight, grading, measurement,
and comparison are local and spend no provider tokens. `run-one` is the only paid
operation. It launches exactly one invocation only when its opaque session ID is
repeated through `--acknowledge-paid-run`.

The fixture campaign can establish the first promotion stage. Canonical
replacement additionally requires five paired field milestones; the comparator
refuses canonical promotion without them.

## 1. Zero-cost verification

```bash
for test_file in .harness-dev/test-*.py; do
  PYTHONDONTWRITEBYTECODE=1 python3 "$test_file" || exit 1
done
```

The four frozen cohorts are:

- `known-path`: one local arithmetic defect;
- `medium-cross-file`: a value threaded through API, pricing, and tax modules;
- `oversized-split`: API/scheduler/worker/live-proof lifecycle work that must
  split without implementation;
- `review-defect`: a green public test hides non-atomic rejection, which the
  milestone reviewer must catch before completion.

## 2. Freeze paired worktrees and plugin snapshots

Use an output directory outside this repository. The legacy plugin is archived
from an explicit commit supplied through `--legacy-ref`; the mechanical plugin is copied from the current working tree. Both
are hashed and then treated as immutable.

```bash
python3 .harness-dev/mechanical-campaign.py prepare \
  --output /absolute/private/mechanical-campaign \
  --legacy-ref bcccdcd781ecdce9cc4c802999ff2b890ecd9669 \
  --claude "$(command -v claude)" \
  --max-budget-usd 10 \
  --timeout-seconds 3600

python3 .harness-dev/mechanical-campaign.py preflight \
  /absolute/private/mechanical-campaign
```

Preparation freezes four pairs, alternates arm order, creates clean worktrees,
and pre-allocates fresh session IDs. The theoretical maximum printed by
preflight assumes every run consumes all five continuation slots; it is not a
spend forecast.

## 3. Launch one explicitly authorised invocation

Inspect the next operation without launching it:

```bash
python3 .harness-dev/mechanical-campaign.py next \
  /absolute/private/mechanical-campaign
```

It prints an authorization string of this form:

```text
Authorize paid mechanical evaluation <opaque-session-id>
```

Only after a human repeats that exact ID may the invocation run:

```bash
python3 .harness-dev/mechanical-campaign.py run-one \
  /absolute/private/mechanical-campaign \
  --acknowledge-paid-run <opaque-session-id>
```

The launch is recorded before Claude starts, uses a frozen per-invocation dollar
cap and wall-time cap, and is never retried under the same session ID. A context
that returns `CONTINUE` receives a different pre-frozen ID and therefore needs a
new authorization. Failed and capped runs remain evidence.

## 4. Stage, grade, and measure a completed run

```bash
python3 .harness-dev/mechanical-campaign.py stage \
  /absolute/private/mechanical-campaign \
  --run-id <run-id> \
  --claude-projects-root "$HOME/.claude/projects"

python3 .harness-dev/mechanical-campaign.py grade \
  /absolute/private/mechanical-campaign --run-id <run-id>

python3 .harness-dev/mechanical-campaign.py measure \
  /absolute/private/mechanical-campaign --run-id <run-id>
```

The grader runs private behavioural probes, validates state and requirement
ownership, and checks independent task verification and milestone review. The
measurement is derived from persisted transcripts, never from a model's cost or
token claim.

Campaign measurement passes its frozen run manifest through `--run-manifest`.
The resulting report includes schema-v1 `evaluation` metadata: run ID, pair ID,
arm, cohort, and fixture. Standalone measurements may omit this option, but
paired comparison requires it.

All contexts in both arms must have valid pricing before savings are compared.
A report may display a partial total for diagnosis, but unpriced contexts,
missing price components, and non-finite or negative costs cannot certify a
release. Supply a complete `--prices <json>` map to `measure-context.py` and
remeasure the staged sessions if necessary. Rates are keyed by exact model name
or the existing Haiku/Sonnet/Opus families and contain `input`, `cache_creation`,
`cache_read`, and `output` USD per million tokens. Model changes within a session
are priced per response; any unpriced response makes the context unpriced.

After all eight fixture runs have evidence:

```bash
python3 .harness-dev/mechanical-campaign.py evidence \
  /absolute/private/mechanical-campaign
```

Run the emitted `compare_argv`. Fixture comparison can mark
`ready_for_opt_in`; `ready_for_canonical` remains false until five field pairs
are included.

## 5. Five field milestones

For each field milestone, freeze the same starting commit into one `legacy` and
one `mechanical` worktree, alternate arm order, and use new session IDs. Attach
this schema-v1 evaluation metadata to each measured report:

```json
{
  "schema_version": 1,
  "run_id": "<unique>",
  "pair_id": "<same for both arms>",
  "arm": "legacy or mechanical",
  "cohort": "field",
  "fixture": "<project milestone id>"
}
```

Grade the mechanical arm with the same seven accuracy fields used by the frozen
fixtures. Then compare all fixture and field reports together with
`compare-harness-runs.py`.

## Promotion rule

Do not replace the canonical workflow unless every gate passes. In particular:

- no false pass or hidden-test failure;
- independent verification and milestone review remain intact;
- median estimated cost saving is at least 35%;
- median token saving is at least 20%;
- controller/parent traffic is at most 15%;
- coordination median is at most 22 turns and no coordination context exceeds
  160k tokens;
- zero polling, notification re-entry, hard-cap violation, duplicated validation,
  state corruption, or unowned requirement;
- all four fixture cohorts and five paired field milestones are present.

A failed gate is a no-promotion result, not a reason to discard the run.

New campaigns record `legacy_commit` as the resolved immutable SHA. Existing
frozen campaigns remain readable without that additive field. Never reuse the
new default as the legacy control arm. Historical exports without plugin metadata
receive a minimal harness namespace manifest; the frozen tree hash includes it.
