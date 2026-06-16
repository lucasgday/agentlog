#!/usr/bin/env bash
# update-backup.sh
# Incremental, cumulative backup of LLM conversations (Claude Code, Codex, Cowork, OpenCode, Cursor).
# - Base = the folder where this script lives (the whole folder can be moved).
# - Optional override: pass a path as the first argument.
# - Incremental: only processes .jsonl that are new or changed in size since the last run.
# - Cumulative: never deletes already-generated markdowns, even if the source removes them (cleanup).
# - Archive sync (Codex): if a session moved to archived_sessions, marks its .md as archived:true.
# - Extensible: each source is a block that is skipped if its origin doesn't exist.

set -uo pipefail

# ---------- base location ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${1:-$SCRIPT_DIR}"           # optional override via argument
cd "$BASE" || { echo "Could not enter $BASE"; exit 1; }

STATE="$BASE/.sync-state"          # index of processed sizes (incremental)
mkdir -p "$STATE"
TMP="$BASE/.sync-tmp"
# The converters live next to this script (SCRIPT_DIR), not in the data folder,
# so the code and the markdowns can live in different folders.
PY_CLAUDE="$SCRIPT_DIR/convert_claude.py"
PY_CODEX="$SCRIPT_DIR/convert_codex.py"

HOME_CLAUDE="$HOME/.claude"
HOME_CODEX="$HOME/.codex"
COWORK_DIR="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"

echo "== LLM backup =="
echo "Base: $BASE"
echo ""

# Function: is this .jsonl new or changed in size since last time?
# Stores the size in $STATE/<hash>.size  (hash = encoded path)
need_process() {
  local f="$1" key sz prev
  key=$(echo "$f" | shasum | cut -d' ' -f1)
  sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null)
  prev=$(cat "$STATE/$key.size" 2>/dev/null || echo "")
  if [ "$sz" != "$prev" ]; then
    echo "$sz" > "$STATE/$key.size"
    return 0   # process
  fi
  return 1     # unchanged, skip
}

# ---------------------------------------------------------------------------
# SOURCE 1: Claude Code  (~/.claude/projects/*/*.jsonl)
# ---------------------------------------------------------------------------
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
    echo "  $new new/changed sessions → converting"
    python3 "$PY_CLAUDE" "$SRC" "$BASE/markdown-claude" claude-code "$HOME_CLAUDE/history.jsonl"
  else
    echo "  no changes"
  fi
else
  echo "-- Claude Code -- (not found, skipped)"
fi
echo ""

# ---------------------------------------------------------------------------
# SOURCE 2: Codex  (~/.codex/sessions and ~/.codex/archived_sessions)
# ---------------------------------------------------------------------------
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
      echo "  $new new/changed active → converting"
      python3 "$PY_CODEX" "$SRC" "$IDX" "$BASE/markdown-codex"
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
      echo "  $new new/changed archived → converting"
      python3 "$PY_CODEX" "$SRC" "$IDX" "$BASE/markdown-codex" archived
    else
      echo "  archived: no changes"
    fi
    # Cheap flag sync: detect sessions that MOVED to archived whose .md still says
    # archived:false. Mark them true without reconverting, and drop active/archived dups.
    # Handles both English ('archived:') and legacy Spanish ('archivada:') metadata.
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
            nuevo=re.sub(r'archived:\s*false','archived: true',txt,count=1)
            nuevo=re.sub(r'archivada:\s*false','archivada: true',nuevo,count=1)
            if nuevo!=txt:
                open(f,'w').write(nuevo); marked+=1; item[1]=True
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
else
  echo "-- Codex -- (not found, skipped)"
fi
echo ""

# ---------------------------------------------------------------------------
# SOURCE 3: Cowork  (nested structure; real conversations under .claude/projects)
# ---------------------------------------------------------------------------
if [ -d "$COWORK_DIR" ]; then
  echo "-- Cowork --"
  SRC="$TMP/cowork/cowork"; rm -rf "$TMP/cowork"; mkdir -p "$SRC"
  new=0
  while IFS= read -r -d '' f; do
    if need_process "$f"; then cp "$f" "$SRC/$(basename "$f")"; new=$((new+1)); fi
  done < <(find "$COWORK_DIR" -path "*/.claude/projects/*.jsonl" ! -name "audit.jsonl" ! -path "*/subagents/*" -print0 2>/dev/null)
  if [ "$new" -gt 0 ]; then
    echo "  $new new/changed sessions → converting"
    python3 "$PY_CLAUDE" "$TMP/cowork" "$BASE/markdown-cowork" cowork "$HOME_CLAUDE/history.jsonl"
  else
    echo "  no changes"
  fi
else
  echo "-- Cowork -- (not found, skipped)"
fi
echo ""

# ---------------------------------------------------------------------------
# SOURCE 4: OpenCode  (~/.local/share/opencode/opencode.db, SQLite)
# Fully reconverted when the DB changed in size (incremental at the DB level).
# ---------------------------------------------------------------------------
OPENCODE_DB="$HOME/.local/share/opencode/opencode.db"
PY_OPENCODE="$SCRIPT_DIR/convert_opencode.py"
if [ -f "$OPENCODE_DB" ] && [ -f "$PY_OPENCODE" ]; then
  echo "-- OpenCode --"
  if need_process "$OPENCODE_DB"; then
    echo "  DB changed → converting"
    python3 "$PY_OPENCODE" "$OPENCODE_DB" "$BASE/markdown-opencode"
  else
    echo "  no changes"
  fi
elif [ ! -f "$PY_OPENCODE" ]; then
  echo "-- OpenCode -- (convert_opencode.py not found, skipped)"
else
  echo "-- OpenCode -- (not found, skipped)"
fi
echo ""

# ---------------------------------------------------------------------------
# SOURCE 5: Cursor  (globalStorage/state.vscdb, SQLite with composers + bubbles)
# The global DB holds the conversations; reconverted if the DB changed in size.
# ---------------------------------------------------------------------------
CURSOR_DB="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
PY_CURSOR="$SCRIPT_DIR/convert_cursor.py"
if [ -f "$CURSOR_DB" ] && [ -f "$PY_CURSOR" ]; then
  echo "-- Cursor --"
  if need_process "$CURSOR_DB"; then
    echo "  DB changed → converting"
    python3 "$PY_CURSOR" "$CURSOR_DB" "$BASE/markdown-cursor"
  else
    echo "  no changes"
  fi
elif [ ! -f "$PY_CURSOR" ]; then
  echo "-- Cursor -- (convert_cursor.py not found, skipped)"
else
  echo "-- Cursor -- (not found, skipped)"
fi
echo ""

# clean up temporaries
rm -rf "$TMP"

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
mkdir -p "$BASE/.sync-state"
DATE=$(date +"%Y-%m-%dT%H:%M:%S%z")
ENTRY=$(printf '{"date":"%s","total":%d,%s}' "$DATE" "$TOTAL" "$(IFS=,; echo "${SUMMARY[*]}")")
# prepend the new entry to the history (keep last 50)
python3 - "$LOG" "$ENTRY" <<'PYEOF'
import sys, json, os
log_path, entry = sys.argv[1], sys.argv[2]
hist = []
if os.path.exists(log_path):
    try: hist = json.load(open(log_path))
    except Exception: hist = []
try: e = json.loads(entry)
except Exception: e = {"date": "?", "total": 0}
hist.insert(0, e)
hist = hist[:50]
json.dump(hist, open(log_path, "w"), ensure_ascii=False, indent=2)
PYEOF

# macOS notification (only if osascript exists, i.e. on a Mac)
if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"Total: $TOTAL conversations backed up\" with title \"LLM backup\" sound name \"\"" >/dev/null 2>&1 || true
fi

echo ""
echo "Open viewer.html and point it at: $BASE"
