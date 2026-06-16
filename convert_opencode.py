#!/usr/bin/env python3
"""Convert OpenCode sessions (opencode.db SQLite) into readable Markdown.
Structure: session (title, directory) -> message (role) -> part (text/tool).
Usage: convert_opencode.py <path_to_opencode.db> <output_dir>
"""
import sqlite3, json, os, re, sys, datetime

def safe_filename(s):
    s = re.sub(r"[^\w\s-]", "", s or "").strip().replace(" ", "_")
    return s[:80] or "session"

def project_label(directory):
    if not directory:
        return "no-project"
    return os.path.basename(directory.rstrip("/")) or "no-project"

def ms_to_iso(ms):
    if not ms:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(ms)/1000, datetime.timezone.utc).isoformat()
    except Exception:
        return ""

def part_to_text(data):
    """Extract readable text from a part. Returns (kind, text) where kind is 'text'|'tool'|''."""
    try:
        d = json.loads(data) if isinstance(data, str) else data
    except Exception:
        return ("", "")
    t = d.get("type", "")
    if t == "text":
        return ("text", d.get("text", "") or "")
    if t == "tool":
        tool = d.get("tool", "tool")
        st = d.get("state", {}) or {}
        inp = st.get("input", {}) or {}
        # summarize the tool call
        target = inp.get("filePath") or inp.get("path") or inp.get("command") or ""
        if isinstance(target, list):
            target = " ".join(map(str, target))
        label = f"[tool: {tool}{(' → ' + str(target)) if target else ''}]"
        # tool output, if any
        out = st.get("output") or st.get("result") or ""
        if isinstance(out, (dict, list)):
            out = json.dumps(out, ensure_ascii=False)
        out = str(out).strip()
        if len(out) > 500:
            out = out[:500] + " …(truncated)"
        body = label + (f"\n```\n{out}\n```" if out else "")
        return ("tool", body)
    if t == "reasoning":
        return ("", "")  # internal reasoning: drop
    # other types: try text
    if "text" in d:
        return ("text", d.get("text", "") or "")
    return ("", "")

def main():
    db_path, out_dir = sys.argv[1], sys.argv[2]
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    sessions = con.execute(
        "SELECT id, title, directory, time_created FROM session ORDER BY time_created"
    ).fetchall()

    counts = {"ok": 0, "empty": 0}
    for s in sessions:
        sid = s["id"]
        title = (s["title"] or "").strip() or ("session-" + sid[:12])
        directory = s["directory"] or ""
        proj = project_label(directory)
        date_iso = ms_to_iso(s["time_created"])

        # session messages, ordered by time
        msgs = con.execute(
            "SELECT id, data, time_created FROM message WHERE session_id=? ORDER BY time_created",
            (sid,)
        ).fetchall()

        blocks = []
        for m in msgs:
            try:
                mdata = json.loads(m["data"])
            except Exception:
                mdata = {}
            role = mdata.get("role", "")
            if role not in ("user", "assistant"):
                continue
            # message parts, ordered
            parts = con.execute(
                "SELECT data, time_created FROM part WHERE message_id=? ORDER BY time_created",
                (m["id"],)
            ).fetchall()
            seg_text = []
            for p in parts:
                typ, txt = part_to_text(p["data"])
                if txt and txt.strip():
                    seg_text.append(txt)
            if not seg_text:
                continue
            label = "You" if role == "user" else "OpenCode"
            blocks.append(f"### {label}\n\n" + "\n\n".join(seg_text) + "\n")

        if not blocks:
            counts["empty"] += 1
            continue

        pdir = os.path.join(out_dir, proj)
        os.makedirs(pdir, exist_ok=True)
        prefix = (date_iso or "0000-00-00")[:10]
        fname = f"{prefix}__{safe_filename(title)}__{sid}.md"
        with open(os.path.join(pdir, fname), "w") as o:
            o.write(f"# {title}\n\n")
            o.write(f"<!-- date: {date_iso} | id: {sid} | project: {proj} | source: opencode -->\n\n")
            o.write("\n".join(blocks))
        counts["ok"] += 1

    con.close()
    print(f"Converted: {counts['ok']}")
    print(f"Empty: {counts['empty']}")

    # backup stamp
    try:
        info_path = os.path.join(out_dir, "_backup-info.json")
        info = {}
        if os.path.exists(info_path):
            try: info = json.load(open(info_path))
            except Exception: info = {}
        info["opencode"] = {"generated": datetime.datetime.now().astimezone().isoformat(), "conversations": counts["ok"]}
        os.makedirs(out_dir, exist_ok=True)
        json.dump(info, open(info_path, "w"), ensure_ascii=False, indent=2)
    except Exception:
        pass

if __name__ == "__main__":
    main()
