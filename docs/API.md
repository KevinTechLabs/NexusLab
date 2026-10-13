# API

## Hub (port 8484)

When `auth.password` is set, every endpoint except the login and static icons needs a signed-in session cookie. Without one, API calls return `401` and pages redirect to `/login`.

| Method | Path | Does |
|---|---|---|
| `GET` | `/` | Dashboard (`/?demo` shows simulated data) |
| `GET` | `/login` | Login page |
| `POST` | `/api/login` | `{"username", "password"}` → sets the session cookie |
| `GET` | `/logout` | Signs out |
| `GET` | `/healthz` | `{"ok": true}`, no login needed (used by the Docker health check) |
| `GET` | `/api/state` | Everything the dashboard shows: every device's metrics, history, containers, Pi-hole, Ollama and power state, plus `energy` (watts now, kWh and cost today and this month, 30-day history) |
| `POST` | `/api/devices/{id}/containers/{name}/{action}` | `start`, `stop` or `restart` a container (device must be `docker: control`) |
| `GET` | `/api/devices/{id}/containers/{name}/logs?tail=300` | Container logs as plain text |
| `POST` | `/api/devices/{id}/power/{action}` | `on` (Wake-on-LAN), `reboot` or `shutdown` |
| `POST` | `/api/devices/{id}/ollama/{action}` | `load` or `unload`, body `{"model": "llama3.1:8b"}` |
| `POST` | `/api/chat` | `{"model", "messages": [{role, content}]}` → streams Ollama's reply as NDJSON, with live status included |
| `POST` | `/api/energy/rate` | `{"rate": 0.32}` sets the electricity rate per kWh |
| `GET` | `/api/summary/send` | Posts a daily summary to Discord now (for testing) |
| `GET` | `/api/brain` | The Brain: notes and topics as a graph, drafts waiting for review, recent activity |
| `POST` | `/api/brain/recall` | `{"query"}` → notes that match, which ones fit the context budget, and why each matched |
| `GET` | `/api/brain/note?path=` | One note in full, by vault path or title |
| `POST` | `/api/brain/notes` | `{"title", "content", "topic"}` saves a memory directly |
| `POST` | `/api/brain/inbox/{id}/{action}` | `approve` (optional edits: `title`, `content`, `topic`, `links`) or `discard` a model's draft |
| `POST` | `/mcp` | MCP server (Streamable HTTP, JSON replies) for LLM clients. Uses `Authorization: Bearer <brain.mcp_token>`, not the session cookie. Tools: `search_memory`, `read_memory`, `list_memory_topics`, `save_memory` |
| `GET` | `/manifest.webmanifest` | Web app manifest for "Add to Home Screen" |

## Agent (port 9101)

Every endpoint except `/health` needs the header `Authorization: Bearer <token>`. If `NEXUSLAB_TOKEN` is empty the agent refuses those requests with `503` instead of serving them unauthenticated.

| Method | Path | Does |
|---|---|---|
| `GET` | `/health` | `{"ok": true}` |
| `GET` | `/metrics` | CPU, memory, disk, temperatures, GPUs, network card, Wake-on-LAN status, and whether power and Ollama are available |
| `GET` | `/containers` | Containers with status, health, CPU and memory |
| `POST` | `/containers/{name}/{action}` | `start`, `stop`, `restart` (only in `control` mode) |
| `GET` | `/containers/{name}/logs?tail=200` | Logs as plain text |
| `POST` | `/power/{action}` | `reboot` or `shutdown` (unless `NEXUSLAB_POWER=off`) |
| `GET` | `/ollama` | Ollama version, installed models and loaded models |
| `POST` | `/ollama/{action}` | `load` or `unload`, body `{"model": "..."}` |
| `POST` | `/ollama/chat` | Passes a chat request to Ollama and streams the reply |

### Example

```bash
TOKEN=$(sudo grep TOKEN /etc/nexuslab-agent.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:9101/metrics | python3 -m json.tool
```
