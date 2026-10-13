"""Brain: the Obsidian-vault memory, its review queue, and the MCP endpoint."""
import pytest
from brain import MAX_CONTENT, Brain, clean_title, mcp_handle, tokenize
from conftest import HUB_PASSWORD
from fastapi.testclient import TestClient

TOKEN = "test-mcp-token"
MCP = {"Authorization": f"Bearer {TOKEN}"}


def rpc(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def call(tool, **args):
    return rpc(9, "tools/call", {"name": tool, "arguments": args})


def text(reply):
    return reply["result"]["content"][0]["text"]


@pytest.fixture
def brain(tmp_path):
    return Brain(tmp_path / "vault", recall_chars=2000, owner="Kevin")


# ---------------------------------------------------------------- vault
def test_new_vault_is_obsidian_ready(brain):
    names = sorted(p.name for p in brain.vault.iterdir())
    assert names == [".nexus", "MOCs", "Memory", "Start here.md"]
    assert brain.graph()["nodes"] == []  # the readme isn't a memory


def test_write_files_note_under_its_topic(brain):
    n = brain.write("Nightly backups run at 2am", "Restic backs up the NAS at 2am.", "Homelab", ["Restic"])
    assert n["path"] == "Memory/Nightly backups run at 2am.md"
    text_ = (brain.vault / n["path"]).read_text()
    assert text_.startswith("---\ncreated: ") and "topic: Homelab" in text_ and "Related: [[Restic]]" in text_
    moc = (brain.vault / "MOCs/Homelab MOC.md").read_text()
    assert "- [[Nightly backups run at 2am]]" in moc
    brain.write("Restic", "Repo on the NAS.", "Homelab")
    assert moc.count("[[") == 1 and (brain.vault / "MOCs/Homelab MOC.md").read_text().count("[[") == 2


def test_graph_links_notes_to_topics(brain):
    brain.write("Restic", "Backup tool.", "Homelab")
    brain.write("Backups at 2am", "Nightly.", "Homelab", ["Restic"])
    brain.write("Short answers", "Kevin likes short answers.", "Preferences")
    g = brain.graph()
    home = {n["title"]: n["home"] for n in g["nodes"]}
    assert home["Restic"] == "MOCs/Homelab MOC.md" and home["Short answers"] == "MOCs/Preferences MOC.md"
    assert ["Memory/Backups at 2am.md", "Memory/Restic.md"] in g["links"]
    assert sorted(g["topics"]) == ["Homelab", "Preferences"]


def test_writing_an_existing_title_appends(brain):
    brain.write("Restic", "Backup tool.", "Homelab")
    n = brain.write("restic", "Keep 7 daily snapshots.", "Homelab", ["Pruning"])
    body = (brain.vault / "Memory/Restic.md").read_text()
    assert n["title"] == "Restic" and "Backup tool." in body and "Keep 7 daily snapshots." in body and "[[Pruning]]" in body


@pytest.mark.parametrize("raw, safe", [
    ("../../etc/passwd", "etc passwd"),
    (".hidden", "hidden"),
    ("a/b\\c:d*e?f", "a b c d e f"),
    ("[[x]] #tag ^id", "x tag id"),
])
def test_titles_cannot_escape_the_vault(raw, safe):
    assert clean_title(raw) == safe


def test_read_by_title_or_path_stays_inside(brain):
    brain.write("Restic", "Backup tool.", "Homelab")
    assert brain.read("restic")["path"] == "Memory/Restic.md"
    assert brain.read("Memory/Restic.md")["title"] == "Restic"
    assert brain.read("../outside") is None


# ---------------------------------------------------------------- recall
def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("How should I schedule the backups running?") == ["schedule", "backup", "runn"]


def test_recall_prefers_topic_links_and_respects_budget(brain):
    brain.write("Backups at 2am", "Restic backs up every volume at 2am.", "Homelab")
    brain.write("Big backup notes", "backup " * 400, "Homelab")
    brain.write("Coffee", "Backup coffee beans live in the pantry.", "Kitchen")
    r = brain.recall("when do backups run")
    assert r["mocs"] == ["Homelab"]
    by = {x["title"]: x for x in r["results"]}
    assert by["Backups at 2am"]["via"] == "moc" and by["Backups at 2am"]["in_context"]
    assert r["used"] <= r["budget"] == 2000
    assert not by["Big backup notes"]["in_context"] and by["Big backup notes"]["body"] is None
    assert brain.activity[-1]["kind"] == "recall"


def test_recall_with_no_matches(brain):
    r = brain.recall("quantum chromodynamics")
    assert r["results"] == [] and r["used"] == 0


# ---------------------------------------------------------------- review queue
def test_proposals_wait_for_approval(brain):
    item = brain.propose("Kuma alerts go to Discord", "Uptime Kuma posts to #alerts.", "Homelab", ["Discord"], "LLM")
    assert brain.graph()["nodes"] == []
    assert [i["id"] for i in brain.inbox()] == [item["id"]]
    n = brain.approve(item["id"], {"title": "Uptime Kuma alerts go to Discord"})
    assert n["title"] == "Uptime Kuma alerts go to Discord" and brain.inbox() == []
    assert "source: LLM" in (brain.vault / n["path"]).read_text()


def test_discard_and_bad_ids(brain):
    item = brain.propose("Temp", "Throwaway.")
    brain.discard(item["id"])
    assert brain.inbox() == []
    for bad in (item["id"], "../../x", "zzzzzzzzzzzz"):
        with pytest.raises(KeyError):
            brain.discard(bad)


def test_proposal_marks_updates_to_existing_notes(brain):
    brain.write("Restic", "Backup tool.", "Homelab")
    brain.propose("Restic", "Retention: 7 daily.")
    assert brain.inbox()[0]["updates_existing"] is True


@pytest.mark.parametrize("title, content, msg", [
    ("", "x", "title"), ("t", "", "content"), ("t", "x" * (MAX_CONTENT + 1), "under"),
])
def test_proposal_validation(brain, title, content, msg):
    with pytest.raises(ValueError, match=msg):
        brain.propose(title, content)


def test_inbox_is_capped(brain, monkeypatch):
    import brain as brain_mod
    monkeypatch.setattr(brain_mod, "MAX_INBOX", 2)
    brain.propose("a", "x")
    brain.propose("b", "x")
    with pytest.raises(ValueError, match="full"):
        brain.propose("c", "x")


# ---------------------------------------------------------------- MCP protocol
def test_mcp_handshake_and_tools(brain):
    r = mcp_handle(brain, rpc(1, "initialize", {"protocolVersion": "2025-03-26"}))
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert mcp_handle(brain, rpc(1, "initialize", {"protocolVersion": "1999-01-01"}))["result"]["protocolVersion"] == "2025-06-18"
    assert mcp_handle(brain, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = mcp_handle(brain, rpc(2, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["search_memory", "read_memory", "list_memory_topics", "save_memory"]
    assert "Kevin" in tools[0]["description"]
    assert mcp_handle(brain, rpc(3, "ping"))["result"] == {}
    assert mcp_handle(brain, rpc(4, "nope"))["error"]["code"] == -32601
    assert mcp_handle(brain, {"id": 5})["error"]["code"] == -32600


def test_mcp_tools_round_trip(brain):
    seen = []
    r = mcp_handle(brain, call("save_memory", title="Backups at 2am", content="Restic runs nightly.", topic="Homelab", related="Restic"),
                   "LLM", seen.append)
    assert "Queued" in text(r) and seen[0]["links"] == ["Restic"]
    assert "No saved memories" in text(mcp_handle(brain, call("search_memory", query="backup")))
    brain.approve(seen[0]["id"])
    found = text(mcp_handle(brain, call("search_memory", query="backup")))
    assert "### Backups at 2am" in found and "Restic runs nightly." in found
    assert "Restic runs nightly." in text(mcp_handle(brain, call("read_memory", title="backups at 2am")))
    assert "No memory titled" in text(mcp_handle(brain, call("read_memory", title="missing")))
    assert "Homelab (1): Backups at 2am" in text(mcp_handle(brain, call("list_memory_topics")))
    bad = mcp_handle(brain, call("save_memory", title="", content="x"))
    assert bad["result"]["isError"] is True
    assert mcp_handle(brain, call("delete_everything"))["error"]["code"] == -32602
    assert mcp_handle(brain, rpc(6, "tools/call", {"name": "search_memory", "arguments": "x"}))["error"]["code"] == -32602


# ---------------------------------------------------------------- hub endpoints
def signed_in(hub):
    c = TestClient(hub.app, follow_redirects=False)
    assert c.post("/api/login", json={"username": "admin", "password": HUB_PASSWORD}).status_code == 200
    return c


@pytest.fixture
def mcp_hub(load_hub):
    return load_hub(brain={"mcp_token": TOKEN, "recall_chars": 3000})


def test_brain_api_needs_login(hub):
    c = TestClient(hub.app)
    assert c.get("/api/brain").status_code == 401
    assert c.post("/api/brain/recall", json={"query": "x"}).status_code == 401


def test_brain_api_flow(hub):
    c = signed_in(hub)
    d = c.get("/api/brain").json()
    assert d["enabled"] and d["mcp"] is False and d["graph"]["nodes"] == [] and d["inbox"] == []
    assert c.get("/api/state").json()["brain"] == {"inbox": 0}
    r = c.post("/api/brain/notes", json={"title": "Restic", "content": "Backup tool.", "topic": "Homelab"})
    assert r.json() == {"saved": "Memory/Restic.md", "title": "Restic"}
    assert c.post("/api/brain/notes", json={"title": "", "content": ""}).status_code == 400
    assert c.post("/api/brain/recall", json={"query": "restic"}).json()["results"][0]["title"] == "Restic"
    assert c.post("/api/brain/recall", json={"query": " "}).status_code == 400
    assert c.get("/api/brain/note", params={"path": "Restic"}).json()["body"].startswith("Backup tool.")
    assert c.get("/api/brain/note", params={"path": "nope"}).status_code == 404

    item = hub.BRAIN.propose("Backups at 2am", "Nightly.", "Homelab")
    assert c.get("/api/state").json()["brain"] == {"inbox": 1}
    r = c.post(f"/api/brain/inbox/{item['id']}/approve", json={"title": "Backups run at 2am", "links": ["Restic"]})
    assert r.json()["saved"] == "Memory/Backups run at 2am.md"
    assert c.post(f"/api/brain/inbox/{item['id']}/approve").status_code == 404
    item = hub.BRAIN.propose("Junk", "Nope.")
    assert c.post(f"/api/brain/inbox/{item['id']}/discard").json() == {"discarded": "Junk"}
    assert c.post(f"/api/brain/inbox/{item['id']}/explode").status_code == 400
    assert len(c.get("/api/brain").json()["activity"]) >= 3


def test_brain_can_be_turned_off(load_hub):
    hub = load_hub(brain={"enabled": False})
    c = signed_in(hub)
    assert c.get("/api/brain").status_code == 404
    assert c.get("/api/state").json()["brain"] is None
    assert TestClient(hub.app).post("/mcp", json=rpc(1, "ping"), headers=MCP).status_code == 404


def test_mcp_is_off_without_a_token(hub):
    assert TestClient(hub.app).post("/mcp", json=rpc(1, "ping"), headers=MCP).status_code == 404


def test_mcp_endpoint_auth(mcp_hub):
    c = TestClient(mcp_hub.app)  # no session cookie: MCP uses its own token
    assert c.post("/mcp", json=rpc(1, "ping")).status_code == 401
    assert c.post("/mcp", json=rpc(1, "ping"), headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/mcp", json=rpc(1, "ping"), headers={**MCP, "Origin": "https://evil.example"}).status_code == 403
    assert c.post("/mcp", json=rpc(1, "ping"), headers={**MCP, "MCP-Protocol-Version": "1999-01-01"}).status_code == 400
    assert c.post("/mcp", content=b"{not json", headers=MCP).json()["error"]["code"] == -32700
    assert c.get("/mcp").status_code == 405


def test_mcp_endpoint_session(mcp_hub):
    c = TestClient(mcp_hub.app)
    r = c.post("/mcp", json=rpc(1, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "open-webui"}}), headers=MCP)
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "nexuslab-brain"
    assert c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=MCP).status_code == 202
    r = c.post("/mcp", json=call("save_memory", title="Kuma alerts", content="Posts to #alerts."), headers=MCP)
    assert "Queued" in text(r.json())
    batch = c.post("/mcp", json=[rpc(1, "ping"), call("list_memory_topics")], headers=MCP).json()
    assert [x["id"] for x in batch] == [1, 9]
    inbox = signed_in(mcp_hub).get("/api/brain").json()["inbox"]
    assert inbox[0]["title"] == "Kuma alerts" and inbox[0]["source"] == "LLM via MCP"


def test_allowed_origin(load_hub):
    hub = load_hub(brain={"mcp_token": TOKEN, "mcp_allowed_origins": ["http://192.168.1.20:3000/"]})
    r = TestClient(hub.app).post("/mcp", json=rpc(1, "ping"), headers={**MCP, "Origin": "http://192.168.1.20:3000"})
    assert r.status_code == 200
