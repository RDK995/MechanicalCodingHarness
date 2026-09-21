# Mechanical review result contract

Write one JSON object with this exact shape. Copy base, head, cycle, tier, model,
reason code, and result/report paths from `review-packet`.

```json
{
  "schema_version": 1,
  "role": "reviewer",
  "milestone": "M1",
  "cycle": 1,
  "verdict": "PASS",
  "base": "<sha>",
  "head": "<sha>",
  "tier": "Mid",
  "model": "sonnet",
  "reason_code": "REVIEW_FLOOR",
  "scope": "SUBSTANTIVE",
  "criteria": [
    {"id": "M1-AC1", "status": "PASS", "evidence": ["path:line or command evidence"]}
  ],
  "findings": [],
  "validation": {
    "command": "<from validation-run>",
    "exit_code": 0,
    "artifact": ".harness/evidence/validation/<file>.log",
    "sha256": "<from validation-run>",
    "ledger_key": "<validation-run key>",
    "execution": 1
  },
  "report": null
}
```

Criteria must appear exactly once and in state order. Evidence arrays are
non-empty. A PASS has every criterion PASS, no BLOCKER/IMPORTANT finding, and a
green validation result.

Every finding has this exact semantic content:

```json
{
  "id": "M1-R1-F1",
  "severity": "BLOCKER",
  "summary": "one falsifiable sentence",
  "evidence": "path:line and observed consequence",
  "suggested_correction": "bounded outcome, not a redesign",
  "paths": ["relative/path"]
}
```

Severities are `BLOCKER`, `IMPORTANT`, or `OPTIONAL`. `CHANGES_REQUIRED` needs at
least one BLOCKER/IMPORTANT finding, a Markdown report under `.harness/reviews/`,
and `report` equal to `{"artifact": "<path>", "sha256": "<hash>"}`. Use
`scope: RECORD_ONLY` only when all required changes are confined to `.harness`;
otherwise use `SUBSTANTIVE`. `BLOCKED` is for an unreadable diff or absent
reliable validation, not for an ordinary defect.

The validation object deliberately renames `key` from `validation-run` to
`ledger_key`; copy all other named fields exactly.
