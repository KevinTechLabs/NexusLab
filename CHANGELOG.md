# Changelog

## 1.2.0

### Added
- **Power tab.** Shows each machine's power draw right now, kWh and cost for today and this month, a projection for the whole month, and a 30-day chart of daily use. Set your electricity rate per kWh on the tab (or `energy.rate_per_kwh` in `config.yaml`).
- Agents report CPU package power (RAPL on Intel and AMD) and, on a Raspberry Pi 5, the power chip's readings. The hub adds a typical allowance for the rest of the machine and power-supply losses to estimate watts at the wall.
- Optional Shelly smart plugs (`power_meter.url` per device) give exact, **Measured** readings instead of estimates.
- Energy totals are kept in `energy.json` next to `state.json` (`NEXUSLAB_DATA_DIR`), so they survive hub restarts.
- `POST /api/energy/rate` sets the rate; `GET /api/state` includes an `energy` section.
- The daily Discord summary includes yesterday's kWh and cost, and the chat assistant can answer questions about power use.

## 1.1.0

### Security
- **Agent fails closed without a token.** If `NEXUSLAB_TOKEN` is empty, every endpoint except `/health` now answers `503` instead of serving metrics, container control and power control to anyone on the network.
- Container names are validated against Docker's naming rule on the hub and the agent, so names such as `..` can't be forwarded into agent URLs. The hub also rejects unknown container actions before contacting the agent.
- `NEXUSLAB_OLLAMA_URL` must be `http://` or `https://`; other schemes (such as `file://`) disable the Ollama integration.
- The session cookie gets the `Secure` flag when the hub is reached over HTTPS, including behind a reverse proxy that sets `X-Forwarded-Proto`.
- Expired failed-login records are pruned, so the lockout table can't grow without bound.

### Added
- `GET /healthz` on the hub (no login needed) and a Docker `HEALTHCHECK` that uses it.
- Test suites for the agent and hub (`tests/`), with Docker, NVIDIA, Ollama, Pi-hole, Discord and agents all faked.
- GitHub Actions CI: ruff, shellcheck, bandit, pip-audit, pytest on Python 3.11 to 3.13 with an 80% coverage gate, and a Docker build and smoke test.
- Dependabot for pip, Docker and GitHub Actions.
- `NEXUSLAB_DATA_DIR` sets where the hub keeps `state.json` (default `hub/data`).

### Changed
- The hub image now listens on port 8484 by default, matching `docker-compose.yml`.

## 1.0.0
- First release: hub, agents, Pi-hole, Ollama, chat, Discord alerts, Wake-on-LAN and the installable phone app.
