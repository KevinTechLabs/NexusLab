"""Agent: token check, feature switches, and the sensor/Docker-stats parsing.

conftest.py starts the agent with Docker, power control, Wake-on-LAN and
Ollama all switched off, so none of these tests can touch the machine.
"""

import sys
from collections import namedtuple

import pytest
from fastapi.testclient import TestClient

agent = sys.modules["nexuslab_agent"]  # loaded by conftest.py

AUTH = {"Authorization": "Bearer agent-secret"}
Temp = namedtuple("Temp", "label current")


@pytest.fixture
def client():
    return TestClient(agent.app)


# ---------------------------------------------------------------- token
def test_health_is_open(client):
    assert client.get("/health").json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "agent-secret"},
        {"Authorization": "Bearer agent-secret "},
    ],
)
def test_endpoints_need_the_exact_token(client, headers):
    for method, path in (("get", "/metrics"), ("get", "/containers"), ("post", "/power/reboot")):
        assert getattr(client, method)(path, headers=headers).status_code == 401, path


def test_metrics_shape(client):
    m = client.get("/metrics", headers=AUTH).json()
    assert {"hostname", "cpu", "ram", "disk", "gpus", "net"} <= set(m)
    assert 0 <= m["ram"]["percent"] <= 100
    assert m["docker"] == "off"
    assert m["power"] is False
    assert m["ollama"] is False


# ---------------------------------------------------------------- feature switches
def test_docker_off(client):
    assert client.get("/containers", headers=AUTH).status_code == 404
    assert client.get("/containers/web/logs", headers=AUTH).status_code == 404


def test_container_actions_need_control_mode(client):
    assert client.post("/containers/web/stop", headers=AUTH).status_code == 403


def test_power_off(client):
    r = client.post("/power/reboot", headers=AUTH)
    assert r.status_code == 403
    assert "NEXUSLAB_POWER=off" in r.json()["detail"]


def test_power_rejects_unknown_actions(client, monkeypatch):
    monkeypatch.setattr(agent, "POWER", True)
    assert client.post("/power/format-disk", headers=AUTH).status_code == 400


def test_ollama_off(client):
    assert client.get("/ollama", headers=AUTH).status_code == 404
    assert client.post("/ollama/delete", headers=AUTH, json={"model": "x"}).status_code == 400


def test_container_allowlist(monkeypatch):
    monkeypatch.setattr(agent, "ALLOWLIST", {"web"})
    with pytest.raises(agent.HTTPException) as e:
        agent._get_container("db")
    assert e.value.status_code == 403


# ---------------------------------------------------------------- temperatures
def test_cpu_temp_prefers_known_labels():
    sensors = {"k10temp": [Temp("Tccd1", 70.0), Temp("Tctl", 55.46)]}
    assert agent.cpu_temp(sensors) == 55.5


def test_cpu_temp_falls_back_to_hottest_reading():
    assert agent._pick([Temp("Core 0", 41.0), Temp("Core 1", 47.25), Temp("Core 2", 0.0)]) == 47.2


def test_cpu_temp_without_sensors():
    assert agent.cpu_temp({}) is None
    assert agent.cpu_temp({"k10temp": []}) is None
    assert agent._pick([Temp("x", 0.0)]) is None


def test_disk_temp():
    assert agent.disk_temp({"nvme": [Temp("Sensor 1", 50.0), Temp("Composite", 38.0)]}) == 38.0
    assert agent.disk_temp({"coretemp": [Temp("", 60.0)]}) is None


def test_ethtool_without_interface():
    assert agent.ethtool_wol(None) == (None, None)


# ---------------------------------------------------------------- docker stats
class FakeContainer:
    def __init__(self, stats):
        self._stats = stats

    def stats(self, stream):
        assert stream is False
        return self._stats


def test_container_stats():
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 2_000}, "system_cpu_usage": 20_000, "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 1_000}, "system_cpu_usage": 10_000},
        "memory_stats": {"usage": 500, "limit": 1000, "stats": {"inactive_file": 100}},
    }
    # 1000 / 10000 of the machine * 4 CPUs = 40 %, memory minus page cache
    assert agent._stats(FakeContainer(stats)) == (40.0, 400, 1000)


def test_container_stats_first_sample_and_bad_data():
    first = {"cpu_stats": {"cpu_usage": {"total_usage": 5, "percpu_usage": [1, 2]}}, "memory_stats": {}}
    assert agent._stats(FakeContainer(first)) == (0.0, 0, None)
    assert agent._stats(FakeContainer({})) == (None, None, None)
