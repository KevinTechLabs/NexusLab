"""
NexusLab Brain: long-term memory for your local LLMs, kept as a plain
Obsidian vault of Markdown notes.

- Notes live in ``Memory/``; maps of content (one per topic) in ``MOCs/``.
- Models never write to the vault directly. ``propose()`` puts a draft in
  ``.nexus/inbox/`` and it only becomes a note when someone approves it on
  the Brain tab.
- ``recall()`` reads maps of content first, follows their links, falls back to
  a keyword search over every note, then fits the best notes into a
  character budget so a small model's context window isn't flooded.

The vault is ordinary Markdown, so it can be opened in Obsidian as-is.
"""
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

NOTES_DIR = "Memory"
MOCS_DIR = "MOCs"
INBOX_DIR = ".nexus/inbox"
SKIP_DIRS = {".obsidian", ".nexus", ".trash", ".git"}
UNSORTED = "Unsorted"

MAX_TITLE = 120
MAX_CONTENT = 8000
MAX_TOPIC = 60
MAX_INBOX = 200
MAX_LINKS = 8

LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
STOP = set("""a an and are as at be but by can could did do does doing for from get had has have how i if in into is it its
just like make me my need of on or our should so that the their them then there these they this to use using was way
we were what when where which who why will with would you your about best good want""".split())


def _stem(t):
    if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    return t


def tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9-]*", str(text).lower())
    out = []
    for w in words:
        if len(w) < 3 or w in STOP:
            continue
        w = _stem(w)
        if w not in out:
            out.append(w)
    return out


def hits(text, toks):
    low = text.lower()
    return sum(1 for t in toks if t in low)


def clean_title(title, limit=MAX_TITLE):
    """A title that is safe as a file name and still readable."""
    t = BAD_CHARS.sub(" ", str(title or ""))
    t = re.sub(r"\s+", " ", t).strip().lstrip(". ")
    return t[:limit].strip()


def split_front(text):
    m = FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


class Brain:
    def __init__(self, vault, recall_chars=8000, tz=None, owner=""):
        self.vault = Path(vault).resolve()
        self.recall_chars = max(1000, int(recall_chars))
        self.tz = tz
        self.owner = owner or "the owner"
        self.activity = []  # newest last: {"t", "kind", "text", "notes"}
        self._cache = {}    # path -> (mtime_ns, parsed note)
        self.ensure()

    # ------------------------------------------------------------ files
    def ensure(self):
        for d in (NOTES_DIR, MOCS_DIR, INBOX_DIR):
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        readme = self.vault / "Start here.md"
        if not readme.exists():
            readme.write_text(
                "# Start here\n\n"
                "This vault is the long-term memory of your NexusLab LLMs.\n\n"
                f"- `{NOTES_DIR}/` holds one note per idea, fact or decision.\n"
                f"- `{MOCS_DIR}/` holds a map of content for each topic, linking its notes.\n"
                "- Models propose new memories; they appear on NexusLab's Brain tab and are only saved here once approved.\n\n"
                "You can open this folder in Obsidian and edit anything by hand.\n")

    def _inside(self, path):
        p = Path(path).resolve()
        if p != self.vault and self.vault not in p.parents:
            raise ValueError("Path is outside the vault")
        return p

    def _now(self):
        return datetime.now(self.tz) if self.tz else datetime.now().astimezone()

    def _parse(self, path):
        st = path.stat()
        cached = self._cache.get(path)
        if cached and cached[0] == st.st_mtime_ns:
            return cached[1]
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = split_front(text)
        body = re.sub(r"\A\s*#\s+" + re.escape(path.stem) + r"[ \t]*\n", "", body)  # the title is shown separately
        rel = path.relative_to(self.vault).as_posix()
        is_moc = rel.startswith(MOCS_DIR + "/") or "moc" in meta.get("tags", "").lower()
        title = path.stem
        if is_moc and title.lower().endswith(" moc"):
            topic = title[:-4].strip()
        else:
            topic = title
        clean = re.sub(r"^#+\s*", "", body, flags=re.M).strip()
        note = {
            "id": rel, "path": rel, "title": title, "topic": topic, "is_moc": is_moc,
            "body": body.strip(), "chars": len(body.strip()),
            "snippet": re.sub(r"\s+", " ", LINK.sub(lambda m: m.group(1), clean))[:220],
            "links": list(dict.fromkeys(m.group(1).strip() for m in LINK.finditer(body))),
            "meta": meta, "mtime": st.st_mtime,
        }
        self._cache[path] = (st.st_mtime_ns, note)
        return note

    def notes(self):
        out = []
        for p in sorted(self.vault.rglob("*.md")):
            rel = p.relative_to(self.vault)
            if any(part in SKIP_DIRS for part in rel.parts[:-1]) or rel.name == "Start here.md":
                continue
            try:
                out.append(self._parse(p))
            except OSError:
                continue
        seen = {n["path"] for n in out}
        self._cache = {k: v for k, v in self._cache.items() if v[1]["path"] in seen}
        return out

    # ------------------------------------------------------------ graph
    def graph(self):
        notes = self.notes()
        by_title = {n["title"].lower(): n for n in notes}
        mocs = [n for n in notes if n["is_moc"]]
        home = {}
        for m in mocs:
            for link in m["links"]:
                t = by_title.get(link.lower())
                if t and not t["is_moc"]:
                    home.setdefault(t["path"], m["path"])
        links = set()
        for n in notes:
            for link in n["links"]:
                t = by_title.get(link.lower())
                if t and t["path"] != n["path"]:
                    links.add(tuple(sorted((n["path"], t["path"]))))
        nodes = [{"id": n["path"], "title": n["title"], "topic": n["topic"], "moc": n["is_moc"],
                  "home": n["path"] if n["is_moc"] else home.get(n["path"]),
                  "chars": n["chars"], "snippet": n["snippet"], "mtime": n["mtime"]} for n in notes]
        return {"vault": str(self.vault), "nodes": nodes, "links": [list(x) for x in sorted(links)],
                "topics": [m["topic"] for m in mocs]}

    def read(self, ref):
        """Full note by vault path or title (case-insensitive)."""
        ref = str(ref or "").strip()
        notes = self.notes()
        n = next((x for x in notes if x["path"] == ref), None) \
            or next((x for x in notes if x["title"].lower() == ref.lower()), None) \
            or next((x for x in notes if x["title"].lower() == clean_title(ref).lower()), None)
        return n

    # ------------------------------------------------------------ recall
    def recall(self, query, budget=None, source="ui"):
        budget = int(budget or self.recall_chars)
        toks = tokenize(query)
        notes = self.notes()
        by_title = {n["title"].lower(): n for n in notes}
        mocs = [n for n in notes if n["is_moc"]]
        matched_mocs, via_moc = [], set()
        for m in mocs:
            kids = [by_title[t.lower()] for t in m["links"] if t.lower() in by_title]
            hay = m["title"] + " " + m["body"] + " " + " ".join(k["title"] for k in kids)
            if toks and hits(hay, toks):
                matched_mocs.append(m)
                via_moc.update(k["path"] for k in kids if not k["is_moc"])
        scored = []
        for n in notes:
            if n["is_moc"] or not toks:
                continue
            s = hits(n["title"], toks) * 3 + hits(" ".join(n["links"]), toks) * 2 + hits(n["body"], toks)
            if s:
                via = "moc" if n["path"] in via_moc else "search"
                scored.append({"note": n, "score": s + (1 if via == "moc" else 0), "via": via})
        scored.sort(key=lambda x: (-x["score"], -x["note"]["mtime"]))
        used, results = 0, []
        for x in scored:
            n = x["note"]
            fits = used + n["chars"] <= budget
            if fits:
                used += n["chars"]
            results.append({"path": n["path"], "title": n["title"], "via": x["via"], "chars": n["chars"],
                            "in_context": fits, "snippet": n["snippet"], "body": n["body"] if fits else None})
        out = {"query": query, "keywords": toks, "mocs": [m["topic"] for m in matched_mocs],
               "used": used, "budget": budget, "results": results}
        self.log("recall", f'{"LLM" if source == "mcp" else "You"} recalled "{str(query)[:80]}"',
                 [r["path"] for r in results if r["in_context"]], source)
        return out

    def log(self, kind, text, notes=(), source="ui"):
        self.activity.append({"t": time.time(), "kind": kind, "text": text, "notes": list(notes)[:20], "source": source})
        del self.activity[:-50]

    # ------------------------------------------------------------ inbox
    def _inbox(self):
        return self.vault / INBOX_DIR

    def inbox(self):
        items = []
        for p in sorted(self._inbox().glob("*.json")):
            try:
                items.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                continue
        items.sort(key=lambda x: x.get("created", 0))
        existing = {n["title"].lower() for n in self.notes() if not n["is_moc"]}
        for it in items:
            it["updates_existing"] = it["title"].lower() in existing
        return items

    def propose(self, title, content, topic="", links=(), source="LLM"):
        title = clean_title(title)
        content = str(content or "").strip()
        if not title:
            raise ValueError("A memory needs a short title")
        if not content:
            raise ValueError("A memory needs some content")
        if len(content) > MAX_CONTENT:
            raise ValueError(f"Keep a memory under {MAX_CONTENT} characters; split bigger ones into several")
        if self.inbox_count() >= MAX_INBOX:
            raise ValueError("The review queue is full. Ask the owner to approve or discard drafts on the Brain tab")
        item = {
            "id": secrets.token_hex(6), "created": time.time(), "title": title, "content": content,
            "topic": clean_title(topic, MAX_TOPIC) or "",
            "links": [clean_title(x) for x in list(links or [])[:MAX_LINKS] if clean_title(x)],
            "source": str(source or "LLM")[:80],
        }
        path = self._inbox() / f"{item['id']}.json"
        path.write_text(json.dumps(item, indent=1))
        self.log("propose", f'{item["source"]} proposed "{title}"', (), "mcp")
        return item

    def _inbox_item(self, item_id):
        if not re.fullmatch(r"[0-9a-f]{12}", str(item_id)):
            raise KeyError(item_id)
        path = self._inbox() / f"{item_id}.json"
        if not path.exists():
            raise KeyError(item_id)
        return path, json.loads(path.read_text())

    def discard(self, item_id):
        path, item = self._inbox_item(item_id)
        path.unlink()
        self.log("discard", f'Discarded "{item["title"]}"')
        return item

    def approve(self, item_id, edits=None):
        path, item = self._inbox_item(item_id)
        edits = edits or {}
        for k in ("title", "content", "topic"):
            if edits.get(k) is not None:
                item[k] = edits[k]
        if edits.get("links") is not None:
            item["links"] = list(edits["links"])
        note = self.write(item["title"], item["content"], item.get("topic"), item.get("links"), item.get("source"))
        path.unlink()
        return note

    # ------------------------------------------------------------ writing
    def write(self, title, content, topic="", links=(), source="You"):
        """Save a note (or add to an existing one) and file it under its topic's map of content."""
        title = clean_title(title)
        content = str(content or "").strip()
        if not title or not content:
            raise ValueError("A note needs a title and content")
        if len(content) > MAX_CONTENT:
            raise ValueError(f"Keep a note under {MAX_CONTENT} characters")
        topic = clean_title(topic, MAX_TOPIC) or UNSORTED
        links = [clean_title(x) for x in list(links or [])[:MAX_LINKS] if clean_title(x) and clean_title(x) != title]
        now = self._now()
        existing = self.read(title)
        if existing and not existing["is_moc"]:
            p = self._inside(self.vault / existing["path"])
            add = f"\n\n## {now:%Y-%m-%d}\n\n{content}\n"
            new_links = [x for x in links if x.lower() not in {y.lower() for y in existing["links"]}]
            if new_links:
                add += "\nRelated: " + ", ".join(f"[[{x}]]" for x in new_links) + "\n"
            with p.open("a", encoding="utf-8") as f:
                f.write(add)
            self.log("save", f'Added to "{title}"', [existing["path"]])
            self.file_under(topic, title)
            return self.read(existing["path"])
        p = self._inside(self.vault / NOTES_DIR / f"{title}.md")
        src = re.sub(r"[\r\n]+", " ", str(source or "You"))[:80]
        text = (f"---\ncreated: {now:%Y-%m-%dT%H:%M}\nsource: {src}\ntopic: {topic}\ntags: memory\n---\n\n"
                f"# {title}\n\n{content}\n")
        if links:
            text += "\nRelated: " + ", ".join(f"[[{x}]]" for x in links) + "\n"
        p.write_text(text, encoding="utf-8")
        self.file_under(topic, title)
        note = self.read(p.relative_to(self.vault).as_posix())
        self.log("save", f'Saved "{title}" under {topic}', [note["path"]])
        return note

    def file_under(self, topic, title):
        moc = self._inside(self.vault / MOCS_DIR / f"{topic} MOC.md")
        if not moc.exists():
            moc.write_text(f"---\ntags: moc\n---\n\n# {topic}\n\nMap of content for {topic}.\n\n", encoding="utf-8")
        body = moc.read_text(encoding="utf-8")
        if f"[[{title}]]".lower() not in body.lower():
            with moc.open("a", encoding="utf-8") as f:
                f.write(("" if body.endswith("\n") else "\n") + f"- [[{title}]]\n")

    # ------------------------------------------------------------ summary
    def inbox_count(self):
        return sum(1 for _ in self._inbox().glob("*.json"))

    def stats(self):
        notes = self.notes()
        return {"notes": sum(1 for n in notes if not n["is_moc"]), "topics": sum(1 for n in notes if n["is_moc"]),
                "inbox": self.inbox_count()}

    def topics_text(self):
        notes = self.notes()
        by_title = {n["title"].lower(): n for n in notes}
        mocs = [n for n in notes if n["is_moc"]]
        if not mocs:
            return "The memory vault is empty so far. Nothing has been saved yet."
        lines = [f"Memory topics ({sum(1 for n in notes if not n['is_moc'])} notes):"]
        for m in mocs:
            kids = [by_title[t.lower()]["title"] for t in m["links"] if t.lower() in by_title]
            lines.append(f"- {m['topic']} ({len(kids)}): " + ", ".join(kids[:15]) + (" …" if len(kids) > 15 else ""))
        return "\n".join(lines)

    def recall_text(self, query, budget=None):
        r = self.recall(query, budget, source="mcp")
        got = [x for x in r["results"] if x["in_context"]]
        if not got:
            return (f'No saved memories match "{query}". Answer from what you know, and if you learn something '
                    f"worth keeping, call save_memory.")
        parts = [f'{len(got)} memories match "{query}" (most relevant first):']
        for x in got:
            parts.append(f"\n### {x['title']}\n{x['body']}")
        skipped = [x["title"] for x in r["results"] if not x["in_context"]]
        if skipped:
            parts.append("\nAlso related but left out to save space (use read_memory): " + ", ".join(skipped[:10]))
        return "\n".join(parts)


# ---------------------------------------------------------------- MCP (Streamable HTTP, JSON responses)
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "search_memory",
        "description": ("Search {owner}'s long-term memory (an Obsidian vault) for notes about a topic. "
                        "Call this before answering whenever the question may depend on earlier conversations, "
                        "{owner}'s preferences, setup, projects or decisions. Use a few keywords."),
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A few keywords, e.g. 'docker backup schedule'"}},
            "required": ["query"]},
    },
    {
        "name": "read_memory",
        "description": "Read one memory note in full by its title (titles come from search_memory or list_memory_topics).",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "list_memory_topics",
        "description": "List the topics in long-term memory and the note titles under each one.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_memory",
        "description": ("Propose a new long-term memory: one fact, preference, decision or lesson worth keeping for "
                        "future conversations. It is queued for {owner} to approve; it is not saved until then. "
                        "Keep it to one idea, written so it makes sense on its own later. Don't save small talk, "
                        "secrets, passwords or anything {owner} asked you to forget."),
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Short title naming the one idea, e.g. 'Nightly backups run at 2am'"},
            "content": {"type": "string", "description": "The memory itself, a few sentences of Markdown"},
            "topic": {"type": "string", "description": "Broad topic to file it under, e.g. 'Homelab', 'Coding', 'Preferences'"},
            "related": {"type": "array", "items": {"type": "string"}, "description": "Titles of existing notes it relates to"},
        }, "required": ["title", "content"]},
    },
]

INSTRUCTIONS = ("Long-term memory for {owner}. Use search_memory to recall what you've learned before; use "
                "save_memory to propose something worth remembering. Saved memories wait for {owner}'s approval.")


def _rpc_error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def mcp_handle(brain, msg, source="LLM", on_propose=None):
    """Answer one JSON-RPC message. Returns a response dict, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    id_, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:
        return None
    owner = brain.owner
    if method == "initialize":
        asked = str(params.get("protocolVersion", ""))
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "nexuslab-brain", "title": "NexusLab Brain", "version": "1.3.0"},
            "instructions": INSTRUCTIONS.format(owner=owner),
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        tools = [{**t, "description": t["description"].format(owner=owner)} for t in TOOLS]
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": tools}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(args, dict):
            return _rpc_error(id_, -32602, "arguments must be an object")
        try:
            if name == "search_memory":
                text = brain.recall_text(str(args.get("query", "")))
            elif name == "read_memory":
                n = brain.read(args.get("title"))
                if n:
                    brain.log("read", f'LLM read "{n["title"]}"', [n["path"]], "mcp")
                text = f"# {n['title']}\n\n{n['body']}" if n else f'No memory titled "{args.get("title")}". Try search_memory.'
            elif name == "list_memory_topics":
                text = brain.topics_text()
            elif name == "save_memory":
                related = args.get("related") or []
                if not isinstance(related, list):
                    related = [related]
                item = brain.propose(args.get("title"), args.get("content"), args.get("topic", ""), related, source)
                if on_propose:
                    on_propose(item)
                text = (f'Queued "{item["title"]}" for {owner} to review on the NexusLab Brain tab. '
                        "It will be remembered once approved.")
            else:
                return _rpc_error(id_, -32602, f"Unknown tool: {name}")
            return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except ValueError as e:
            return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}}
    return _rpc_error(id_, -32601, f"Method not found: {method}")
