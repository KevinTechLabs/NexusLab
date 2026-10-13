# Configuration

NexusLab has two places for settings:

- **Hub:** `hub/config.yaml` on the hub machine. Restart with `docker compose restart` after changes.
- **Agent:** `/etc/nexuslab-agent.env` on each machine. Restart with `sudo systemctl restart nexuslab-agent` after changes.

A complete, commented example is in [`hub/config.example.yaml`](../hub/config.example.yaml).

## Hub: `config.yaml`

### General

| Key | Default | Meaning |
|---|---|---|
| `poll_seconds` | `3` | How often CPU, memory, disk and GPU refresh |
| `container_poll_seconds` | `10` | How often containers, Pi-hole and Ollama refresh |
| `history_points` | `120` | Length of the history chart (120 × 3 s = 6 minutes) |

### `auth`

| Key | Default | Meaning |
|---|---|---|
| `username` | `admin` | Login name |
| `password` | *(empty)* | Login password. Empty turns the login **off**, which isn't recommended |
| `session_days` | `30` | How long a sign-in lasts. Changing the password signs everyone out |

### `devices` (one entry per machine)

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Short unique id used in URLs, like `pi` |
| `name` | no | Name shown in the dashboard |
| `url` | yes | Agent address, like `http://192.168.1.12:9101` |
| `token` | yes | The token the agent's installer printed |
| `docker` | no | `off`, `monitor` or `control`. Must match what the agent allows |
| `gpu` | no | `true` shows the GPU section |
| `mac` | no | MAC address for Start (Wake-on-LAN). Normally learned automatically |
| `pihole` | no | Pi-hole connection; see below |

| `power_meter` | no | Shelly smart plug for exact power; see below |
| `power` | no | Tune the power estimate; see below |

#### `pihole`

| Key | Default | Meaning |
|---|---|---|
| `url` | | Pi-hole web address, like `http://192.168.1.12` |
| `version` | `6` | `6` or `5` |
| `password` | | v6 web password or app password |
| `api_token` | | v5 API token |
| `verify_tls` | `false` | Check HTTPS certificates |

### `energy` and power per device

| Key | Default | Meaning |
|---|---|---|
| `energy.rate_per_kwh` | `0` | Your electricity price per kWh. A rate saved on the Power tab overrides this |
| `energy.currency` | `$` | Currency symbol |
| `power_meter.url` | | Address of a Shelly plug (Gen1, Plus or Gen3) the machine is plugged into. Its readings are marked **Measured** |
| `power_meter.channel` | `0` | Outlet number on multi-outlet Shelly devices |
| `power_meter.username` / `password` | | Only if the plug has a login |
| `power.overhead_w` | `6`, or `25` with a GPU | Watts for the parts without a sensor: board, memory, drives, fans |
| `power.efficiency` | `0.87` | Power supply efficiency |
| `power.idle_w` / `max_w` | `10` / `45` | Used only when a machine has no power sensor at all: the estimate scales between these with CPU load |

**How estimates work.** The agent reads the CPU's own power counter (RAPL on Intel and AMD) or, on a Raspberry Pi 5, its power chip. NVIDIA GPUs report their own draw. The hub adds `overhead_w` and divides by `efficiency` to get watts at the wall, then adds that up every few seconds into kWh per day, stored in `hub/data/energy.json`. Expect estimates to be within about 15%; a Shelly plug gives exact numbers. Machines that are off count as 0 W, and time while the hub itself is down isn't counted.

### `alerts`

| Key | Default | Meaning |
|---|---|---|
| `discord_webhook` | *(empty)* | Discord webhook URL. Empty turns alerts off |
| `device_offline` | `true` | Alert when a machine stops answering |
| `offline_after_seconds` | `60` | How long before a machine counts as offline |
| `high_temp` | `true` | Alert on high CPU or GPU temperature |
| `cpu_temp_c` | `85` | CPU alert level in °C |
| `gpu_temp_c` | `83` | GPU alert level in °C |
| `temp_sustain_seconds` | `60` | How long it must stay hot before alerting |
| `container_stopped` | `true` | Alert when a running container stops |
| `message_on_start` | `true` | Post "NexusLab is running" whenever the hub starts |
| `daily_summary` | `"08:00"` | Time of the daily summary (24-hour). `""` turns it off |
| `timezone` | `America/Los_Angeles` | Time zone for the summary and times in messages |

Alerts don't fire for actions you take from the dashboard. Stopping a container or rebooting a machine from NexusLab is expected, and a machine you shut down is shown as **Off** rather than offline.

### `chat`

| Key | Default | Meaning |
|---|---|---|
| `owner` | *(empty)* | Your name, used by the assistant ("Kevin's homelab") |
| `model` | *(empty)* | Default model. Empty uses whichever model is loaded, or the first installed |
| `num_ctx` | `8192` | Context window sent to Ollama. Raise it if answers seem to ignore data |

### `brain`

Long-term memory for your LLMs. See [BRAIN.md](BRAIN.md) for connecting Open WebUI.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Show the Brain tab and serve the memory API |
| `vault` | `data/vault` | Folder holding the Obsidian vault. Point it at an existing vault to use that instead |
| `mcp_token` | *(empty)* | Bearer token your LLM clients use at `/mcp`. Empty turns MCP off |
| `recall_chars` | `8000` | Most note text a single recall returns, so small models aren't flooded |
| `notify_discord` | `true` | Post to Discord when a model proposes a memory (needs `alerts.discord_webhook`) |
| `mcp_allowed_origins` | `[]` | Browser origins allowed to call `/mcp`. Server-side clients like Open WebUI send none and don't need this |

## Agent: `/etc/nexuslab-agent.env`

| Variable | Default | Meaning |
|---|---|---|
| `NEXUSLAB_TOKEN` | *(generated)* | Shared secret; the hub must send it |
| `NEXUSLAB_PORT` | `9101` | Port the agent listens on |
| `NEXUSLAB_DOCKER` | `off` | `off`, `monitor` or `control`. The agent enforces this itself |
| `NEXUSLAB_CONTAINERS` | *(all)* | Comma-separated list of container names to show |
| `NEXUSLAB_DISK_PATH` | `/` | Which filesystem to report as "SSD" |
| `NEXUSLAB_TEMP_SENSOR` | *(auto)* | Force a CPU sensor name, like `k10temp`. Run `sensors` to list them |
| `NEXUSLAB_POWER` | `on` | Allow Reboot and Shut down from the dashboard |
| `NEXUSLAB_WOL` | `on` | Switch Wake-on-LAN on for the wired card at every boot (needs `ethtool`) |
| `NEXUSLAB_OLLAMA_URL` | `http://127.0.0.1:11434` | Where to find Ollama. `off` disables it |

## Files the hub writes

`hub/data/vault/` is the Brain's Obsidian vault (unless `brain.vault` points elsewhere). Back it up; it's your models' memory. Drafts waiting for review are in its hidden `.nexus/inbox/` folder.

`hub/data/state.json` stores each machine's learned MAC address and which machines you turned off on purpose, so both survive hub restarts. It's safe to delete; it's rebuilt as machines report in.
