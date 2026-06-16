#!/usr/bin/env python3
"""Convert Cursor conversations (globalStorage/state.vscdb) into Markdown.
Structure: ItemTable['composer.composerHeaders'] = conversation index (composers).
           cursorDiskKV['composerData:<id>'] = composer data (bubble order).
           cursorDiskKV['bubbleId:<composerId>:<bubbleId>'] = each message (type 1=user, 2=assistant).
Usage: convert_cursor.py <path_to_state.vscdb> <output_dir>
"""
import sqlite3, json, os, re, sys, datetime
from collections import defaultdict

def safe_filename(s):
    s = re.sub(r"[^\w\s-]", "", s or "").strip().replace(" ", "_")
    return s[:80] or "session"

def ms_to_iso(ms):
    if not ms: return ""
    try: return datetime.datetime.fromtimestamp(int(ms)/1000, datetime.timezone.utc).isoformat()
    except Exception: return ""

def get_json(con, table, key):
    row = con.execute(f"SELECT value FROM {table} WHERE key=?", (key,)).fetchone()
    if not row: return None
    try: return json.loads(row[0])
    except Exception: return None

def bubble_to_text(b):
    """Return (role, text) for a bubble. type 1=user, 2=assistant."""
    typ = b.get("type")
    role = "user" if typ == 1 else "assistant" if typ == 2 else None
    if role is None: return (None, "")
    segs = []
    txt = (b.get("text") or "").strip()
    if txt: segs.append(txt)
    # tools
    for tr in (b.get("toolResults") or []):
        name = tr.get("name") or "tool"
        args = tr.get("args") or {}
        target = args.get("path") or args.get("filePath") or args.get("command") or ""
        segs.append(f"[tool: {name}{(' → ' + str(target)) if target else ''}]")
    for sb in (b.get("suggestedCodeBlocks") or []):
        f = sb.get("uri") or sb.get("path") or ""
        if f: segs.append(f"[suggested code: {f}]")
    return (role, "\n\n".join(segs))

def main():
    db_path, out_dir = sys.argv[1], sys.argv[2]
    con = sqlite3.connect(db_path)

    headers = get_json(con, "ItemTable", "composer.composerHeaders") or {}
    comps = headers.get("allComposers", [])

    # archived/date index per composerId
    meta = {}
    for c in comps:
        cid = c.get("composerId")
        if cid:
            meta[cid] = {"createdAt": c.get("createdAt"), "archived": bool(c.get("isArchived"))}

    # group every bubble by composerId (from the bubbleId:<cid>:<bid> key)
    bubbles_by_comp = defaultdict(dict)  # cid -> { bubbleId: data }
    for key, val in con.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"):
        parts = key.split(":")
        if len(parts) < 3: continue
        cid, bid = parts[1], parts[2]
        try: bubbles_by_comp[cid][bid] = json.loads(val)
        except Exception: pass

    counts = {"ok": 0, "empty": 0}
    # walk known composers (from the header) + any with bubbles not in the header
    all_cids = set(meta.keys()) | set(bubbles_by_comp.keys())
    for cid in all_cids:
        cdata = get_json(con, "cursorDiskKV", f"composerData:{cid}") or {}
        # bubble order: prefer fullConversationHeadersOnly; otherwise whatever exists
        order = [h.get("bubbleId") for h in cdata.get("fullConversationHeadersOnly", []) if h.get("bubbleId")]
        bubs = bubbles_by_comp.get(cid, {})
        if not order:
            order = list(bubs.keys())  # fallback: no explicit order

        blocks = []
        title = ""
        for bid in order:
            b = bubs.get(bid)
            if not b: continue
            role, txt = bubble_to_text(b)
            if not role or not txt.strip(): continue
            if role == "user" and not title:
                title = txt.strip().split("\n")[0][:80]
            label = "You" if role == "user" else "Cursor"
            blocks.append(f"### {label}\n\n{txt.strip()}\n")

        if not blocks:
            counts["empty"] += 1
            continue

        m = meta.get(cid, {})
        date_iso = ms_to_iso(m.get("createdAt") or cdata.get("createdAt"))
        archived = m.get("archived", False)
        title = title or ("session-" + cid[:12])

        proj = "cursor"   # Cursor doesn't tie a clear project per composer; group under 'cursor'
        pdir = os.path.join(out_dir, proj)
        os.makedirs(pdir, exist_ok=True)
        prefix = (date_iso or "0000-00-00")[:10]
        fname = f"{prefix}__{safe_filename(title)}__{cid}.md"
        with open(os.path.join(pdir, fname), "w") as o:
            o.write(f"# {title}\n\n")
            o.write(f"<!-- date: {date_iso} | id: {cid} | project: {proj} | source: cursor | archived: {str(archived).lower()} -->\n\n")
            o.write("\n".join(blocks))
        counts["ok"] += 1

    con.close()
    print(f"Converted: {counts['ok']}")
    print(f"Empty: {counts['empty']}")

    try:
        info_path = os.path.join(out_dir, "_backup-info.json")
        info = {}
        if os.path.exists(info_path):
            try: info = json.load(open(info_path))
            except Exception: info = {}
        info["cursor"] = {"generated": datetime.datetime.now().astimezone().isoformat(), "conversations": counts["ok"]}
        os.makedirs(out_dir, exist_ok=True)
        json.dump(info, open(info_path, "w"), ensure_ascii=False, indent=2)
    except Exception:
        pass

if __name__ == "__main__":
    main()
