#!/usr/bin/env bash
# update-backup.sh
# Incremental, cumulative backup of LLM conversations (Claude Code, Codex, Cowork, OpenCode, Cursor).
# - Base = the folder where this script lives (the whole folder can be moved).
# - Optional override: pass a path as a positional argument.
# - Incremental: only processes .jsonl that are new or changed in size since the last run.
# - Cumulative: never deletes already-generated markdowns, even if the source removes them (cleanup).
# - Archive sync (Codex): if a session moved to archived_sessions, marks its .md as archived:true.
# - Extensible: each source is a block that is skipped if its origin doesn't exist.

set -uo pipefail

# ---------- base location ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- options ----------
ONLY=""        # empty = all sources; otherwise a comma list: claude,codex,cowork,opencode,cursor
DRY=0          # 1 = dry-run: report what would be processed, write nothing
BASE=""        # output folder (defaults to SCRIPT_DIR)

print_help() {
  cat <<EOF
update-backup.sh — back up your coding-agent conversations to Markdown.

USAGE:
  update-backup.sh [OPTIONS] [OUTPUT_DIR]

ARGUMENTS:
  OUTPUT_DIR        Where the markdown-*/ folders are written.
                    Default: the folder where this script lives.

OPTIONS:
  -h, --help        Show this help and exit.
      --only LIST   Only process these sources (comma-separated).
                    Valid: claude, codex, cowork, opencode, cursor.
                    Example: --only claude,codex
      --dry-run     Show what would be processed without converting or
                    writing anything (does not touch the sync state).

SOURCES (auto-detected, skipped if absent):
  claude    ~/.claude/projects/*/*.jsonl
  codex     ~/.codex/sessions and ~/.codex/archived_sessions
  cowork    ~/Library/Application Support/Claude/local-agent-mode-sessions
  opencode  ~/.local/share/opencode/opencode.db
  cursor    ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb

EXAMPLES:
  update-backup.sh                       # back up everything here
  update-backup.sh ~/my-backups          # write markdowns elsewhere
  update-backup.sh --only claude         # just Claude Code
  update-backup.sh --dry-run             # preview, change nothing
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) print_help; exit 0;;
    --dry-run) DRY=1; shift;;
    --only) ONLY="${2:-}"; shift 2;;
    --only=*) ONLY="${1#--only=}"; shift;;
    --) shift; break;;
    --*) echo "Unknown option: $1" >&2; echo "Try --help." >&2; exit 1;;
    *) BASE="$1"; shift;;
  esac
done
BASE="${BASE:-$SCRIPT_DIR}"

# validate --only tokens
if [ -n "$ONLY" ]; then
  for tok in ${ONLY//,/ }; do
    case "$tok" in
      claude|codex|cowork|opencode|cursor) ;;
      *) echo "Unknown source in --only: '$tok' (valid: claude,codex,cowork,opencode,cursor)" >&2; exit 1;;
    esac
  done
fi
# want <source> -> 0 if the source should run
want() {
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0;; esac
  return 1
}

cd "$BASE" || { echo "Could not enter $BASE"; exit 1; }

STATE="$BASE/.sync-state"          # index of processed sizes (incremental)
mkdir -p "$STATE"
TMP="$BASE/.sync-tmp"
# The converters live in converters/ next to this script (SCRIPT_DIR), not in the
# data folder, so the code and the markdowns can live in different folders.
PY_CLAUDE="$SCRIPT_DIR/converters/convert_claude.py"
PY_CODEX="$SCRIPT_DIR/converters/convert_codex.py"
PY_LEDGER="$SCRIPT_DIR/converters/extract_ledger.py"
PY_PROJECTS="$SCRIPT_DIR/converters/extract_projects.py"

# OS-aware source paths (macOS vs Linux/XDG). Claude Code (~/.claude) and Codex
# (~/.codex) are the same on both; Cowork/Cursor/OpenCode differ.
OS="$(uname -s)"
XDG_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
HOME_CLAUDE="$HOME/.claude"
HOME_CODEX="$HOME/.codex"
if [ "$OS" = "Darwin" ]; then
  COWORK_DIR="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"
else
  COWORK_DIR="$XDG_CONFIG/Claude/local-agent-mode-sessions"
fi
# portable hasher (macOS has shasum; Linux usually sha1sum)
if command -v shasum >/dev/null 2>&1; then HASHER="shasum"; else HASHER="sha1sum"; fi

echo "== trailkeep backup =="
echo "Base: $BASE"
[ "$DRY" = "1" ] && echo "(dry-run: nothing will be written)"
[ -n "$ONLY" ] && echo "(only: $ONLY)"
echo ""

# Function: is this .jsonl new or changed in size since last time?
# Stores the size in $STATE/<hash>.size  (hash = encoded path). In dry-run it
# detects changes but writes nothing.
need_process() {
  local f="$1" key sz prev
  key=$(echo "$f" | "$HASHER" | cut -d' ' -f1)
  sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null)
  prev=$(cat "$STATE/$key.size" 2>/dev/null || echo "")
  if [ "$sz" != "$prev" ]; then
    [ "$DRY" = "1" ] || echo "$sz" > "$STATE/$key.size"
    return 0   # process
  fi
  return 1     # unchanged, skip
}

# --- Loud health detection -------------------------------------------------
# These tools store conversations in private, undocumented locations/formats.
# When a vendor moves a folder or changes a format, the backup must not fail
# silently — it should shout. Existing markdowns are never lost (cumulative);
# the risk is NEW data quietly not being captured. HEALTH_WARN tallies issues.
HEALTH_WARN=0

# Run a converter; warn loudly if it errors, or — having been handed new/changed
# raw data — converts 0 sessions (likely a changed path or storage format).
# Usage: run_convert <label> <python args...>
run_convert() {
  local label="$1"; shift
  local out conv rc
  out=$(python3 "$@" 2>&1); rc=$?
  printf '%s\n' "$out"
  if [ "$rc" -ne 0 ]; then
    echo "  !! $label: the converter errored (exit $rc) — its source format/path may have changed."
    HEALTH_WARN=$((HEALTH_WARN+1)); return 0
  fi
  conv=$(printf '%s' "$out" | sed -n 's/.*Converted:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)
  if [ "${conv:-x}" = "0" ]; then
    echo "  !! $label: had new/changed raw data but converted 0 sessions — the format may have changed."
    HEALTH_WARN=$((HEALTH_WARN+1))
  fi
}

# Source folder/db is gone but we have markdowns for it → the tool likely moved
# its data. Usage: warn_missing <label> <markdown-dir>
warn_missing() {
  local label="$1" mddir="$2" n=0
  [ -d "$mddir" ] && n=$(find "$mddir" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then
    echo "-- $label -- !! not found, but $n markdowns are on record — the tool may have moved its data."
    HEALTH_WARN=$((HEALTH_WARN+1))
  else
    echo "-- $label -- (not found, skipped)"
  fi
}

# ---------------------------------------------------------------------------
# SOURCE 1: Claude Code  (~/.claude/projects/*/*.jsonl)
# ---------------------------------------------------------------------------
if want claude; then
if [ -d "$HOME_CLAUDE/projects" ]; then
  echo "-- Claude Code --"
  SRC="$TMP/claude/conversations"
  rm -rf "$TMP/claude"; mkdir -p "$SRC"
  new=0
  while IFS= read -r -d '' f; do
    [[ "$f" == *"/subagents/"* ]] && continue
    [[ "$(basename "$f")" == agent-* ]] && continue
    if need_process "$f"; then
      proj=$(basename "$(dirname "$f")")
      mkdir -p "$SRC/$proj"
      cp "$f" "$SRC/$proj/$(basename "$f")"
      new=$((new+1))
    fi
  done < <(find "$HOME_CLAUDE/projects" -name "*.jsonl" -print0 2>/dev/null)
  if [ "$new" -gt 0 ]; then
    if [ "$DRY" = "1" ]; then echo "  $new new/changed sessions (dry-run, not converting)";
    else echo "  $new new/changed sessions → converting"; run_convert "Claude Code" "$PY_CLAUDE" "$SRC" "$BASE/markdown-claude" claude-code "$HOME_CLAUDE/history.jsonl"; fi
  else
    echo "  no changes"
  fi
  # Evidence Ledger: deterministic metrics over the FULL corpus (not the
  # incremental delta), written as a sidecar the viewer reads. $0, no network.
  # TRAILKEEP_CLAUDE_RAW_EXTRA (optional, os.pathsep-joined): extra raw-archive
  # dirs of recovered .jsonl, so token data survives Claude pruning the originals.
  if [ "$DRY" != "1" ]; then
    CLAUDE_RAW="$HOME_CLAUDE/projects${TRAILKEEP_CLAUDE_RAW_EXTRA:+:$TRAILKEEP_CLAUDE_RAW_EXTRA}"
    python3 "$PY_LEDGER" "$CLAUDE_RAW" "$BASE/markdown-claude" claude-code || true
  fi
else
  warn_missing "Claude Code" "$BASE/markdown-claude"
fi
echo ""
fi

# ---------------------------------------------------------------------------
# SOURCE 2: Codex  (~/.codex/sessions and ~/.codex/archived_sessions)
# ---------------------------------------------------------------------------
if want codex; then
if [ -d "$HOME_CODEX/sessions" ] || [ -d "$HOME_CODEX/archived_sessions" ]; then
  echo "-- Codex --"
  IDX="$HOME_CODEX/session_index.jsonl"

  # Active
  if [ -d "$HOME_CODEX/sessions" ]; then
    SRC="$TMP/codex-act"; rm -rf "$SRC"; mkdir -p "$SRC/all"
    new=0
    while IFS= read -r -d '' f; do
      if need_process "$f"; then cp "$f" "$SRC/all/$(basename "$f")"; new=$((new+1)); fi
    done < <(find "$HOME_CODEX/sessions" -name "*.jsonl" -print0 2>/dev/null)
    if [ "$new" -gt 0 ]; then
      if [ "$DRY" = "1" ]; then echo "  $new new/changed active (dry-run, not converting)";
      else echo "  $new new/changed active → converting"; run_convert "Codex" "$PY_CODEX" "$SRC" "$IDX" "$BASE/markdown-codex"; fi
    else
      echo "  active: no changes"
    fi
  fi

  # Archived: INCREMENTAL conversion (only new/changed) + cheap flag sync.
  if [ -d "$HOME_CODEX/archived_sessions" ]; then
    SRC="$TMP/codex-arch"; rm -rf "$SRC"; mkdir -p "$SRC/all"
    new=0
    while IFS= read -r -d '' f; do
      if need_process "$f"; then cp "$f" "$SRC/all/$(basename "$f")"; new=$((new+1)); fi
    done < <(find "$HOME_CODEX/archived_sessions" -name "*.jsonl" -print0 2>/dev/null)
    if [ "$new" -gt 0 ]; then
      if [ "$DRY" = "1" ]; then echo "  $new new/changed archived (dry-run, not converting)";
      else echo "  $new new/changed archived → converting"; python3 "$PY_CODEX" "$SRC" "$IDX" "$BASE/markdown-codex" archived; fi
    else
      echo "  archived: no changes"
    fi
    # Cheap flag sync: detect sessions that MOVED to archived whose .md still says
    # archived:false. Mark them true without reconverting, and drop active/archived dups.
    # Handles both English ('archived:') and legacy Spanish ('archivada:') metadata.
    if [ "$DRY" != "1" ]; then
    python3 - "$BASE/markdown-codex" "$HOME_CODEX/archived_sessions" <<'PYEOF'
import sys, glob, os, re, json
mddir, archdir = sys.argv[1], sys.argv[2]
# currently archived ids
arch_ids=set()
for f in glob.glob(os.path.join(archdir,'**','*.jsonl'), recursive=True):
    for l in open(f):
        l=l.strip()
        if not l: continue
        try: o=json.loads(l)
        except: continue
        if o.get('type')=='session_meta':
            sid=o.get('payload',{}).get('id')
            if sid: arch_ids.add(sid)
            break
def is_archived(txt):
    return 'archived: true' in txt or 'archivada: true' in txt
# index markdowns by id, with their archived state
by_id={}
for f in glob.glob(os.path.join(mddir,'**','*.md'), recursive=True):
    txt=open(f).read()
    m=re.search(r'id:\s*([0-9a-f-]{36})', txt)
    if not m: continue
    by_id.setdefault(m.group(1),[]).append([f, is_archived(txt), txt])
marked=0; removed=0
for sid in arch_ids:
    if sid not in by_id: continue
    lst=by_id[sid]
    has_true=any(a for _,a,_ in lst)
    if not has_true:
        # no .md for this id is marked archived:true → mark the (single) existing one
        for item in lst:
            f,a,txt=item
            updated=re.sub(r'archived:\s*false','archived: true',txt,count=1)
            updated=re.sub(r'archivada:\s*false','archivada: true',updated,count=1)
            if updated!=txt:
                open(f,'w').write(updated); marked+=1; item[1]=True
    # remove active:false duplicates if there's already a true one
    if any(a for _,a,_ in by_id[sid]):
        for f,a,_ in by_id[sid]:
            if not a and os.path.exists(f):
                os.remove(f); removed+=1
msg=[]
if marked: msg.append(f"{marked} marked archived")
if removed: msg.append(f"{removed} active duplicates removed")
if msg: print("  flag sync: "+", ".join(msg))
PYEOF
    fi
  fi
  # Evidence Ledger over active AND archived Codex sessions (both keep raw token
  # usage; archived rollouts would otherwise show counts-only via the .md).
  if [ "$DRY" != "1" ] && { [ -d "$HOME_CODEX/sessions" ] || [ -d "$HOME_CODEX/archived_sessions" ]; }; then
    CODEX_RAW="$HOME_CODEX/sessions"
    [ -d "$HOME_CODEX/archived_sessions" ] && CODEX_RAW="$CODEX_RAW:$HOME_CODEX/archived_sessions"
    python3 "$PY_LEDGER" "$CODEX_RAW" "$BASE/markdown-codex" codex codex || true
  fi
else
  warn_missing "Codex" "$BASE/markdown-codex"
fi
echo ""
fi

# ---------------------------------------------------------------------------
# SOURCE 3: Cowork  (nested structure; real conversations under .claude/projects)
# ---------------------------------------------------------------------------
if want cowork; then
if [ -d "$COWORK_DIR" ]; then
  echo "-- Cowork --"
  SRC="$TMP/cowork/cowork"; rm -rf "$TMP/cowork"; mkdir -p "$SRC"
  new=0
  while IFS= read -r -d '' f; do
    if need_process "$f"; then cp "$f" "$SRC/$(basename "$f")"; new=$((new+1)); fi
  done < <(find "$COWORK_DIR" -path "*/.claude/projects/*.jsonl" ! -name "audit.jsonl" ! -path "*/subagents/*" -print0 2>/dev/null)
  if [ "$new" -gt 0 ]; then
    if [ "$DRY" = "1" ]; then echo "  $new new/changed sessions (dry-run, not converting)";
    else echo "  $new new/changed sessions → converting"; run_convert "Cowork" "$PY_CLAUDE" "$TMP/cowork" "$BASE/markdown-cowork" cowork "$HOME_CLAUDE/history.jsonl"; fi
  else
    echo "  no changes"
  fi
  # Evidence Ledger (Cowork uses the Claude .jsonl format → 'claude' scanner).
  if [ "$DRY" != "1" ]; then
    python3 "$PY_LEDGER" "$COWORK_DIR" "$BASE/markdown-cowork" cowork claude || true
  fi
else
  warn_missing "Cowork" "$BASE/markdown-cowork"
fi
echo ""
fi

# ---------------------------------------------------------------------------
# SOURCE 4: OpenCode  (~/.local/share/opencode/opencode.db, SQLite)
# Fully reconverted when the DB changed in size (incremental at the DB level).
# ---------------------------------------------------------------------------
if want opencode; then
OPENCODE_DB="$XDG_DATA/opencode/opencode.db"
PY_OPENCODE="$SCRIPT_DIR/converters/convert_opencode.py"
if [ -f "$OPENCODE_DB" ] && [ -f "$PY_OPENCODE" ]; then
  echo "-- OpenCode --"
  if need_process "$OPENCODE_DB"; then
    if [ "$DRY" = "1" ]; then echo "  DB changed (dry-run, not converting)";
    else echo "  DB changed → converting"; run_convert "OpenCode" "$PY_OPENCODE" "$OPENCODE_DB" "$BASE/markdown-opencode"; fi
  else
    echo "  no changes"
  fi
  # Evidence Ledger (OpenCode carries token usage + model per message).
  if [ "$DRY" != "1" ]; then
    python3 "$PY_LEDGER" "$OPENCODE_DB" "$BASE/markdown-opencode" opencode opencode || true
  fi
elif [ ! -f "$PY_OPENCODE" ]; then
  echo "-- OpenCode -- (convert_opencode.py not found, skipped)"
else
  warn_missing "OpenCode" "$BASE/markdown-opencode"
fi
echo ""
fi

# ---------------------------------------------------------------------------
# SOURCE 5: Cursor  (globalStorage/state.vscdb, SQLite with composers + bubbles)
# The global DB holds the conversations; reconverted if the DB changed in size.
# ---------------------------------------------------------------------------
if want cursor; then
if [ "$OS" = "Darwin" ]; then
  CURSOR_DB="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
else
  CURSOR_DB="$XDG_CONFIG/Cursor/User/globalStorage/state.vscdb"
fi
PY_CURSOR="$SCRIPT_DIR/converters/convert_cursor.py"
if [ -f "$CURSOR_DB" ] && [ -f "$PY_CURSOR" ]; then
  echo "-- Cursor --"
  if need_process "$CURSOR_DB"; then
    if [ "$DRY" = "1" ]; then echo "  DB changed (dry-run, not converting)";
    else echo "  DB changed → converting"; run_convert "Cursor" "$PY_CURSOR" "$CURSOR_DB" "$BASE/markdown-cursor"; fi
  else
    echo "  no changes"
  fi
  # Evidence Ledger (Cursor: counts only — its store carries no token usage).
  if [ "$DRY" != "1" ]; then
    python3 "$PY_LEDGER" "$CURSOR_DB" "$BASE/markdown-cursor" cursor cursor || true
  fi
elif [ ! -f "$PY_CURSOR" ]; then
  echo "-- Cursor -- (convert_cursor.py not found, skipped)"
else
  warn_missing "Cursor" "$BASE/markdown-cursor"
fi
echo ""
fi

# ---------------------------------------------------------------------------
# Project metadata: deterministic git / stack / deploy / status per project, written as a
# _projects.json sidecar the viewer joins with the ledger. Cross-source, so it runs
# once over all sources. Reads the project dirs locally (git, manifests, deploy
# hints); no network.
# ---------------------------------------------------------------------------
if [ "$DRY" != "1" ]; then
  python3 "$PY_PROJECTS" "$BASE" \
    "claude=$HOME_CLAUDE/projects" "cowork=$COWORK_DIR" \
    "codex=$HOME_CODEX/sessions" "opencode=${OPENCODE_DB:-}" || true
fi

# clean up temporaries
rm -rf "$TMP"

if [ "$DRY" = "1" ]; then
  echo "== Dry-run done (nothing written) =="
  exit 0
fi

echo "== Backup updated =="
echo "Markdowns:"
# count per source and total, and record in the log
TOTAL=0
declare -a SUMMARY
for d in markdown-claude markdown-codex markdown-cowork markdown-opencode markdown-cursor; do
  if [ -d "$BASE/$d" ]; then
    n=$(find "$BASE/$d" -name '*.md' | wc -l | tr -d ' ')
    echo "  $d: $n"
    TOTAL=$((TOTAL + n))
    SUMMARY+=("\"${d#markdown-}\": $n")
  fi
done
echo "  TOTAL: $TOTAL"

# write this run into log.json (cumulative run history)
LOG="$BASE/.sync-state/log.json"
LOG_PUBLIC="$BASE/log.json"
mkdir -p "$BASE/.sync-state"
# net new conversations vs the previous run (read BEFORE prepending the new entry)
PREV=$(python3 -c "import json,os,sys; p=sys.argv[1]; h=json.load(open(p)) if os.path.exists(p) else []; print(h[0].get('total',0) if h else 0)" "$LOG" 2>/dev/null || echo 0)
NEW=$((TOTAL - PREV)); [ "$NEW" -lt 0 ] && NEW=0
echo "  NEW: $NEW"
DATE=$(date +"%Y-%m-%dT%H:%M:%S%z")
ENTRY=$(printf '{"date":"%s","total":%d,%s}' "$DATE" "$TOTAL" "$(IFS=,; echo "${SUMMARY[*]}")")
# prepend the new entry to the history (keep last 50)
python3 - "$LOG" "$LOG_PUBLIC" "$ENTRY" <<'PYEOF'
import sys, json, os
log_path, public_log_path, entry = sys.argv[1], sys.argv[2], sys.argv[3]
hist = []
if os.path.exists(log_path):
    try: hist = json.load(open(log_path))
    except Exception: hist = []
try: e = json.loads(entry)
except Exception: e = {"date": "?", "total": 0}
hist.insert(0, e)
hist = hist[:50]
json.dump(hist, open(log_path, "w"), ensure_ascii=False, indent=2)
json.dump(hist, open(public_log_path, "w"), ensure_ascii=False, indent=2)
PYEOF

# desktop notification (macOS osascript, or Linux notify-send if present)
if [ "$NEW" -gt 0 ]; then BODY="+$NEW new · $TOTAL total conversations"; else BODY="$TOTAL conversations · no new"; fi
if [ "$HEALTH_WARN" -gt 0 ]; then BODY="$BODY · WARNING: $HEALTH_WARN source(s) need attention"; fi
if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"$BODY\" with title \"trailkeep\" subtitle \"Backup updated\" sound name \"\"" >/dev/null 2>&1 || true
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "trailkeep — backup updated" "$BODY" >/dev/null 2>&1 || true
fi

if [ "$HEALTH_WARN" -gt 0 ]; then
  echo ""
  echo "!! HEALTH: $HEALTH_WARN source(s) may have an issue (see the !! lines above)."
  echo "   Your existing backups are intact (cumulative, never deleted), but NEW data"
  echo "   from those tools may not be getting captured — a tool likely changed where"
  echo "   or how it stores conversations. Check that source's path/format."
fi

echo ""
echo "Open viewer.html and point it at: $BASE"
