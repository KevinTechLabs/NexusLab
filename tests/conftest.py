"""Shared fixtures.

agent.py and hub/app.py read their configuration at import time, so each test
loads a fresh copy of the module with the environment / config it needs.
"""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))  # hub/app.py imports hub/brain.py

AGENT_TOKEN = "test-agent-token"
HUB_PASSWORD = "correct horse battery staple"


def _load(name, path):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def load_agent(monkeypatch):
    """Return a loader: load_agent(NEXUSLAB_DOCKER="control", ...) -> fresh agent module."""

    def _load_agent(**env):
        settings = {
            "NEXUSLAB_TOKEN": AGENT_TOKEN,
            "NEXUSLAB_WOL": "off",  # never touch the test machine's network card
            "NEXUSLAB_OLLAMA_URL": "off",
            "NEXUSLAB_DOCKER": "off",
            "NEXUSLAB_CONTAINERS": "",
            "NEXUSLAB_POWER": "on",
            "NEXUSLAB_TEMP_SENSOR": "",
        }
        settings.update(env)
        for key, value in settings.items():
            monkeypatch.setenv(key, value)
        return _load("nexuslab_agent", ROOT / "agent" / "agent.py")

    return _load_agent


@pytest.fixture
def agent(load_agent):
    return load_agent()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


def hub_config(**overrides):
    cfg = {
        "poll_seconds": 3,
        "auth": {"username": "admin", "password": HUB_PASSWORD},
        "devices": [
            {"id": "server", "name": "Home Server", "url": "http://192.168.1.10:9101", "token": "tok-server", "docker": "control"},
            {"id": "ai", "name": "AI PC", "url": "http://192.168.1.11:9101", "token": "tok-ai", "docker": "monitor", "gpu": True},
            {"id": "mini", "name": "Mini PC", "url": "http://192.168.1.13:9101", "token": "tok-mini"},
        ],
        "alerts": {"discord_webhook": "", "daily_summary": "", "timezone": "UTC"},
        "chat": {"owner": "Kevin"},
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture
def load_hub(monkeypatch, tmp_path):
    """Return a loader: load_hub(**config_overrides) -> fresh hub module.

    The module's shared httpx client is replaced by one backed by
    ``hub.mock`` (an httpx.MockTransport). Set ``hub.routes[(method, path)]``
    to a callable(request) -> httpx.Response to fake agent answers.
    """

    def _load_hub(**overrides):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.safe_dump(hub_config(**overrides)))
        monkeypatch.setenv("NEXUSLAB_CONFIG", str(cfg_file))
        monkeypatch.setenv("NEXUSLAB_DATA_DIR", str(tmp_path / "data"))
        hub = _load("nexuslab_hub", ROOT / "hub" / "app.py")

        hub.routes = {}
        hub.requests = []

        def handler(request: httpx.Request):
            hub.requests.append(request)
            fn = hub.routes.get((request.method, request.url.path))
            if fn is None:
                raise httpx.ConnectError("no route in test", request=request)
            return fn(request)

        hub.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return hub

    return _load_hub


@pytest.fixture
def hub(load_hub):
    return load_hub()


@pytest.fixture
def no_sleep(monkeypatch):
    """no_sleep(module): make that module's asyncio.sleep return at once
    (login back-off, Wake-on-LAN retries) without touching the event loop's own asyncio."""
    import asyncio

    async def _instant(*_a, **_k):
        return None

    class _Asyncio:
        sleep = staticmethod(_instant)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    def _apply(module):
        monkeypatch.setattr(module, "asyncio", _Asyncio())

    return _apply
