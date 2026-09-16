# Output

- Your deliverable is one JSON file, at the path and against the schema named in your prompt. The prompt names the schema and gives the absolute path of the schema file next to it; read that file when a field or an enum value is not obvious. Write the output in full, in one go.
- Write nothing else. No markdown view of the same content, no summary file, no notes file — every human-readable view is rendered from your JSON by `mf render`.
- Valid JSON only: no comments, no trailing commas, no code fence around the file body, no prose before or after the object.
- Use the field names and the enum values from the schema file exactly. A value you could not determine is `null` or the schema's "unknown" member, never an invented one.
- Where the schema has a reasoning field it comes first and stays short: what you found and why, not a transcript of how you looked.
- Shape, for orientation only — the schema in your prompt governs:

```json
{
  "reasoning": "Checked every subsection against the checklist; both failures sit in 4.2.",
  "draft_sha": "3b1f0c9a7d24e5b68f01c3a9d47e2b5081ac6f39d0b2e74c5a8916d3f0428ebc",
  "checklist": [
    {"id": "LOG-01", "pass": true, "evidence": "4.1 opens with the conclusion sentence."},
    {"id": "LOG-04", "pass": false, "evidence": "4.2 concludes on Article 22 without applying it above."}
  ],
  "issues": [
    {"severity": "blocker", "category": "logic", "section_id": "s-4-2", "issue": "Conclusion rests on authority never applied.", "suggestion": "Apply Article 22 in the analysis beat or drop the conclusion.", "checklist_id": "LOG-04"}
  ],
  "verdict": "needs_revision"
}
```

- Your final chat message is a short summary (≤100 words). The file is the deliverable; the message is not.
