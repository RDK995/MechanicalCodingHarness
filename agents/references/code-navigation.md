# Bounded code navigation

Use this only when a named code symbol must be located. It returns locations,
not explanations or an authoritative context packet.

```bash
NAV="${CLAUDE_PLUGIN_ROOT}/scripts/code-nav.py"
python3 "$NAV" capabilities
python3 "$NAV" definition --file <path> --line <one-based> --column <one-based>
python3 "$NAV" references --file <path> --line <one-based> --column <one-based>
```

Pass `--symbol <identifier>` when the position is ambiguous. Use repeated
`--path <scoped-path>` to keep a textual fallback inside the task's known
boundary. `--max-results` is globally bounded to 100 and defaults to 40.

Interpret output conservatively:

- `source: LSP` means a language server reported the locations. Read only the
  relevant spans at those locations and verify conclusions against the code.
- `source: TEXT_FALLBACK` means exact word occurrences only. It is incomplete
  semantic evidence: it cannot distinguish definitions, aliases, dynamic calls,
  generated code, or reflection.
- `truncated: true` requires a narrower `--path`; do not broaden reads.
- `authoritative` is always false. Never claim the output proves blast radius or
  completeness.

If the query cannot locate the symbol, use one narrow `rg` query or inspect the
known file. Do not recursively dump files, request a synthesized repository
packet, or use Graft. Escalate only when the missing relationship changes a
material decision.
