"""Load the hub and the agent as modules with a throwaway config.

Both read their configuration at import time, so the environment is set up
here, before any test module imports them. Nothing reaches a real device,
Docker, Ollama, Discord or the power controls.
"""

import importlib.util
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="nexuslab-test-"))

HUB_CONFIG = """
poll_seconds: 3
container_poll_seconds: 10
history_points: 5
auth:
  username: admin
  password: test-password-123
  session_days: 30
devices:
  - id: server
    name: Home Server
    url: http://192.168.1.10:9101
    token: server-token
    docker: control
  - id: pi
    name: Raspberry Pi
    url: http://192.168.1.12:9101
    token: pi-token
    docker: monitor
    mac: "AA:BB:CC:DD:EE:FF"
alerts:
  discord_webhook: ""
  offline_after_seconds: 0
  cpu_temp_c: 85
  gpu_temp_c: 83
  temp_sustain_seconds: 0
  timezone: America/Los_Angeles
"""

(TMP / "config.yaml").write_text(HUB_CONFIG)
os.environ["NEXUSLAB_CONFIG"] = str(TMP / "config.yaml")

# Agent: token required, and every side-effecting feature switched off.
os.environ.update(
    NEXUSLAB_TOKEN="agent-secret",
    NEXUSLAB_DOCKER="off",
    NEXUSLAB_POWER="off",
    NEXUSLAB_WOL="off",
    NEXUSLAB_OLLAMA_URL="off",
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hub = _load("nexuslab_hub", ROOT / "hub" / "app.py")
hub.DATA_FILE = TMP / "data" / "state.json"  # keep save_state() out of the repo
agent = _load("nexuslab_agent", ROOT / "agent" / "agent.py")
