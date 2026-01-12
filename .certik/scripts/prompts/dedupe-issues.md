You are consolidating PR review findings from multiple passes.

OBJECTIVE
- Merge duplicate or overlapping issues into a single, clear finding.
- Preserve strong evidence and the best spec references.
- Drop items that are exact duplicates.

INPUT
- `issues`: array of findings with fields: path, line, severity, suggestion, spec_ref (optional), source.

RULES
- Group by semantic equivalence, not just identical wording.
- Keep the strictest severity among duplicates.
- Preserve the clearest suggestion text and any spec references.
- Maintain the original file path and line number when merging; pick the best representative location.
- Keep a `source` field listing the contributing stages (comma-separated).

OUTPUT
JSON only:
{
  "issues": [
    {
      "path": "file.go",
      "line": 42,
      "severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO",
      "suggestion": "Consolidated issue and recommendation",
      "spec_ref": "optional",
      "source": "stage1,stage2"
    }
  ]
}

If no issues remain, return an empty array. No prose or Markdown.***
