import io
import urllib.error
from collections import namedtuple
from types import SimpleNamespace

import docker
import pytest
from fastapi.testclient import TestClient

Temp = namedtuple("Temp", "label current")


# ---------------------------------------------------------------- fakes
class FakeContainer:
    def __init__(self, name, status="running", image="nginx:latest", health=None, stats=None):
        self.name = name
        self.id = f"id-{name}"
        self.status = status
        self.attrs = {
            "Config": {"Image": image},
            "State": {"StartedAt": "2026-10-01T00:00:00Z", **({"Health": {"Status": health}} if health else {})},
        }
        self._stats = stats
        self.calls = []

    def stats(self, stream=False):
        if self._stats is None:
            raise RuntimeError("no stats")
        return self._stats

    def logs(self, tail, timestamps):
        self.calls.append(("logs", tail, timestamps))
        return b"line one\nline two\n"

    def start(self):
        self.calls.append(("start",))
        self.status = "running"

    def stop(self, timeout):
        self.calls.append(("stop", timeout))
        self.status = "exited"

    def restart(self, timeout):
        self.calls.append(("restart", timeout))
        self.status = "running"

    def reload(self):
        pass


class FakeDocker:
    def __init__(self, *containers):
        self.by_name = {c.name: c for c in containers}
        self.containers = self

    def list(self, all=False):
        return list(self.by_name.values())

    def get(self, name):
        if name not in self.by_name:
            raise docker.errors.NotFound("missing")
        return self.by_name[name]


STATS = {
    "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 4},
    "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
    "memory_stats": {"usage": 500, "limit": 1000, "stats": {"inactive_file": 100}},
}


def client(agent):
    return TestClient(agent.app)


# ---------------------------------------------------------------- auth
def test_health_needs_no_token(agent):
    assert client(agent).get("/health").json() == {"ok": True}


@pytest.mark.parametrize("header", [None, "Bearer wrong", "test-agent-token", "Basic test-agent-token"])
def test_protected_endpoints_reject_bad_tokens(agent, header):
    headers = {"Authorization": header} if header else {}
    r = client(agent).get("/metrics", headers=headers)
    assert r.status_code == 401


def test_agent_without_token_fails_closed(load_agent):
    agent = load_agent(NEXUSLAB_TOKEN="", NEXUSLAB_DOCKER="control")
    c = client(agent)
    assert c.get("/health").status_code == 200
    for method, path in [("get", "/metrics"), ("get", "/containers"), ("post", "/power/reboot"), ("post", "/containers/web/stop")]:
        r = getattr(c, method)(path)
        assert r.status_code == 503, path
        assert "NEXUSLAB_TOKEN" in r.json()["detail"]


# ---------------------------------------------------------------- metrics
def test_metrics_shape(agent, auth, monkeypatch):
    monkeypatch.setattr(agent, "ethtool_wol", lambda iface: (None, None))
    m = client(agent).get("/metrics", headers=auth).json()
    assert {"hostname", "os", "uptime_s", "cpu", "ram", "disk", "gpus", "docker", "power", "ollama", "net"} <= m.keys()
    assert 0 <= m["ram"]["percent"] <= 100
    assert m["disk"]["path"] == "/"
    assert m["docker"] == "off"
    assert m["power"] is True
    assert m["ollama"] is False
    assert m["net"]["ethtool"] is False


def test_power_flag_reflects_env(load_agent):
    assert load_agent(NEXUSLAB_POWER="off").POWER is False
    assert load_agent(NEXUSLAB_POWER="no").POWER is False
    assert load_agent(NEXUSLAB_POWER="on").POWER is True


# ---------------------------------------------------------------- temperatures
def test_pick_prefers_known_labels_then_hottest(agent):
    assert agent._pick([Temp("Tccd1", 70.0), Temp("Tctl", 55.0)]) == 55.0
    assert agent._pick([Temp("core 0", 40.0), Temp("core 1", 62.0)]) == 62.0
    assert agent._pick([Temp("x", 0.0)]) is None


def test_cpu_temp_sensor_order_and_override(load_agent):
    sensors = {"acpitz": [Temp("", 30.0)], "k10temp": [Temp("Tctl", 61.0)]}
    assert load_agent().cpu_temp(sensors) == 61.0
    assert load_agent(NEXUSLAB_TEMP_SENSOR="acpitz").cpu_temp(sensors) == 30.0
    assert load_agent().cpu_temp({}) is None


def test_disk_temp(agent):
    assert agent.disk_temp({"nvme": [Temp("Sensor 1", 50.0), Temp("Composite", 41.0)]}) == 41.0
    assert agent.disk_temp({}) is None


# ---------------------------------------------------------------- network / Wake-on-LAN
ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
    "wlan0\t0001A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
    "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n"
)


def test_default_iface_reads_route_table(agent, monkeypatch):
    monkeypatch.setattr(agent, "open", lambda *a, **k: io.StringIO(ROUTE), raising=False)
    assert agent.default_iface() == "eth0"


def test_default_iface_without_proc(agent, monkeypatch):
    def boom(*a, **k):
        raise OSError("no /proc")

    monkeypatch.setattr(agent, "open", boom, raising=False)
    assert agent.default_iface() is None


def test_ethtool_wol_parsing(agent, monkeypatch):
    out = "Settings for eth0:\n\tSupports Wake-on: pumbg\n\tWake-on: d\n"
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out))
    assert agent.ethtool_wol("eth0") == ("pumbg", "d")
    assert agent.ethtool_wol(None) == (None, None)


def test_ethtool_missing(agent, monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("ethtool")

    monkeypatch.setattr(agent.subprocess, "run", missing)
    assert agent.ethtool_wol("eth0") == (None, None)


@pytest.mark.parametrize("current, expect_call", [("d", True), ("g", False)])
def test_arm_wol_only_when_needed(agent, monkeypatch, current, expect_call):
    calls = []
    monkeypatch.setattr(agent, "WOL", True)
    monkeypatch.setattr(agent, "default_iface", lambda: "eth0")
    monkeypatch.setattr(agent, "ethtool_wol", lambda iface: ("pumbg", current))
    monkeypatch.setattr(agent.subprocess, "run", lambda argv, **k: calls.append(argv))
    agent.arm_wol()
    assert calls == ([["ethtool", "-s", "eth0", "wol", "g"]] if expect_call else [])


def test_net_info_wired_vs_wifi(agent, monkeypatch):
    monkeypatch.setattr(agent, "default_iface", lambda: "wlp3s0")
    monkeypatch.setattr(agent, "iface_mac", lambda iface: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(agent, "ethtool_wol", lambda iface: ("g", "g"))
    agent._net.update(at=0.0, val=None)
    info = agent.net_info()
    assert info["wired"] is False
    assert info["wol_supported"] and info["wol_armed"]


# ---------------------------------------------------------------- docker
def test_containers_off_by_default(agent, auth):
    assert client(agent).get("/containers", headers=auth).status_code == 404


def test_container_list_sorted_with_stats(load_agent, auth):
    agent = load_agent(NEXUSLAB_DOCKER="monitor")
    agent._docker = FakeDocker(
        FakeContainer("web", stats=STATS, health="healthy"),
        FakeContainer("db", status="exited"),
    )
    body = client(agent).get("/containers", headers=auth).json()
    assert body["mode"] == "monitor"
    assert [i["name"] for i in body["items"]] == ["db", "web"]
    web = body["items"][1]
    assert web["cpu_percent"] == 80.0
    assert web["mem_used"] == 400
    assert web["health"] == "healthy"
    assert body["items"][0]["cpu_percent"] is None


def test_container_allowlist_filters_and_blocks(load_agent, auth):
    agent = load_agent(NEXUSLAB_DOCKER="control", NEXUSLAB_CONTAINERS="web")
    agent._docker = FakeDocker(FakeContainer("web"), FakeContainer("secret"))
    c = client(agent)
    assert [i["name"] for i in c.get("/containers", headers=auth).json()["items"]] == ["web"]
    assert c.post("/containers/secret/stop", headers=auth).status_code == 403
    assert c.get("/containers/secret/logs", headers=auth).status_code == 403


def test_monitor_mode_cannot_control(load_agent, auth):
    agent = load_agent(NEXUSLAB_DOCKER="monitor")
    agent._docker = FakeDocker(FakeContainer("web"))
    r = client(agent).post("/containers/web/stop", headers=auth)
    assert r.status_code == 403
    assert agent._docker.by_name["web"].calls == []


@pytest.mark.parametrize("action, expected", [("stop", ("stop", 15)), ("restart", ("restart", 15)), ("start", ("start",))])
def test_control_actions(load_agent, auth, action, expected):
    agent = load_agent(NEXUSLAB_DOCKER="control")
    web = FakeContainer("web")
    agent._docker = FakeDocker(web)
    r = client(agent).post(f"/containers/web/{action}", headers=auth)
    assert r.status_code == 200
    assert r.json()["action"] == action
    assert web.calls == [expected]


def test_control_rejects_unknown_action_and_names(load_agent, auth):
    agent = load_agent(NEXUSLAB_DOCKER="control")
    agent._docker = FakeDocker(FakeContainer("web"))
    c = client(agent)
    assert c.post("/containers/web/remove", headers=auth).status_code == 400
    assert c.post("/containers/bad%20name/stop", headers=auth).status_code == 400
    assert c.post("/containers/-rf/stop", headers=auth).status_code == 400
    assert c.post("/containers/ghost/stop", headers=auth).status_code == 404


def test_logs(load_agent, auth):
    agent = load_agent(NEXUSLAB_DOCKER="monitor")
    web = FakeContainer("web")
    agent._docker = FakeDocker(web)
    c = client(agent)
    r = c.get("/containers/web/logs?tail=50", headers=auth)
    assert r.status_code == 200
    assert r.text == "line one\nline two\n"
    assert web.calls == [("logs", 50, True)]
    assert c.get("/containers/web/logs?tail=999999", headers=auth).status_code == 422


def test_stats_handles_missing_precpu(agent):
    stats = {"cpu_stats": {"cpu_usage": {"total_usage": 10, "percpu_usage": [1, 2]}, "system_cpu_usage": 0}, "memory_stats": {}}
    assert agent._stats(FakeContainer("x", stats=stats)) == (0.0, 0, None)
    assert agent._stats(FakeContainer("x", stats=None)) == (None, None, None)


# ---------------------------------------------------------------- power
class FakeTimer:
    started = []

    def __init__(self, interval, fn):
        self.interval, self.fn = interval, fn

    def start(self):
        FakeTimer.started.append(self)


@pytest.fixture
def timers(agent, monkeypatch):
    FakeTimer.started = []
    monkeypatch.setattr(agent.threading, "Timer", FakeTimer)
    return FakeTimer.started


@pytest.mark.parametrize("action", ["reboot", "shutdown"])
def test_power_schedules_command_without_running_it(agent, auth, timers, action):
    r = client(agent).post(f"/power/{action}", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"action": action, "in_seconds": 2}
    assert len(timers) == 1 and timers[0].interval == 2.0


def test_power_rejects_bad_action_and_disabled(load_agent, auth, monkeypatch):
    agent = load_agent()
    monkeypatch.setattr(agent.threading, "Timer", FakeTimer)
    assert client(agent).post("/power/format-disk", headers=auth).status_code == 400
    agent = load_agent(NEXUSLAB_POWER="off")
    assert client(agent).post("/power/reboot", headers=auth).status_code == 403


# ---------------------------------------------------------------- Ollama
def test_ollama_url_must_be_http(load_agent):
    assert load_agent(NEXUSLAB_OLLAMA_URL="file:///etc/passwd").OLLAMA_URL == "off"
    assert load_agent(NEXUSLAB_OLLAMA_URL="http://127.0.0.1:11434/").OLLAMA_URL == "http://127.0.0.1:11434"


def test_ollama_off(agent, auth):
    assert client(agent).get("/ollama", headers=auth).status_code == 404


@pytest.fixture
def ollama(load_agent, monkeypatch):
    agent = load_agent(NEXUSLAB_OLLAMA_URL="http://127.0.0.1:11434")
    sent = []

    def fake(path, body=None, timeout=5):
        sent.append((path, body))
        return {
            "/api/version": {"version": "0.12.0"},
            "/api/tags": {"models": [{"name": "qwen3:8b", "size": 5, "details": {"parameter_size": "8B"}},
                                     {"name": "llama3:8b", "size": 4}]},
            "/api/ps": {"models": [{"name": "qwen3:8b", "size": 5, "size_vram": 5}]},
        }.get(path, {})

    monkeypatch.setattr(agent, "_ollama", fake)
    agent.sent = sent
    return agent


def test_ollama_info(ollama, auth):
    body = client(ollama).get("/ollama", headers=auth).json()
    assert body["version"] == "0.12.0"
    assert [m["name"] for m in body["installed"]] == ["llama3:8b", "qwen3:8b"]
    assert body["installed"][1]["params"] == "8B"
    assert body["loaded"][0]["vram"] == 5


def test_ollama_load_and_unload(ollama, auth):
    c = client(ollama)
    assert c.post("/ollama/load", json={"model": "qwen3:8b"}, headers=auth).status_code == 200
    assert c.post("/ollama/unload", json={"model": "qwen3:8b"}, headers=auth).status_code == 200
    assert c.post("/ollama/delete", json={"model": "qwen3:8b"}, headers=auth).status_code == 400
    generate = [b for p, b in ollama.sent if p == "/api/generate"]
    assert generate == [{"model": "qwen3:8b", "stream": False}, {"model": "qwen3:8b", "keep_alive": 0, "stream": False}]


def test_ollama_embedding_model_falls_back_to_embed(ollama, auth, monkeypatch):
    calls = []

    def fake(path, body=None, timeout=5):
        calls.append(path)
        if path == "/api/generate":
            raise urllib.error.HTTPError(path, 400, "embedding model", {}, None)
        return {}

    monkeypatch.setattr(ollama, "_ollama", fake)
    r = client(ollama).post("/ollama/load", json={"model": "nomic-embed-text"}, headers=auth)
    assert r.status_code == 200
    assert calls == ["/api/generate", "/api/embed"]
