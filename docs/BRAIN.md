# Brain: memory for your LLMs

The Brain tab gives the models you run in Open WebUI a long-term memory. It's a plain [Obsidian](https://obsidian.md) vault of Markdown notes, drawn as a brain: each topic is a lobe and each memory a neuron.

- **Models recall** notes when a question depends on earlier conversations, your setup or your preferences.
- **Models propose** new memories. A proposal waits in the Brain's **Inbox** until you edit and approve it, or discard it. Nothing is written to the vault without you.
- **You can open the vault in Obsidian** and edit anything by hand. NexusLab reads the files fresh every time.

## How it's organised

```
vault/
  Start here.md          explains the layout
  Memory/                one note per fact, preference, decision or lesson
  MOCs/                  one "map of content" per topic, linking its notes
  .nexus/inbox/          drafts waiting for review (hidden from Obsidian)
```

Each note has a little front matter (`created`, `source`, `topic`) and links to related notes with `[[wiki links]]`. When a note is approved it's added to its topic's map of content, creating the topic if it's new.

## How recall works

A recall (from a model, or from the Recall panel) does what you'd do in Obsidian:

1. Reads every map of content and picks the topics that match the question.
2. Follows those topics' links to their notes.
3. Falls back to a keyword search across the whole vault.
4. Ranks the matches and fits the best ones into `brain.recall_chars` (8,000 by default), so an 8B model's context isn't flooded. Notes that don't fit are named, and the model can read them with `read_memory`.

Try it on the Recall panel to see exactly what a model would get.

## Connect Open WebUI

You need Open WebUI **0.6.31 or later** (native MCP support) on the machine that runs your models, and that machine must be able to reach the hub on port 8484 over your LAN or Tailscale.

**1. Give the Brain a token.** On the hub machine:

```bash
openssl rand -hex 24          # copy the output
nano hub/config.yaml          # under brain:, set mcp_token: "<that value>"
docker compose up -d --build  # rebuild: this release adds a file to the image
```

The hub log should say `[brain] ... MCP on at /mcp`. The Brain tab's chip changes to **LLMs connected (MCP)**.

**2. Add the server in Open WebUI.** Go to **Admin Settings → External Tools**, click **+**, and fill in:

| Field | Value |
|---|---|
| Type | MCP (Streamable HTTP) |
| URL | `http://<hub-ip>:8484/mcp` (use the hub's Tailscale IP if Open WebUI reaches it over Tailscale) |
| Auth | Bearer, with the token from step 1 |

Save. Open WebUI should list four tools: `search_memory`, `read_memory`, `list_memory_topics` and `save_memory`.

**3. Turn it on for your models.** In **Workspace → Models**, edit each model (for example Nexus and Nexus Coder):

- Under **Tools**, tick the NexusLab Brain server.
- Under **Advanced Params**, set **Function Calling** to **Native**.
- Add a line like this to the system prompt:

  > You have a long-term memory. Before answering anything that may depend on earlier conversations, my setup, projects or preferences, call search_memory. When you learn something worth keeping, call save_memory with one idea per memory.

**4. Test it.** In a new chat, tell the model something worth remembering ("My backups run nightly at 2am with Restic. Remember that."). A draft appears in the Brain's Inbox, with a badge on the Brain tab and a Discord message if alerts are set up. Approve it, start another chat, and ask "When do my backups run?". The Activity panel shows the model's lookup and lights up the notes it read.

Smaller models (around 8B) are less reliable at deciding when to call tools. The system prompt line above helps; so does asking directly ("check your memory").

## Open the vault in Obsidian

The vault lives on the hub machine at `hub/data/vault` (or wherever `brain.vault` points). Either:

- **Sync a copy to your computer** with [Syncthing](https://syncthing.net) (or any two-way file sync), then use **Open folder as vault** in Obsidian. Edits in either place show up in the other.
- **Point the Brain at a vault you already have.** Set `brain.vault` to that folder and mount it into the container in `hub/docker-compose.yml`, for example `- /srv/obsidian/MyVault:/vault` with `vault: /vault`. NexusLab only writes inside `Memory/`, `MOCs/` and `.nexus/`, and only when you approve something.

## Security

- `/mcp` doesn't use your dashboard login. It needs `Authorization: Bearer <brain.mcp_token>`, and it's off while the token is empty. Use a long random token and keep port 8484 off the internet.
- Requests carrying a browser `Origin` header are refused unless listed in `brain.mcp_allowed_origins`, so a web page can't reach the Brain through your browser.
- Models can only propose. Read drafts before approving: anything you approve will be fed back to your models later.
- Don't let models save passwords or keys. The `save_memory` tool tells them not to, but the review step is the real safeguard.

## Troubleshooting

| Problem | Fix |
|---|---|
| Open WebUI can't connect | From the Open WebUI machine, run `curl -X POST http://<hub-ip>:8484/mcp -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'`. `{"result":{}}` means the hub is fine; check the URL in Open WebUI. `404` means `mcp_token` isn't set or the hub wasn't restarted. `401` means the token doesn't match. |
| Open WebUI runs in Docker and can't reach the hub | Use the hub's LAN or Tailscale IP, not `localhost`. |
| The model never uses its memory | Check that the tool is ticked for that model, Function Calling is **Native**, and the system prompt mentions it. |
| Brain tab says "Brain unavailable" | `brain.enabled` is `false`, or the hub can't write to the vault folder. The hub log explains which. |
