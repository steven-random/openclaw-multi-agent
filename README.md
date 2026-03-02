# openclaw-multi-agent

A production-grade, per-channel Slack multi-agent system built on [OpenClaw](https://openclaw.dev) native capabilities — no custom routing code, no glue layer.

Each Slack channel gets its own isolated AI agent with independent long-term memory. A local [OpenAI Codex](https://openai.com/codex) proxy bridges the model. A PostgreSQL plugin lets you grant DB access to specific agents only.

```
#email-agent  ──→  Email Agent  ──→  Yahoo Mail IMAP/SMTP
#stock-agent  ──→  Stock Agent  ──→  Web Search + Analysis
#your-agent   ──→  Your Agent   ──→  PostgreSQL (optional, per-agent)
      ↕                 ↕
  OpenClaw          codex-proxy
  Gateway            (:9999)
  (:18789)              ↕
                    codex exec CLI
```

---

## Features

- **Per-channel agent isolation** — each Slack channel routes to a dedicated agent with its own workspace, sessions, and memory SQLite database
- **Zero cross-channel leakage** — routing (bindings), state (sessions dir), and memory (per-agent SQLite) are all physically isolated
- **Long-term memory** — local GGUF embedding model (embeddinggemma-300m, fully offline, ~330MB)
- **OpenAI Codex via ChatGPT Plus** — `codex exec` CLI bridged through a local FastAPI SSE proxy
- **Selective DB access** — `db_query` PostgreSQL tool registered as `optional`; only agents that explicitly list it in `tools.allow` can see it
- **Email operations** — full IMAP/SMTP agent: list, search, send, move, flag
- **Stock analysis** — real-time web search + LLM reasoning for US market
- **systemd-managed** — both services run as user units, auto-restart on failure

---

## Architecture

```
Slack Workspace (single Bot, Socket Mode)
│
├── #email-agent  ──→  Agent "email"
│                      workspace:  ~/claw-email/
│                      memory:     ~/.openclaw/memory/email.sqlite
│                      tools:      exec(allowlist) + fs(workspaceOnly)
│                                  ✗ db_query  ✗ web_search
│
└── #stock-agent  ──→  Agent "stock"
                       workspace:  ~/claw-stock/
                       memory:     ~/.openclaw/memory/stock.sqlite
                       tools:      web_search + web_fetch + fs(workspaceOnly)
                                   ✗ exec  ✗ db_query
```

### Memory isolation (3 layers)

| Layer | Mechanism | Effect |
|-------|-----------|--------|
| **Routing** | `bindings[].match.peer.kind="channel" + id` | Messages only reach the bound agent |
| **State** | Per-agent `agentId` → separate sessions dir | No session history sharing |
| **Memory** | `~/.openclaw/memory/<agentId>.sqlite` | Vector search only on own SQLite |

### Per-agent tool control

Tools are filtered through this pipeline (each layer can only restrict, never grant back):

```
tools.profile  →  tools.allow/deny  →  agents.list[].tools.allow/deny  →  sandbox
```

The `db_query` plugin is registered with `{ optional: true }` — OpenClaw never auto-enables it.
Only agents with `"db_query"` in their `tools.allow` array can call it.

### codex-proxy

FastAPI bridge: OpenClaw's OpenAI-compatible calls → `codex exec` CLI

- Listens on `http://127.0.0.1:9999`
- Returns proper SSE (`text/event-stream`) for `stream: true` requests
- Detects agent identity from system prompt keywords → sets correct `cwd` for `codex exec`

---

## Project structure

```
openclaw-multi-agent/
├── README.md
├── .gitignore
│
├── codex-proxy/                       # FastAPI bridge: OpenAI API → codex exec
│   ├── proxy.py                       # SSE streaming + workspace detection
│   ├── requirements.txt               # fastapi, uvicorn
│   └── codex-proxy.service.example    # systemd unit template
│
├── agents/
│   ├── email/                         # Email agent workspace template
│   │   ├── AGENTS.md                  # Role + tool commands
│   │   ├── SOUL.md                    # Personality and behavior
│   │   ├── TOOLS.md                   # Tool permissions reference
│   │   ├── HEARTBEAT.md               # Scheduled inbox check
│   │   ├── BOOTSTRAP.md               # First-run guide
│   │   ├── IDENTITY.md                # Agent identity (fill on first run)
│   │   ├── USER.md                    # User profile (fill on first run)
│   │   └── scripts/
│   │       ├── email_ops.py           # Yahoo Mail IMAP/SMTP CLI tool
│   │       └── .env.example           # Credentials template (never commit .env)
│   │
│   └── stock/                         # Stock analyst workspace template
│       ├── AGENTS.md
│       ├── SOUL.md
│       ├── TOOLS.md
│       ├── HEARTBEAT.md
│       ├── IDENTITY.md
│       └── USER.md
│
├── plugins/
│   └── db-tool/                       # Optional per-agent PostgreSQL query tool
│       ├── index.ts                   # Tool impl (optional: true, read-only guard)
│       ├── openclaw.plugin.json       # Manifest + configSchema
│       ├── package.json               # pg dependency
│       └── package-lock.json
│
├── config/
│   └── openclaw.json.example          # Full config template (no secrets)
│
└── systemd/
    ├── openclaw-gateway.service.example
    └── codex-proxy.service.example
```

---

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| [OpenClaw](https://openclaw.dev) | 2026.2.26+ | `npm install -g openclaw` |
| [@openai/codex](https://www.npmjs.com/package/@openai/codex) | 0.106.0+ | `npm install -g @openai/codex` |
| Python | 3.10+ | For codex-proxy and email_ops |
| Node.js | 20+ | For OpenClaw gateway |
| Slack App | Socket Mode enabled | Bot + App token required |
| ChatGPT Plus | Active subscription | Used by `codex exec` CLI |
| PostgreSQL | Any | Only needed if using `db-tool` plugin |

---

## Installation

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/openclaw-multi-agent.git
cd openclaw-multi-agent
```

### 2. Install OpenClaw and Codex

```bash
npm install -g openclaw @openai/codex
```

### 3. Set up codex-proxy

```bash
cd codex-proxy
pip install -r requirements.txt
```

Edit `proxy.py` — replace `YOUR_USER` with your Linux username in `AGENT_WORKSPACES` and `DEFAULT_WORKSPACE`.

### 4. Set up agent workspaces

```bash
# Email agent
mkdir ~/claw-email
cp -r agents/email/* ~/claw-email/
cp agents/email/scripts/.env.example ~/claw-email/scripts/.env
nano ~/claw-email/scripts/.env      # fill in real credentials

# Stock agent
mkdir ~/claw-stock
cp -r agents/stock/* ~/claw-stock/

# Memory dirs (required by OpenClaw memory search)
mkdir -p ~/claw-email/memory ~/claw-stock/memory
```

### 5. Configure OpenClaw

```bash
mkdir -p ~/.openclaw
cp config/openclaw.json.example ~/.openclaw/openclaw.json
nano ~/.openclaw/openclaw.json       # fill in Slack tokens + channel IDs
```

### 6. Install db-tool plugin (optional)

Skip if you don't need per-agent database access.

```bash
mkdir -p ~/.openclaw/extensions/db-tool
cp -r plugins/db-tool/* ~/.openclaw/extensions/db-tool/
cd ~/.openclaw/extensions/db-tool && npm install --ignore-scripts
```

Fill in `plugins.entries.db-tool.config` in `openclaw.json` with your PostgreSQL connection:

```json
"db-tool": {
  "enabled": true,
  "config": {
    "host": "localhost",
    "port": 5432,
    "database": "mydb",
    "user": "myuser",
    "password": "mypassword",
    "ssl": false,
    "maxRows": 100
  }
}
```

Add `"allow": ["db-tool"]` to `plugins.allow` and grant the tool to specific agents:

```json
// Only this agent can call db_query:
{
  "id": "myagent",
  "tools": { "allow": ["db_query"], "fs": { "workspaceOnly": true } }
}
```

### 7. Install systemd services

```bash
mkdir -p ~/.config/systemd/user

# codex-proxy
sed "s/YOUR_USER/$USER/g" codex-proxy/codex-proxy.service.example \
  > ~/.config/systemd/user/codex-proxy.service

# openclaw-gateway (edit OPENCLAW_GATEWAY_TOKEN before enabling)
cp systemd/openclaw-gateway.service.example \
   ~/.config/systemd/user/openclaw-gateway.service
nano ~/.config/systemd/user/openclaw-gateway.service

systemctl --user daemon-reload
systemctl --user enable --now codex-proxy
systemctl --user enable --now openclaw-gateway
```

### 8. Verify

```bash
openclaw doctor
openclaw agents list --bindings
openclaw channels status --probe
openclaw plugins list
```

---

## Configuration reference

### `~/.openclaw/openclaw.json` — secrets (never commit)

| Key path | Description |
|----------|-------------|
| `channels.slack.botToken` | Slack Bot Token (`xoxb-...`) |
| `channels.slack.appToken` | Slack App Token (`xapp-...`), Socket Mode |
| `tools.web.search.apiKey` | Brave Search API key (stock agent) |
| `gateway.auth.token` | Local gateway token — `openssl rand -hex 24` |
| `plugins.entries.db-tool.config` | PostgreSQL connection details |

### `~/claw-email/scripts/.env` — secrets (never commit)

| Variable | Description |
|----------|-------------|
| `EMAIL_ADDRESS` | Yahoo Mail address |
| `EMAIL_APP_PASSWORD` | Yahoo App Password (not login password) |
| `IMAP_HOST` | Default: `imap.mail.yahoo.com` |
| `SMTP_HOST` | Default: `smtp.mail.yahoo.com` |

> **Yahoo App Password**: Account Security → Manage app passwords → Other app

---

## Slack App setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App
2. Enable **Socket Mode** → generate App-level token (`xapp-...`) with `connections:write` scope
3. Add Bot Token OAuth scopes: `chat:write`, `channels:read`, `groups:read`, `reactions:write`
4. Install to workspace → copy Bot Token (`xoxb-...`)
5. Invite the bot to each channel: `/invite @YourBotName`
6. Get channel IDs from channel URLs (the `C...` part after `/archives/`)

---

## Usage examples

### Email agent (`#email-agent`)

```
You: 列出最新5封邮件
Bot: 📬 INBOX 最新 5 封邮件: ...

You: 把所有验证码邮件移到 Verification 文件夹
Bot: ✅ 已将 23 封邮件移动到 Verification

You: 发邮件给 friend@example.com，主题：周末，正文：有空吗
Bot: ✅ 已发送邮件给 friend@example.com
```

### Stock agent (`#stock-agent`)

```
You: NVDA 最新财报分析
Bot: 📊 NVIDIA Q4 FY2025 — 营收 $39.3B (+78% YoY) ...

You: 你知道我在 #email-agent 里说的什么吗？
Bot: 我没有其他频道的记忆。（跨 channel 记忆完全隔离）
```

### Verifying memory isolation

```bash
# In #email-agent:   "请记住：我的幸运数字是 42"
# In #stock-agent:   "我的幸运数字是多少？"
# Expected: stock agent has no idea

# Physical proof — two separate SQLite files:
ls ~/.openclaw/memory/
# email.sqlite   stock.sqlite
```

---

## Adding a new channel agent

1. **Get the Slack channel ID** (starts with `C`)

2. **Create workspace**
   ```bash
   mkdir ~/claw-myagent && mkdir -p ~/claw-myagent/memory
   cp -r agents/email/* ~/claw-myagent/
   # Edit AGENTS.md to define the new agent's role
   ```

3. **Edit `~/.openclaw/openclaw.json`** — add 3 entries:

   ```json
   // agents.list[]
   {
     "id": "myagent",
     "workspace": "/home/YOUR_USER/claw-myagent",
     "model": { "primary": "codex/gpt-5.3-codex" },
     "tools": {
       "fs": { "workspaceOnly": true },
       "exec": { "security": "deny" }
       // Add "allow": ["db_query"] here to grant DB access to this agent only
     }
   }

   // bindings[]
   { "agentId": "myagent", "match": { "channel": "slack",
     "peer": { "kind": "channel", "id": "CNEWCHANNEL" } } }

   // channels.slack.channels
   "CNEWCHANNEL": { "requireMention": false }
   ```

4. **Reload and invite**
   ```bash
   openclaw gateway restart
   # In Slack: /invite @YourBotName
   ```

---

## Operations

```bash
# Status
systemctl --user status openclaw-gateway codex-proxy
openclaw doctor && openclaw plugins list

# Logs
journalctl --user -u openclaw-gateway -f
journalctl --user -u codex-proxy -f

# Hot reload (keeps Slack WebSocket alive)
openclaw gateway restart
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Bot doesn't reply | Socket Mode disconnected | `openclaw channels status --probe` |
| Empty reply | codex-proxy returning non-SSE | Check `journalctl --user -u codex-proxy` |
| `404` from proxy | Wrong endpoint | Proxy handles `/chat/completions` and `/v1/chat/completions` |
| OpenAI call fails | ChatGPT Plus session expired | `codex auth login` |
| Memory not persisting | SQLite missing | `openclaw memory status` |
| Agent replies to wrong channel | Binding wrong | `openclaw agents list --bindings` |
| `db_query` unavailable | Not in `tools.allow` | Add `"allow": ["db_query"]` to that agent's tools |
| ~30s response time | `codex exec` cold-start per request | See below |

### Performance: ~30s latency

`codex exec` spawns a new process + WebSocket handshake per call (~13s baseline).

| Mitigation | Latency saved | Effort |
|-----------|--------------|--------|
| Limit prompt to last 10 messages | 5–10s on long sessions | 1 line in `proxy.py` |
| Stream codex stdout in real-time | User sees first token at ~3s | 2–3h |
| Switch to OpenAI API directly | Drops to 3–8s total | Half day |

---

## License

MIT
