#!/usr/bin/env python3
"""Convert Claude Code sessions (.jsonl) into readable Markdown.
Title: uses ai-title if present; otherwise derives one from the first user message.
Date: from the first timestamp. Project: from the folder name.
Usage: convert_claude.py <conversations_dir> <output_dir> [source] [history.jsonl]
"""
import json, os, re, sys, glob

def fmt_tool_use(c):
    name = c.get("name", "tool")
    inp = c.get("input", {}) or {}
    if name in ("Edit", "Write", "Read", "NotebookEdit"):
        return f"[tool: {name} → {inp.get('file_path') or inp.get('path','')}]"
    if name == "Bash":
        return f"[tool: Bash]\n```bash\n{(inp.get('command') or '').strip()}\n```"
    if name in ("Grep", "Glob"):
        return f"[tool: {name} → {inp.get('pattern','')}]"
    if name in ("WebSearch", "WebFetch"):
        return f"[tool: {name} → {inp.get('query') or inp.get('url','')}]"
    blob = json.dumps(inp, ensure_ascii=False)
    if len(blob) > 300:
        blob = blob[:300] + " …"
    return f"[tool: {name} {blob}]"

def fmt_tool_result(c):
    content = c.get("content", "")
    if isinstance(content, list):
        content = "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
    content = str(content).strip()
    if len(content) > 500:
        content = content[:500] + " …(truncated)"
    return f"[result]\n```\n{content}\n```" if content else ""

def render(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for c in content:
            if not isinstance(c, dict):
                if isinstance(c, str):
                    out.append(c)
                continue
            t = c.get("type")
            if t == "text":
                out.append(c.get("text", ""))
            elif t == "tool_use":
                out.append(fmt_tool_use(c))
            elif t == "tool_result":
                r = fmt_tool_result(c)
                if r:
                    out.append(r)
            elif "text" in c:
                out.append(c["text"])
        return "\n\n".join(p for p in out if p and str(p).strip())
    return ""

def first_user_text(content):
    """Used to derive a title from the first user message."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("text", None) and c.get("text"):
                return c["text"]
            if isinstance(c, str):
                return c
    return ""

def clean_title(s):
    """Clean a string for use as a title: drop internal commands/noise, cut on a word."""
    if not s:
        return None
    s = s.strip()
    # drop prompts that are just internal commands
    if s.startswith("<command-name>") or s.startswith("<") and ">" in s[:30] and len(s) < 40:
        return None
    s = s.replace("\n", " ").strip()
    if not s:
        return None
    if len(s) > 70:
        cut = s[:70].rsplit(" ", 1)[0]
        s = (cut or s[:70]) + "…"
    return s

def parse_session(path, history=None, source="claude-code"):
    title = None
    first_user = None
    first_ts = None
    cwd = None
    sid = None
    blocks = []

    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        if first_ts is None and d.get("timestamp"):
            first_ts = d["timestamp"]
        if cwd is None and d.get("cwd"):
            cwd = d["cwd"]
        if sid is None and d.get("sessionId"):
            sid = d["sessionId"]

        if t == "ai-title":
            title = d.get("aiTitle")
        elif t in ("user", "assistant"):
            msg = d.get("message", {})
            role = msg.get("role", t)
            content = msg.get("content", "")
            if role == "user" and first_user is None:
                first_user = first_user_text(content)
            txt = render(content)
            if txt.strip():
                label = "You" if role == "user" else "Claude"
                blocks.append(f"### {label}\n\n{txt}\n")

    # title priority: ai-title > history.jsonl (real first prompt) > first message > uuid
    if not title and history and sid and sid in history:
        title = clean_title(history[sid])
    if not title and first_user:
        title = clean_title(first_user)
    if not title:
        title = "session-" + os.path.splitext(os.path.basename(path))[0][:20]

    project = os.path.basename(cwd.rstrip("/")) if cwd else ""
    return {
        "title": title,
        "date": (first_ts or "")[:10],
        "datetime": first_ts or "",
        "project": project,
        "source": source,
        "blocks": blocks,
    }

def safe_filename(s):
    s = re.sub(r"[^\w\s-]", "", s).strip().replace(" ", "_")
    return s[:80] or "session"

def project_label(folder):
    # The path looks like "-Users-<user>-<...>-<projectName>", so the last
    # segment is the project name (agnostic to the user/folder structure).
    parts = [p for p in folder.split("-") if p]
    label = parts[-1] if parts else folder
    return normalize_project(label)

def normalize_project(label):
    """Strip suffixes that Finder adds when restoring from snapshots:
    ' (del respaldo)', ' (del respaldo 2)', ' 2', ' copia', etc.
    This unifies variants of the same project."""
    s = label
    s = re.sub(r"\s*\(del respaldo[^)]*\)", "", s)
    s = re.sub(r"\s*\(copia[^)]*\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+\d+$", "", s)   # trailing ' 2'
    return s.strip() or label

def load_history(path):
    """Map sessionId -> first user prompt from history.jsonl."""
    hist = {}
    if not path or not os.path.exists(path):
        return hist
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        sid = d.get("sessionId")
        disp = d.get("display")
        if sid and disp and sid not in hist:
            hist[sid] = disp
    return hist

def main():
    # usage: convert_claude.py <conversations> <output> [source] [history.jsonl]
    src, out_dir = sys.argv[1], sys.argv[2]
    source = sys.argv[3] if len(sys.argv) > 3 else "claude-code"
    hist_path = sys.argv[4] if len(sys.argv) > 4 else os.path.expanduser("~/.claude/history.jsonl")
    history = load_history(hist_path)
    counts = {"ok": 0, "empty": 0, "dups": 0}

    # 1) Collect every .jsonl grouped by (normalized project, session uuid).
    #    The uuid is the filename without extension. If the same session shows up
    #    in several folders (e.g. "my-project" and "my-project (del respaldo)"),
    #    keep the largest file (the most complete one).
    best = {}  # (plabel, uuid) -> (size, filepath)
    for proj_dir in sorted(glob.glob(os.path.join(src, "*"))):
        if not os.path.isdir(proj_dir):
            continue
        plabel = project_label(os.path.basename(proj_dir))
        for f in glob.glob(os.path.join(proj_dir, "*.jsonl")):
            uuid = os.path.splitext(os.path.basename(f))[0]
            try:
                size = os.path.getsize(f)
            except OSError:
                size = 0
            k = (plabel, uuid)
            if k not in best or size > best[k][0]:
                if k in best:
                    counts["dups"] += 1
                best[k] = (size, f)
            else:
                counts["dups"] += 1

    # 2) Convert one file per unique session.
    for (plabel, uuid), (size, f) in best.items():
        s = parse_session(f, history=history, source=source)
        if not s["blocks"]:
            counts["empty"] += 1
            continue
        proj = plabel or s["project"] or "no-project"
        pdir = os.path.join(out_dir, proj)
        os.makedirs(pdir, exist_ok=True)
        base = safe_filename(s["title"])
        prefix = s["date"] or "0000-00-00"
        fname = f"{prefix}__{base}__{uuid}.md"   # full uuid = no collisions
        with open(os.path.join(pdir, fname), "w") as o:
            o.write(f"# {s['title']}\n\n")
            o.write(f"<!-- date: {s['datetime']} | id: {uuid} | project: {proj} | source: {source} -->\n\n")
            o.write("\n".join(s["blocks"]))
        counts["ok"] += 1

    print(f"Converted: {counts['ok']}")
    print(f"Empty: {counts['empty']}")
    if counts["dups"]:
        print(f"Duplicates skipped (same session in several folders): {counts['dups']}")
    if history:
        print(f"(history.jsonl supplied {len(history)} prompts for titles)")

    # backup stamp: timestamp of this run per source
    try:
        import datetime
        info_path = os.path.join(out_dir, "_backup-info.json")
        info = {}
        if os.path.exists(info_path):
            try: info = json.load(open(info_path))
            except Exception: info = {}
        info[source] = {"generated": datetime.datetime.now().astimezone().isoformat(), "conversations": counts["ok"]}
        os.makedirs(out_dir, exist_ok=True)
        json.dump(info, open(info_path, "w"), ensure_ascii=False, indent=2)
    except Exception:
        pass

if __name__ == "__main__":
    main()
