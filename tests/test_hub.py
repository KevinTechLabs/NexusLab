"""Hub: login sessions, the auth gate, device actions and the alert state machines."""

import asyncio
import sys
import time

import pytest
from fastapi.testclient import TestClient

hub = sys.modules["nexuslab_hub"]  # loaded by conftest.py


@pytest.fixture
def client():
    hub.FAILED.clear()
    # Not used as a context manager, so the polling/alert loops never start.
    return TestClient(hub.app, follow_redirects=False)


@pytest.fixture
def authed(client):
    client.cookies.set(hub.COOKIE, hub.make_session())
    return client


@pytest.fixture
def sent(monkeypatch):
    """Capture alerts instead of posting them to Discord."""
    messages = []

    async def fake_send(self, title, description, color, log=None, fields=None):
        messages.append(title)

    monkeypatch.setattr(hub.Alerter, "send", fake_send)
    return messages


def device(**cfg):
    base = {"id": "box", "name": "Box", "url": "http://10.0.0.5:9101"}
    return hub.Device({**base, **cfg})


# ---------------------------------------------------------------- sessions
def test_session_round_trip():
    assert hub.valid_session(hub.make_session())


def test_tampered_session_is_rejected():
    exp, _, sig = hub.make_session().partition(".")
    assert not hub.valid_session(f"{int(exp) + 86400}.{sig}")  # extended expiry, old signature
    assert not hub.valid_session(f"{exp}.{'0' * len(sig)}")


def test_expired_session_is_rejected():
    import hashlib
    import hmac

    exp = str(int(time.time()) - 1)
    sig = hmac.new(hub.SECRET, exp.encode(), hashlib.sha256).hexdigest()
    assert not hub.valid_session(f"{exp}.{sig}")


@pytest.mark.parametrize("value", [None, "", "garbage", "123", ".abc", "abc.def"])
def test_malformed_sessions_are_rejected(value):
    assert not hub.valid_session(value)


# ---------------------------------------------------------------- auth gate
def test_api_needs_login(client):
    r = client.get("/api/state")
    assert r.status_code == 401


def test_pages_redirect_to_login(client):
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_public_paths_are_open(client):
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/login").status_code == 200


def test_wrong_password(client):
    r = client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert hub.COOKIE not in r.cookies


def test_login_sets_a_working_cookie(client):
    r = client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    assert r.status_code == 200
    assert hub.valid_session(r.cookies[hub.COOKIE])
    state = client.get("/api/state").json()
    assert state["auth"] is True
    assert [d["id"] for d in state["devices"]] == ["server", "pi"]


def test_login_is_rate_limited(client):
    hub.FAILED["testclient"] = [time.time()] * 8
    r = client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    assert r.status_code == 429


def test_login_page_redirects_when_already_signed_in(authed):
    assert authed.get("/login").status_code == 303


# ---------------------------------------------------------------- device actions
def test_unknown_device(authed):
    assert authed.post("/api/devices/nope/containers/web/restart").status_code == 404


def test_monitor_only_device_refuses_container_actions(authed):
    r = authed.post("/api/devices/pi/containers/web/stop")
    assert r.status_code == 403
    assert "monitor-only" in r.json()["detail"]


def test_device_capabilities():
    assert device(mac="AA:BB:CC:00:11:22").can_wake
    assert device(mac="AA:BB:CC:00:11:22").mac == "aa:bb:cc:00:11:22"
    d = device()
    assert not d.can_wake
    d.learned = {"mac": "aa:bb:cc:00:11:22", "wired": True, "wol": None}
    assert d.can_wake
    d.learned["wol"] = False  # card said it can't wake
    assert not d.can_wake
    d.learned = {"mac": "aa:bb:cc:00:11:22", "wired": False}
    assert not d.can_wake


def test_public_state_explains_deliberate_shutdown():
    d = device()
    d.power_action = ("shutdown", time.time())
    assert d.public()["error"] == "Turned off on purpose"


def test_magic_packet_needs_a_valid_mac():
    with pytest.raises(ValueError):
        hub.send_magic_packet(device())
    with pytest.raises(ValueError):
        hub.send_magic_packet(device(mac="not-a-mac"))


@pytest.mark.parametrize(
    ("seconds", "text"), [(5, "5 seconds"), (89, "89 seconds"), (600, "10 minutes"), (7200, "2.0 hours")]
)
def test_duration_text(seconds, text):
    assert hub._dur(seconds) == text


# ---------------------------------------------------------------- alerts
def test_offline_then_back_online(sent):
    alerter = hub.Alerter({"offline_after_seconds": 0})
    d = device()
    asyncio.run(alerter.check_device(d))
    assert sent == ["🔴 Box is offline"]
    asyncio.run(alerter.check_device(d))
    assert len(sent) == 1  # alerts once, not every poll
    d.online = True
    asyncio.run(alerter.check_device(d))
    assert sent[-1] == "🟢 Box is back online"


def test_deliberate_shutdown_does_not_alert(sent):
    alerter = hub.Alerter({"offline_after_seconds": 0})
    d = device()
    d.power_action = ("shutdown", time.time())
    asyncio.run(alerter.check_device(d))
    assert sent == []


def test_temperature_alert_has_hysteresis(sent):
    alerter = hub.Alerter({"temp_sustain_seconds": 0, "cpu_temp_c": 85})
    d = device()

    def poll(temp):
        d.online, d.metrics = True, {"cpu": {"percent": 1, "temp_c": temp}, "ram": {"percent": 1}}
        asyncio.run(alerter.check_device(d))

    poll(90)
    assert sent == ["🔥 Box: CPU is running hot"]
    poll(82)  # below the limit but inside the 5 °C band: no flapping
    assert len(sent) == 1
    poll(79)
    assert sent[-1] == "✅ Box: CPU has cooled down"


def test_stopped_container_alerts_unless_stopped_from_dashboard(sent):
    alerter = hub.Alerter({})
    d = device()
    d.online = True

    def poll(**status):
        d.containers = {"ok": True, "items": [{"name": n, "status": s} for n, s in status.items()]}
        asyncio.run(alerter.check_containers(d))

    poll(web="running", db="running")
    assert sent == []  # first look only records what's running
    d.recent_actions["db"] = time.time()
    poll(web="exited", db="exited")
    assert sent == ["⚠️ Box: web stopped"]
    poll(web="running", db="running")
    assert sent[-1] == "✅ Box: web is running again"
    assert len(sent) == 2


def test_daily_stats_track_peaks():
    stats = hub.DailyStats()
    d = device()
    d.online = True
    for cpu, temp in ((10, 50), (30, 70), (20, None)):
        d.metrics = {"cpu": {"percent": cpu, "temp_c": temp}, "ram": {"percent": cpu}, "gpus": []}
        stats.record(d)
    d.online = False
    stats.record(d)
    s = stats.dev["box"]
    assert (s["samples"], s["online"], s["cpu_n"]) == (4, 3, 3)
    assert s["cpu_sum"] == 60
    assert s["cpu_temp"] == 70
    assert s["ram_peak"] == 30
