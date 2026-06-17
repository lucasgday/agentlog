#!/usr/bin/env python3
"""Evidence Ledger — deterministic, $0, on-device metrics from Claude Code .jsonl.

Reads the raw transcripts (the SAME source convert_claude reads) and writes a
sidecar `_ledger.json` the viewer renders. No LLM, no network: every number is a
parse-and-sum over the .jsonl (token usage, tool calls, test/build runs, files
modified, errors). This is the local answer to a cloud "evidence ledger" — the
counters never leave the machine.

Usage: extract_ledger.py <projects_dir> <output_dir> [source]
  <projects_dir>  e.g. ~/.claude/projects  (folders of *.jsonl)
  <output_dir>    where markdown-*/ live; _ledger.json is written/merged here
  [source]        ledger key (default: claude-code)

Per-source results are merged into _ledger.json (like _backup-info.json), so
other Claude-format sources (e.g. cowork) can call this independently.

INCREMENTAL: per-session metrics are cached in `_ledger-cache.json` keyed by
project+session and validated by the file's size:mtime (same idea as the backup's
change detection). Each run only re-scans sessions whose .jsonl changed; the rest
are reused from cache, then everything is re-aggregated. Sessions that vanished
from the source are dropped from the cache so totals stay honest.
"""
import json, os, re, sys, glob, datetime

# Reuse the dedup/project logic so the ledger counts sessions the same way the
# Markdown converter does (one file per session; restored-backup folders unified).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from convert_claude import project_label
except Exception:
    def project_label(folder):
        parts = [p for p in folder.split("-") if p]
        return parts[-1] if parts else folder

CACHE_VERSION = 1

TEST_RE = re.compile(
    r"\b(pytest|jest|vitest|go test|cargo test|npm (?:run )?test|"
    r"yarn test|py_compile|bash -n|rspec|phpunit|mvn test|gradle test)\b")
BUILD_RE = re.compile(
    r"\b(make|tsc|webpack|vite build|npm run build|yarn build|"
    r"cargo build|go build|mvn package|gradle build|docker build)\b")

EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def add_tokens(dst, usage):
    dst["input"] += usage.get("input_tokens", 0) or 0
    dst["output"] += usage.get("output_tokens", 0) or 0
    dst["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
    dst["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0


def scan_session(path, plabel):
    """Parse one session's .jsonl into a self-contained per-session metrics dict.
    Sets (files, tools, models) are kept explicit so they can be unioned/merged
    when aggregating — and so the result is JSON-serializable for the cache."""
    s = {
        "project": plabel, "sessions": 1,
        "user_messages": 0, "assistant_messages": 0,
        "tool_calls": 0, "mcp_tool_calls": 0,
        "test_runs": 0, "build_runs": 0, "errors": 0, "web_searches": 0,
        "tokens": {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0},
        "models": {}, "tools": {}, "files": [],
        "first": None, "last": None,
    }
    files = set()

    for line in open(path, errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue

        ts = d.get("timestamp")
        if ts:
            if s["first"] is None or ts < s["first"]:
                s["first"] = ts
            if s["last"] is None or ts > s["last"]:
                s["last"] = ts

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
            add_tokens(s["tokens"], usage)
            mm = s["models"].setdefault(
                model, {"input": 0, "output": 0,
                        "cache_creation": 0, "cache_read": 0})
            add_tokens(mm, usage)

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
                    name = b.get("name", "tool")
                    s["tool_calls"] += 1
                    s["tools"][name] = s["tools"].get(name, 0) + 1
                    if name.startswith("mcp__"):
                        s["mcp_tool_calls"] += 1
                    if name in ("WebSearch", "WebFetch"):
                        s["web_searches"] += 1
                    inp = b.get("input") or {}
                    if name in EDIT_TOOLS:
                        fp = inp.get("file_path") or inp.get("path")
                        if fp:
                            files.add(fp)
                    if name == "Bash":
                        cmd = inp.get("command") or ""
                        if TEST_RE.search(cmd):
                            s["test_runs"] += 1
                        if BUILD_RE.search(cmd):
                            s["build_runs"] += 1

        if role == "user" and has_text and not has_tool_result:
            s["user_messages"] += 1
        elif role == "assistant" and (has_text or usage) and model != "<synthetic>":
            s["assistant_messages"] += 1

    s["files"] = sorted(files)
    return s


SCALAR_KEYS = ("sessions", "user_messages", "assistant_messages", "tool_calls",
               "mcp_tool_calls", "test_runs", "build_runs", "errors", "web_searches")


def aggregate(sessions):
    """Reduce per-session dicts into the ledger the viewer renders."""
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


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    projects_dir, out_dir = sys.argv[1], sys.argv[2]
    source = sys.argv[3] if len(sys.argv) > 3 else "claude-code"

    # One file per (normalized project, session uuid); on collisions keep the
    # largest (most complete) — same as convert_claude.
    best = {}  # key -> (size, mtime, path, plabel)
    for proj_dir in sorted(glob.glob(os.path.join(projects_dir, "*"))):
        if not os.path.isdir(proj_dir):
            continue
        plabel = project_label(os.path.basename(proj_dir))
        for f in glob.glob(os.path.join(proj_dir, "*.jsonl")):
            base = os.path.basename(f)
            if base.startswith("agent-") or "/subagents/" in f:
                continue
            uuid = os.path.splitext(base)[0]
            try:
                st = os.stat(f)
                size, mtime = st.st_size, int(st.st_mtime)
            except OSError:
                size, mtime = 0, 0
            k = plabel + "\t" + uuid
            if k not in best or size > best[k][0]:
                best[k] = (size, mtime, f, plabel)

    cache_path = os.path.join(out_dir, "_ledger-cache.json")
    old = load_cache(cache_path)
    new_cache = {}
    sessions = []
    hits = misses = 0
    for k, (size, mtime, f, plabel) in best.items():
        sig = "%d:%d" % (size, mtime)
        entry = old.get(k)
        if entry and entry.get("sig") == sig and isinstance(entry.get("metrics"), dict):
            metrics = entry["metrics"]
            hits += 1
        else:
            try:
                metrics = scan_session(f, plabel)
            except Exception:
                continue
            misses += 1
        new_cache[k] = {"sig": sig, "metrics": metrics}
        sessions.append(metrics)

    ledger = aggregate(sessions)
    ledger["generated"] = datetime.datetime.now().astimezone().isoformat()

    # Merge per-source into _ledger.json (same pattern as _backup-info.json).
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

    # Persist the (pruned) per-session cache for next run.
    json.dump({"version": CACHE_VERSION, "sessions": new_cache},
              open(cache_path, "w"), ensure_ascii=False)

    tt = ledger["totals"]
    print(f"Ledger [{source}]: {tt['sessions']} sessions "
          f"({hits} cached, {misses} scanned), {tt['tool_calls']} tool calls, "
          f"{tt['files_modified']} files, "
          f"{ledger['tokens']['input'] + ledger['tokens']['output']:,} base tokens")


if __name__ == "__main__":
    main()
