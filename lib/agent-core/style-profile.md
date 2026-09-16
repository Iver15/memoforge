# Style profile

- When `${prose_style_path}` is set in your prompt, read that file. It is the user's own house style and it is authoritative wherever it differs from `lib/prose-style.md`; where it is silent, `lib/prose-style.md` still applies.
- When it is empty or absent, `lib/prose-style.md` is the style of record.
- A custom profile may rename or drop conventions — a different risk vocabulary, different section names, a different quoting habit. Follow its vocabulary rather than the default one, and when you flag something against it, name the rule: `per <profile>/prose-style.md §<section>`.
- Agents that read the profile: `memo-writer`, `form-reviewer`, `client-readiness-reviewer`, `revision-mediator`. Other agents ignore it even when the path is present.
- Neither file overrides `mf draft lint` and `mf draft audit-citations`; those checks are the same under every profile.
