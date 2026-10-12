# Changelog

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
