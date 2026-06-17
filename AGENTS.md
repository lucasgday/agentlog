# AGENTS.md

Context for AI agents (and humans) working on **agentlog** — a local, self-hosted
backup + viewer for your AI-coding-tool conversations (Claude Code, Codex, Cursor,
OpenCode, Cowork). macOS and Linux (paths resolved per-OS; Cowork is macOS-only).

## Non-negotiable rules

1. **Privacy is the product. The viewer makes ZERO network calls.** Never add
   `fetch`, `XMLHttpRequest`, `sendBeacon`, WebSockets, analytics/telemetry, or
   external resources (no CDN scripts/fonts) to `viewer.html`. The scripts only
   read local files and write local Markdown. Nothing — raw or derived — may leave
   the user's machine. This is the whole pitch; don't break it.
2. **English only in code** — comments and identifiers. Two deliberate exceptions,
   do NOT "fix" them:
   - the `es:` block of the `I18N` dictionary in `viewer.html` (the Spanish UI
     translation), and
   - **legacy Spanish data keys** the parser accepts for backward-compat with
     older backups: `fecha/proyecto/fuente/archivada` and the turn/tool markers
     `Tú/herramienta/resultado`, plus `generado/conversaciones`. Keep these.
3. **Cumulative, never destructive.** The backup never deletes already-generated
   markdowns, even if the source tool removed the original.

## Layout

- `update-backup.sh` — orchestrator / CLI entrypoint (`--help`, `--only`,
  `--dry-run`, optional `[OUTPUT_DIR]`). Reads each source, calls the converters.
- `converters/convert_*.py` — one per source (claude/codex/opencode/cursor; cowork
  reuses convert_claude). Each reads its origin and writes the standard Markdown.
- `converters/extract_ledger.py` — **Evidence Ledger**: deterministic, $0, on-device
  metrics (token usage, tool/test/build counts, files modified, errors) parsed from
  the raw `.jsonl`. Writes a `_ledger.json` sidecar the viewer reads. No LLM, no
  network. Incremental: per-session metrics are cached in `_ledger-cache.json`
  (validated by file size:mtime), so each run only re-scans changed sessions.
- `viewer.html` — standalone, bilingual (EN/ES) viewer. Pure reader. No build step.
- `*.command` — double-click launchers (install/uninstall the launchd task; run).
- `docs/` — `index.html` (the GitHub Pages live demo, sample data baked in),
  screenshots, `hero.gif`.
- `ROADMAP.md` — public roadmap. `ROADMAP.private.md` — maintainer-only strategy &
  full vision (gitignored via `*.private.md`; not in clones).

## Standard Markdown format (converters ↔ viewer contract)

Converters emit, and the viewer parses, exactly this:

```
# <title>

<!-- date: <ISO> | id: <id> | project: <project> | source: <source> | archived: <true|false> -->

### You

…

### <Assistant>      (Claude / Codex / Cursor / OpenCode)

…
```

Tool calls render as `[tool: <name> → …]` / `[result]` blocks. If you change the
format, update **both** the converters and the viewer's parser, and keep reading
the legacy Spanish keys (rule 2).

## Adding a source

Write a `converters/convert_<tool>.py` that reads that tool's storage and emits the
format above, then wire a block into `update-backup.sh`. The viewer needs no
changes. See any existing converter as a reference.

## Verifying changes

- Shell: `bash -n update-backup.sh *.command`
- Converters: `python3 -m py_compile converters/convert_*.py converters/extract_ledger.py`
- Viewer JS parses: `node -e "const h=require('fs').readFileSync('viewer.html','utf8');new Function(h.match(/<script>([\s\S]*)<\/script>/)[1]);console.log('ok')"`
- Visual: serve a folder of sample `.md` and open the viewer headless, or open
  `viewer.html` and point it at a markdown folder. The viewer must render with no
  console errors and make no network requests.

## i18n

UI strings live in the `I18N` dictionary (`en`/`es`) in `viewer.html`; use `t(key)`
(or `T(key)` inside turn loops where `t` is shadowed). Add new strings to **both**
languages. Language auto-detects from the browser and is user-toggleable.

## Roadmap

See [ROADMAP.md](ROADMAP.md). Post-launch direction (Linux vs the summary/AGENTS.md
pipeline) is feedback-driven; details and strategy are in the maintainer's private
notes.
