#!/usr/bin/env python3
"""Evidence Ledger — deterministic, $0, on-device metrics for every source.

Reads each tool's RAW storage (the same data the converters read) and writes a
sidecar `_ledger.json` the viewer renders. No LLM, no network: every number is a
parse-and-count over the raw data (token usage, tool calls, test/build runs,
files modified, errors). This is the local answer to a cloud "evidence ledger" —
the counters never leave the machine.

One scanner per format, all emitting the same per-session metrics dict:
  - claude   : Claude Code / Cowork .jsonl  (usage, tool_use, model)
  - codex     : Codex rollout-*.jsonl        (info.total_token_usage, function_call)
  - opencode  : opencode.db (SQLite)         (message.tokens, modelID, tool parts)
  - cursor    : state.vscdb (SQLite)         (bubbles + toolResults; no tokens)

Usage: extract_ledger.py <input_path> <output_dir> <source> [format]
  <input_path>  projects dir (claude/codex) or the .db file (cursor/opencode)
  <output_dir>  where markdown-*/ live; _ledger.json is written/merged here
  <source>      ledger key (claude-code / cowork / codex / opencode / cursor)
  [format]      claude | codex | opencode | cursor (default: inferred from source)

Per-source results are merged into _ledger.json (like _backup-info.json). The
viewer reads every _ledger.json and aggregates across sources.

INCREMENTAL: per-session metrics are cached in `_ledger-cache.json` (in the data
folder, next to the markdowns), validated by size:mtime — file-based sources
(claude/codex) re-scan only changed sessions; DB sources re-scan only when changed.

CUMULATIVE + PORTABLE (AGENTS.md rule #3): a session stays in the ledger as long as
its `.md` exists, even after the tool prunes the raw transcript. Token data, once
computed from raw, is carried forward from the cache — so it persists in the data
folder and travels with the markdowns. Recovered raw (AGENTLOG_CLAUDE_RAW_EXTRA, see
below) only needs to be read ONCE to seed the tokens; after that the archive is
optional. Nothing already measured is ever lost just because the raw went away.
"""
import json, os, re, sys, glob, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from convert_claude import project_label
except Exception:
    def project_label(folder):
        parts = [p for p in folder.split("-") if p]
        return parts[-1] if parts else folder

CACHE_VERSION = 3

# Recovered-raw archives often hold duplicate project folders with a localized
# "(del respaldo)" / "(copy)" suffix; strip it so they map to the real project.
_DUP_SUFFIX = re.compile(r"\s*\((?:del respaldo|copia|copy|backup|restored)\)\s*$", re.I)


def clean_project_dir(name):
    return _DUP_SUFFIX.sub("", name)

TEST_RE = re.compile(
    r"\b(pytest|jest|vitest|go test|cargo test|npm (?:run )?test|"
    r"yarn test|py_compile|bash -n|rspec|phpunit|mvn test|gradle test)\b")
BUILD_RE = re.compile(
    r"\b(make|tsc|webpack|vite build|npm run build|yarn build|"
    r"cargo build|go build|mvn package|gradle build|docker build)\b")

# Edit-ish tool names across tools (lowercased) → count distinct files touched.
EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "apply_patch",
              "str_replace_editor", "create_file", "createfile"}
WEB_TOOLS = {"websearch", "webfetch", "web_search", "web_fetch", "webfetchtool"}


def new_session(project):
    return {
        "id": None,
        "project": project or "(no project)", "sessions": 1,
        "user_messages": 0, "assistant_messages": 0,
        "tool_calls": 0, "mcp_tool_calls": 0,
        "test_runs": 0, "build_runs": 0, "errors": 0, "web_searches": 0,
        "tokens": {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0},
        "models": {}, "tools": {}, "files": [],
        "first": None, "last": None, "_files": set(),
    }


def finish_session(s):
    s["files"] = sorted(s.pop("_files"))
    return s


def epoch_to_iso(v):
    """Epoch (seconds or milliseconds) → ISO string. SQLite sources store ints."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 1_000_000_000_000:   # 13+ digits → milliseconds
        v = v / 1000.0
    try:
        return datetime.datetime.fromtimestamp(v, datetime.timezone.utc).isoformat()
    except Exception:
        return None


def bump_time(s, ts):
    if not ts:
        return
    if s["first"] is None or ts < s["first"]:
        s["first"] = ts
    if s["last"] is None or ts > s["last"]:
        s["last"] = ts


def model_tokens(s, model):
    return s["models"].setdefault(model or "unknown", {
        "input": 0, "output": 0, "cache_creation": 0, "cache_read": 0})


def record_tool(s, name, cmd=None, fpath=None):
    """Common bookkeeping for one tool call (name + optional command/file)."""
    s["tool_calls"] += 1
    s["tools"][name] = s["tools"].get(name, 0) + 1
    low = name.lower()
    if name.startswith("mcp__") or low.startswith("mcp_"):
        s["mcp_tool_calls"] += 1
    if low in WEB_TOOLS:
        s["web_searches"] += 1
    if low in EDIT_TOOLS and fpath:
        s["_files"].add(fpath)
    if cmd:
        if TEST_RE.search(cmd):
            s["test_runs"] += 1
        if BUILD_RE.search(cmd):
            s["build_runs"] += 1


# --------------------------------------------------------------------------
# Claude Code / Cowork  (.jsonl)
# --------------------------------------------------------------------------
def scan_claude(path, plabel):
    s = new_session(plabel)
    s["id"] = os.path.splitext(os.path.basename(path))[0]  # uuid == the .md's id
    for line in open(path, errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        bump_time(s, d.get("timestamp"))
        if d.get("isApiErrorMessage"):
            s["errors"] += 1
        tur = d.get("toolUseResult")
        if isinstance(tur, dict) and (tur.get("is_error") or tur.get("error")):
            s["errors"] += 1
        msg = d.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", d.get("type"))
        content = msg.get("content")
        model = msg.get("model")
        usage = msg.get("usage")
        if usage and model and model != "<synthetic>":
            s["tokens"]["input"] += usage.get("input_tokens", 0) or 0
            s["tokens"]["output"] += usage.get("output_tokens", 0) or 0
            s["tokens"]["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
            s["tokens"]["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
            mm = model_tokens(s, model)
            mm["input"] += usage.get("input_tokens", 0) or 0
            mm["output"] += usage.get("output_tokens", 0) or 0
            mm["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
            mm["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        has_text = isinstance(content, str) and content.strip()
        has_tool_result = False
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    has_text = True
                elif bt == "tool_result":
                    has_tool_result = True
                    if b.get("is_error"):
                        s["errors"] += 1
                elif bt == "tool_use":
                    inp = b.get("input") or {}
                    record_tool(s, b.get("name", "tool"),
                                cmd=inp.get("command"),
                                fpath=inp.get("file_path") or inp.get("path"))
        if role == "user" and has_text and not has_tool_result:
            s["user_messages"] += 1
        elif role == "assistant" and (has_text or usage) and model != "<synthetic>":
            s["assistant_messages"] += 1
    return finish_session(s)


# --------------------------------------------------------------------------
# Codex  (rollout-*.jsonl)
# --------------------------------------------------------------------------
def _codex_cmd(args):
    try:
        a = json.loads(args) if isinstance(args, str) else args
    except Exception:
        return ""
    if isinstance(a, dict):
        cmd = a.get("command")
        if isinstance(cmd, list):
            return " ".join(map(str, cmd))
        if isinstance(cmd, str):
            return cmd
    return ""


# Codex edits files via `apply_patch` heredocs inside exec_command; recover the
# touched paths from the patch markers so files_modified isn't always zero.
PATCH_FILE_RE = re.compile(r"\*\*\* (?:Add|Update|Delete) File: (.+)")


def _codex_patch_files(cmd):
    if not cmd or "*** " not in cmd:
        return []
    return [m.strip() for m in PATCH_FILE_RE.findall(cmd)]


def scan_codex(path, _plabel=None):
    s = new_session(None)
    model = None
    best_total = None  # codex reports cumulative usage; keep the largest snapshot
    for line in open(path, errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        bump_time(s, d.get("timestamp"))
        p = d.get("payload", {}) or {}
        typ = d.get("type")
        if typ == "session_meta":
            cwd = p.get("cwd")
            if cwd:
                s["project"] = os.path.basename(cwd.rstrip("/")) or s["project"]
            if p.get("id"):
                s["id"] = p.get("id")  # matches the .md's id
            bump_time(s, p.get("timestamp"))
        if isinstance(p, dict) and p.get("model"):
            model = p.get("model")
        # cumulative token usage lives on event_msg payloads (info.total_token_usage)
        info = p.get("info") if isinstance(p, dict) else None
        tot = (info or {}).get("total_token_usage") if isinstance(info, dict) else None
        if isinstance(tot, dict):
            if best_total is None or (tot.get("total_tokens", 0) or 0) > (best_total.get("total_tokens", 0) or 0):
                best_total = tot
        if isinstance(p, dict) and isinstance(p.get("type"), str) and "error" in p["type"]:
            s["errors"] += 1
        ptype = p.get("type") if isinstance(p, dict) else None
        if typ == "response_item":
            if ptype == "message":
                role = p.get("role")
                if role == "user":
                    s["user_messages"] += 1
                elif role == "assistant":
                    s["assistant_messages"] += 1
            elif ptype == "function_call":
                cmd = _codex_cmd(p.get("arguments"))
                record_tool(s, p.get("name", "tool"), cmd=cmd)
                for fp in _codex_patch_files(cmd):
                    s["_files"].add(fp)
            elif ptype == "function_call_output":
                out = p.get("output")
                blob = json.dumps(out) if isinstance(out, (dict, list)) else str(out or "")
                if '"success": false' in blob or '"exit_code": 1' in blob:
                    s["errors"] += 1
        elif typ == "compacted":
            for m in p.get("replacement_history", []):
                if isinstance(m, dict) and m.get("type") == "message":
                    if m.get("role") == "user":
                        s["user_messages"] += 1
                    elif m.get("role") == "assistant":
                        s["assistant_messages"] += 1
    if best_total:
        # split cached input out so semantics match Claude (input = fresh prompt)
        inp = best_total.get("input_tokens", 0) or 0
        cached = best_total.get("cached_input_tokens", 0) or 0
        out = (best_total.get("output_tokens", 0) or 0) + (best_total.get("reasoning_output_tokens", 0) or 0)
        s["tokens"]["input"] = max(inp - cached, 0)
        s["tokens"]["cache_read"] = cached
        s["tokens"]["output"] = out
        mm = model_tokens(s, model)
        mm["input"] = s["tokens"]["input"]
        mm["cache_read"] = cached
        mm["output"] = out
    return finish_session(s)


# --------------------------------------------------------------------------
# OpenCode  (opencode.db SQLite)
# --------------------------------------------------------------------------
def scan_opencode_db(db_path):
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = []
    try:
        sessions = con.execute(
            "SELECT id, title, directory, time_created FROM session").fetchall()
    except Exception:
        con.close()
        return out
    for row in sessions:
        sid = row["id"]
        proj = os.path.basename((row["directory"] or "").rstrip("/")) or "(no project)"
        s = new_session(proj)
        s["id"] = str(sid)
        s["_key"] = "opencode\t" + str(sid)
        bump_time(s, epoch_to_iso(row["time_created"]))
        try:
            msgs = con.execute(
                "SELECT id, data FROM message WHERE session_id=?", (sid,)).fetchall()
        except Exception:
            msgs = []
        for m in msgs:
            try:
                md = json.loads(m["data"])
            except Exception:
                continue
            role = md.get("role")
            if role == "user":
                s["user_messages"] += 1
            elif role == "assistant":
                s["assistant_messages"] += 1
                tk = md.get("tokens") or {}
                if isinstance(tk, dict):
                    cache = tk.get("cache") or {}
                    model = md.get("modelID") or "unknown"
                    inp = tk.get("input", 0) or 0
                    o = (tk.get("output", 0) or 0) + (tk.get("reasoning", 0) or 0)
                    cr = (cache.get("read", 0) or 0)
                    cw = (cache.get("write", 0) or 0)
                    s["tokens"]["input"] += inp
                    s["tokens"]["output"] += o
                    s["tokens"]["cache_read"] += cr
                    s["tokens"]["cache_creation"] += cw
                    mm = model_tokens(s, model)
                    mm["input"] += inp; mm["output"] += o
                    mm["cache_read"] += cr; mm["cache_creation"] += cw
                if md.get("error"):
                    s["errors"] += 1
            try:
                parts = con.execute(
                    "SELECT data FROM part WHERE message_id=?", (m["id"],)).fetchall()
            except Exception:
                parts = []
            for pr in parts:
                try:
                    pd = json.loads(pr["data"])
                except Exception:
                    continue
                if pd.get("type") == "tool":
                    st = pd.get("state", {}) or {}
                    inp = st.get("input", {}) or {}
                    cmd = inp.get("command")
                    if isinstance(cmd, list):
                        cmd = " ".join(map(str, cmd))
                    record_tool(s, pd.get("tool", "tool"), cmd=cmd,
                                fpath=inp.get("filePath") or inp.get("path"))
        out.append(finish_session(s))
    con.close()
    return out


# --------------------------------------------------------------------------
# Cursor  (state.vscdb SQLite) — counts only, no tokens
# --------------------------------------------------------------------------
def scan_cursor_db(db_path):
    import sqlite3
    from collections import defaultdict
    con = sqlite3.connect(db_path)
    out = []
    try:
        row = con.execute(
            "SELECT value FROM ItemTable WHERE key=?",
            ("composer.composerHeaders",)).fetchone()
        headers = json.loads(row[0]) if row else {}
    except Exception:
        headers = {}
    meta = {}
    for c in headers.get("allComposers", []):
        cid = c.get("composerId")
        if cid:
            meta[cid] = c.get("createdAt")
    bubbles = defaultdict(list)
    try:
        for key, val in con.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"):
            parts = key.split(":")
            if len(parts) < 3:
                continue
            try:
                bubbles[parts[1]].append(json.loads(val))
            except Exception:
                pass
    except Exception:
        pass
    con.close()
    for cid, bubs in bubbles.items():
        s = new_session("cursor")
        s["id"] = str(cid)
        s["_key"] = "cursor\t" + str(cid)
        bump_time(s, epoch_to_iso(meta.get(cid)))
        for b in bubs:
            typ = b.get("type")
            if typ == 1:
                if (b.get("text") or "").strip():
                    s["user_messages"] += 1
            elif typ == 2:
                if (b.get("text") or "").strip():
                    s["assistant_messages"] += 1
            for tr in (b.get("toolResults") or []):
                args = tr.get("args") or {}
                record_tool(s, tr.get("name") or "tool",
                            cmd=args.get("command"),
                            fpath=args.get("path") or args.get("filePath"))
        out.append(finish_session(s))
    return out


# --------------------------------------------------------------------------
# Markdown fallback (any source) — covers sessions whose raw was pruned but
# whose cumulative .md backup survives. Counts only: tokens/model live in raw.
# --------------------------------------------------------------------------
MD_USER_RE = re.compile(r"^###\s+(?:You|Tú)\s*$", re.M)
MD_ASSIST_RE = re.compile(r"^###\s+(?:Claude|Codex|Cursor|OpenCode)\s*$", re.M)
MD_TOOL_RE = re.compile(r"\[(?:tool|herramienta):\s*([^\]\n→]+?)(?:\s*→\s*([^\]\n]*))?\]")
MD_BASH_RE = re.compile(r"```bash\n(.*?)\n```", re.S)


def scan_markdown(path):
    try:
        txt = open(path, errors="ignore").read()
    except Exception:
        return None
    s = new_session(None)
    cm = re.search(r"<!--(.*?)-->", txt, re.S)
    meta = cm.group(1) if cm else ""
    mid = re.search(r"id:\s*([^|>\s]+)", meta)
    s["id"] = mid.group(1).strip() if mid else None
    md = re.search(r"(?:date|fecha):\s*([^|]+)", meta)
    if md:
        bump_time(s, md.group(1).strip())
    mp = re.search(r"(?:project|proyecto):\s*([^|]+)", meta)
    if mp:
        s["project"] = mp.group(1).strip() or s["project"]
    s["user_messages"] = len(MD_USER_RE.findall(txt))
    s["assistant_messages"] = len(MD_ASSIST_RE.findall(txt))
    for tm in MD_TOOL_RE.finditer(txt):
        name = (tm.group(1) or "tool").strip().split()
        name = name[0] if name else "tool"
        target = (tm.group(2) or "").strip()
        s["tool_calls"] += 1
        s["tools"][name] = s["tools"].get(name, 0) + 1
        low = name.lower()
        if name.startswith("mcp__") or low.startswith("mcp_"):
            s["mcp_tool_calls"] += 1
        if low in WEB_TOOLS:
            s["web_searches"] += 1
        if low in EDIT_TOOLS and target:
            s["_files"].add(target)
        if target:  # cursor/opencode keep the shell command in the → target
            if TEST_RE.search(target):
                s["test_runs"] += 1
            if BUILD_RE.search(target):
                s["build_runs"] += 1
    for bm in MD_BASH_RE.finditer(txt):  # claude/codex keep it in a bash block
        cmd = bm.group(1)
        if TEST_RE.search(cmd):
            s["test_runs"] += 1
        if BUILD_RE.search(cmd):
            s["build_runs"] += 1
    return finish_session(s)


def collect_markdown(out_dir, old):
    """Scan out_dir/*.md → (cache, every session's metrics). The Markdown backup
    is cumulative + portable, so it is the durable anchor: a session is "still
    real" iff it has a .md here, even after its raw transcript was pruned."""
    cache, sessions, hits, misses = {}, [], 0, 0
    for root, _dirs, fnames in os.walk(out_dir):
        for base in fnames:
            if not base.endswith(".md"):
                continue
            f = os.path.join(root, base)
            try:
                st = os.stat(f); sig = "%d:%d" % (st.st_size, int(st.st_mtime))
            except OSError:
                continue
            key = "md\t" + f
            entry = old.get(key)
            if entry and entry.get("sig") == sig and isinstance(entry.get("metrics"), dict):
                metrics = entry["metrics"]; hits += 1
            else:
                metrics = scan_markdown(f)
                if metrics is None:
                    continue
                misses += 1
            cache[key] = {"sig": sig, "metrics": metrics}
            sessions.append(metrics)
    return cache, sessions, hits, misses


# --------------------------------------------------------------------------
# Aggregate + cache
# --------------------------------------------------------------------------
SCALAR_KEYS = ("sessions", "user_messages", "assistant_messages", "tool_calls",
               "mcp_tool_calls", "test_runs", "build_runs", "errors", "web_searches")


def aggregate(sessions):
    T = {k: 0 for k in SCALAR_KEYS}
    tok = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    models, tools, files = {}, {}, set()
    projects = {}
    first = last = None
    for s in sessions:
        for k in SCALAR_KEYS:
            T[k] += s.get(k, 0)
        for k in tok:
            tok[k] += s.get("tokens", {}).get(k, 0)
        for m, v in s.get("models", {}).items():
            d = models.setdefault(m, {"input": 0, "output": 0,
                                      "cache_creation": 0, "cache_read": 0})
            for k in d:
                d[k] += v.get(k, 0)
        for name, n in s.get("tools", {}).items():
            tools[name] = tools.get(name, 0) + n
        sf = s.get("files", [])
        files.update(sf)
        if s.get("first") and (first is None or s["first"] < first):
            first = s["first"]
        if s.get("last") and (last is None or s["last"] > last):
            last = s["last"]
        p = projects.setdefault(s.get("project") or "(no project)",
                                {"sessions": 0, "tool_calls": 0, "tokens": 0,
                                 "user_messages": 0, "files": set()})
        p["sessions"] += s.get("sessions", 0)
        p["tool_calls"] += s.get("tool_calls", 0)
        p["tokens"] += s.get("tokens", {}).get("input", 0) + s.get("tokens", {}).get("output", 0)
        p["user_messages"] += s.get("user_messages", 0)
        p["files"].update(sf)
    upm = T["user_messages"]
    proj_list = [{"project": name, "sessions": p["sessions"],
                  "tool_calls": p["tool_calls"], "files_modified": len(p["files"]),
                  "user_messages": p["user_messages"]}
                 for name, p in projects.items()]
    proj_list.sort(key=lambda p: -p["tool_calls"])
    return {
        "totals": {
            **T, "unique_tools": len(tools),
            "tools_per_user_msg": round(T["tool_calls"] / upm, 1) if upm else 0,
            "files_modified": len(files),
        },
        "tokens": tok,
        "tokens_by_model": models,
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
        "projects": proj_list[:12],
        "first_activity": first,
        "last_activity": last,
    }


def load_cache(path):
    try:
        c = json.load(open(path))
        if isinstance(c, dict) and c.get("version") == CACHE_VERSION \
                and isinstance(c.get("sessions"), dict):
            return c["sessions"]
    except Exception:
        pass
    return {}


def collect_file_source(input_path, fmt, old):
    """Claude/Codex: one .jsonl per session, cached by file size:mtime."""
    best = {}  # key -> (size, mtime, path, plabel)
    if fmt == "claude":
        # input_path may be several roots (os.pathsep-joined): the live ~/.claude
        # dir plus any recovered-raw archive folders (Claude prunes old raw; the
        # archive keeps token data alive). os.walk (not glob) so it also covers
        # Cowork, nested under a hidden .claude/projects dir. Dedup across all
        # roots by session uuid (globally unique) — keep the largest copy — so
        # duplicate "(del respaldo)" folders don't double-count.
        for root_dir in input_path.split(os.pathsep):
            if not root_dir:
                continue
            for root, _dirs, fnames in os.walk(root_dir):
                if os.sep + "subagents" in root + os.sep:
                    continue
                for base in fnames:
                    if not base.endswith(".jsonl"):
                        continue
                    if base.startswith("agent-") or base == "audit.jsonl":
                        continue
                    f = os.path.join(root, base)
                    uuid = os.path.splitext(base)[0]
                    plabel = project_label(clean_project_dir(os.path.basename(os.path.dirname(f))))
                    try:
                        st = os.stat(f); size, mtime = st.st_size, int(st.st_mtime)
                    except OSError:
                        size, mtime = 0, 0
                    cur = best.get(uuid)
                    if cur is None or size > cur[0]:
                        best[uuid] = (size, mtime, f, plabel)
        scanner = scan_claude
    else:  # codex — several roots (os.pathsep): active + archived sessions, both
        # carrying token usage; dedup by the rollout uuid.
        for root_dir in input_path.split(os.pathsep):
            if not root_dir:
                continue
            for f in glob.glob(os.path.join(root_dir, "**", "*.jsonl"), recursive=True):
                key = "codex\t" + os.path.splitext(os.path.basename(f))[0]
                try:
                    st = os.stat(f); size, mtime = st.st_size, int(st.st_mtime)
                except OSError:
                    size, mtime = 0, 0
                if key not in best or size > best[key][0]:
                    best[key] = (size, mtime, f, None)
        scanner = scan_codex

    new_cache, sessions, hits, misses = {}, [], 0, 0
    for key, (size, mtime, f, plabel) in best.items():
        sig = "%d:%d" % (size, mtime)
        entry = old.get(key)
        if entry and entry.get("sig") == sig and isinstance(entry.get("metrics"), dict):
            metrics = entry["metrics"]; hits += 1
        else:
            try:
                metrics = scanner(f, plabel)
            except Exception:
                continue
            misses += 1
        new_cache[key] = {"sig": sig, "metrics": metrics}
        sessions.append(metrics)
    return new_cache, sessions, hits, misses


def collect_db_source(db_path, fmt, old):
    """Cursor/OpenCode: a single SQLite DB. Re-scan only when the DB changed."""
    try:
        st = os.stat(db_path); sig = "%d:%d" % (st.st_size, int(st.st_mtime))
    except OSError:
        return {}, [], 0, 0
    reuse = {k: v for k, v in old.items()
             if v.get("sig") == sig and isinstance(v.get("metrics"), dict)}
    if reuse:
        sessions = [v["metrics"] for v in reuse.values()]
        return reuse, sessions, len(reuse), 0
    scanned = (scan_opencode_db if fmt == "opencode" else scan_cursor_db)(db_path)
    new_cache, sessions = {}, []
    for m in scanned:
        key = m.pop("_key", fmt + "\t" + str(len(sessions)))
        new_cache[key] = {"sig": sig, "metrics": m}
        sessions.append(m)
    return new_cache, sessions, 0, len(sessions)


FORMAT_BY_SOURCE = {"claude-code": "claude", "cowork": "claude",
                    "codex": "codex", "codex-archived": "codex",
                    "opencode": "opencode", "cursor": "cursor"}


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    input_path, out_dir, source = sys.argv[1], sys.argv[2], sys.argv[3]
    fmt = sys.argv[4] if len(sys.argv) > 4 else FORMAT_BY_SOURCE.get(source, "claude")

    cache_path = os.path.join(out_dir, "_ledger-cache.json")
    old = load_cache(cache_path)
    if fmt in ("claude", "codex"):
        new_cache, sessions, hits, misses = collect_file_source(input_path, fmt, old)
    else:
        new_cache, sessions, hits, misses = collect_db_source(input_path, fmt, old)

    # Make the ledger cumulative + portable, like the Markdown backup itself
    # (AGENTS.md rule #3). A session is "still real" as long as its .md exists
    # here, even after the tool pruned its raw transcript. So:
    #   - carry forward every session ever measured from raw (its cached metrics,
    #     with tokens) — token data, once computed, persists in the data folder and
    #     travels with the markdowns; no dependency on the raw after the first read;
    #   - sessions whose raw was never seen fall back to the .md (counts only).
    raw_ids = {m.get("id") for m in sessions if m.get("id")}
    md_cache, md_all, md_hits, md_misses = collect_markdown(out_dir, old)
    md_ids = {m.get("id") for m in md_all if m.get("id")}
    carried = 0
    for key, entry in old.items():
        if key.startswith("md\t") or key in new_cache:
            continue  # md-fallback entry, or a raw session re-scanned this run
        m = entry.get("metrics") if isinstance(entry, dict) else None
        if not isinstance(m, dict):
            continue
        sid = m.get("id")
        if sid and sid in md_ids and sid not in raw_ids:
            sessions.append(m)        # keep its tokens
            new_cache[key] = entry    # persist (cumulative)
            raw_ids.add(sid)
            carried += 1
    md_sessions = [m for m in md_all if not (m.get("id") and m["id"] in raw_ids)]
    new_cache.update(md_cache)
    sessions = sessions + md_sessions

    # Drop empty sessions (no messages, no tools) so the ledger's session count
    # matches what the Markdown converters emit (they skip empties too).
    sessions = [m for m in sessions
                if m.get("user_messages") or m.get("assistant_messages") or m.get("tool_calls")]

    ledger = aggregate(sessions)
    ledger["generated"] = datetime.datetime.now().astimezone().isoformat()

    out_path = os.path.join(out_dir, "_ledger.json")
    doc = {}
    if os.path.exists(out_path):
        try:
            doc = json.load(open(out_path))
        except Exception:
            doc = {}
    if not isinstance(doc, dict) or "sources" not in doc:
        doc = {"sources": {}}
    doc["sources"][source] = ledger
    doc["generated"] = ledger["generated"]
    os.makedirs(out_dir, exist_ok=True)
    json.dump(doc, open(out_path, "w"), ensure_ascii=False, indent=2)
    json.dump({"version": CACHE_VERSION, "sessions": new_cache},
              open(cache_path, "w"), ensure_ascii=False)

    tt = ledger["totals"]
    md_note = f", {len(md_sessions)} from .md (raw pruned)" if md_sessions else ""
    md_note += f", {carried} carried (cumulative)" if carried else ""
    print(f"Ledger [{source}]: {tt['sessions']} sessions "
          f"({hits + md_hits} cached, {misses + md_misses} scanned{md_note}), "
          f"{tt['tool_calls']} tool calls, {tt['files_modified']} files, "
          f"{ledger['tokens']['input'] + ledger['tokens']['output']:,} base tokens")


if __name__ == "__main__":
    main()
