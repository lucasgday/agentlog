# Roadmap

Where agentlog is headed. These are **directions, not promises** — and they all
keep the core rule: everything stays local, nothing is uploaded.

Feedback and PRs very welcome — open an issue if you'd use one of these (or want
something that isn't here).

## Planned

- **Linux support.** Map each source's paths on Linux and replace the
  macOS-specific automatic-task mechanism. Biggest reach blocker today.
- **Per-conversation summaries.** A short summary at the top of each conversation
  so you can skim instead of re-reading every reply.
- **Per-project overview.** A project "home": stack, activity, a rolled-up
  summary, and what's still pending across its conversations.
- **Recommended `AGENTS.md` / `CLAUDE.md`.** Derived from your own conversations —
  your style, preferences and recurring asks — so you stop re-explaining them in
  every repo. Per-project and global.

## Exploring

- A local **"insights" view** (model usage, productivity rhythms, token spend)
  computed entirely on-device.
- **Search across your archive**, optionally AI-assisted, over one or all projects.

## How processing would work (privacy first)

Any of the above that needs an LLM would run through **your own** model — your
IDE's agent (Claude Code, Codex, …) or your own API key — and write its results
as local sidecar files the viewer reads. No data leaves your machine; you choose
the model.

## Done

- Multi-tool backup (Claude Code, Codex, Cursor, OpenCode, Cowork) → Markdown
- Standalone, bilingual (EN/ES) viewer with grouping, search, analytics
- Incremental + cumulative; daily automatic backup; portable Markdown history
