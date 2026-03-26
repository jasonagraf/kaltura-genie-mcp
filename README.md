# Kaltura Genie MCP

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Claude Desktop to [Kaltura Genie](https://knowledge.kaltura.com/help/kaltura-genie), Kaltura's AI-powered video search assistant.

Ask Claude natural-language questions about your Kaltura video library. Genie handles all the RAG search and LLM processing — returning structured flashcard answers with video clip timestamps and sources. Claude passes the result through faithfully.

---

## What it does

When connected, you can ask Claude things like:

> *"How do manual and rule-based playlists differ?"*
> *"What are the main features in Content Lab?"*
> *"Summarize what was discussed in last week's Open Kaltura session."*

Genie returns structured **flashcards**, each with:
- A title and explanation
- Video clip references with precise timestamps (e.g. `1_0aip7ru9  7:01 – 7:13`)
- A sources list of the Kaltura entries Genie drew from

---

## Requirements

- macOS with [Claude Desktop](https://claude.ai/download) installed
- Python 3.10+ (3.12 recommended)
- A Kaltura account with Genie access
- Auth credentials (see below)

---

## Auth modes

### Mode A — Static KS (personal / development)

Copy a KS token directly from your browser and paste it into the config. Simple to set up; the token expires every 24 hours, so you'll need to refresh it periodically.

### Mode B — Enterprise / programmatic (recommended for teams)

IT deploys a shared config containing only org-level credentials (Partner ID, Admin Secret, Genie ID). No user identity lives in the config file. Each user runs the `genie_set_user` tool once after installing — the server stores their userId locally and generates a scoped KS on their behalf for every request, auto-refreshing every hour.

**This is the recommended mode for any deployment beyond personal use.**

---

## Installation

**1. Clone the repo**

```bash
git clone https://github.com/YOUR_USERNAME/kaltura-genie-mcp.git
cd kaltura-genie-mcp
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

Or run the included helper script:

```bash
bash install.sh
```

**3. Configure auth**

Pick one mode:

#### Mode A — Static KS

1. Open your MediaSpace site and navigate to the Genie chat page
2. Open browser DevTools → Network tab
3. Ask Genie any question
4. Find the request to `genie.nvp1.ovp.kaltura.com/assistant/converse`
5. Copy the `Authorization` header value — it looks like `KS djJ8NDk2...`
6. Use only the token part (everything after `KS `)

> **Note:** Tokens expire within 24 hours. When you get a 401 error, grab a fresh token.

#### Mode B — Enterprise (programmatic)

IT needs three values to populate the shared config:

| Value | Where to find it |
|---|---|
| `KALTURA_PARTNER_ID` | KMC → Settings → Integration Settings |
| `KALTURA_ADMIN_SECRET` | KMC → Settings → Integration Settings |
| `GENIE_ID` | KMS Admin → genieai module → Genie ID field (numeric, e.g. `295190462`) |

`KALTURA_USER_ID` is **not** in the config. Each user registers themselves after installation using the `genie_set_user` tool (see step 5 below).

The `GENIE_ID` is a per-KMS-site identifier that routes the Kaltura Session to the correct knowledge base. It is passed as the `genieid:<id>` KS privilege. Without it, programmatically-generated sessions hit an empty knowledge base.

**4. Add to Claude Desktop config**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and add the following inside the `"mcpServers"` block:

**Mode A:**
```json
"kaltura-genie": {
  "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
  "args": ["/path/to/kaltura-genie-mcp/server.py"],
  "env": {
    "GENIE_KS":  "YOUR_KS_TOKEN_HERE",
    "GENIE_URL": "https://genie.nvp1.ovp.kaltura.com/assistant/converse"
  }
}
```

**Mode B (IT deploys this — same file for everyone in the org):**
```json
"kaltura-genie": {
  "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
  "args": ["/path/to/kaltura-genie-mcp/server.py"],
  "env": {
    "KALTURA_PARTNER_ID":   "YOUR_PARTNER_ID",
    "KALTURA_ADMIN_SECRET": "YOUR_ADMIN_SECRET",
    "GENIE_ID":             "YOUR_GENIE_ID",
    "GENIE_URL":            "https://genie.nvp1.ovp.kaltura.com/assistant/converse"
  }
}
```

Replace `/path/to/kaltura-genie-mcp/server.py` with the actual path on the machine.

**5. Restart Claude Desktop, then (Mode B only) register your user identity**

Quit and reopen Claude Desktop. Then, in Claude, say:

> *"Set up my Kaltura Genie access with user ID jane.doe@example.com"*

Claude will call `genie_set_user`, which writes your Kaltura userId to `~/.kaltura_genie_user`. This is a one-time step per machine. After that, every Genie request automatically generates a session scoped to you — no tokens to copy, no 401s to worry about.

---

## Tools

### `genie_query`

Ask Genie a new question.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `question` | string | required | Natural-language question |
| `text_mode` | bool | `false` | Return plain text instead of flashcards |
| `markdown_output` | bool | `true` | Return pre-rendered markdown (recommended) |
| `model_type` | string | `"fast"` | `"fast"` or `"quality"` |

### `genie_followup`

Ask a follow-up within an existing conversation thread.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `question` | string | required | Follow-up question |
| `thread_id` | string | required | `thread_id` from a previous `genie_query` response |
| `text_mode` | bool | `false` | Return plain text instead of flashcards |
| `markdown_output` | bool | `true` | Return pre-rendered markdown (recommended) |
| `model_type` | string | `"fast"` | `"fast"` or `"quality"` |

### `genie_set_user`

Register your Kaltura userId on this machine. Enterprise mode only — run once per machine.

| Parameter | Type | Description |
|---|---|---|
| `user_id` | string | Your Kaltura userId, typically your work email (e.g. `jane.doe@example.com`) |

The userId is stored in `~/.kaltura_genie_user` and used for all subsequent KS generation. It never appears in the shared config file.

---

## Output format

By default (`markdown_output=true`), responses are pre-rendered markdown that Claude displays verbatim:

```
**Flashcard 1 — Manual vs. Rule-Based Playlists**
Manual and rule-based playlists are two distinct approaches...

**Flashcard 2 — Manual Playlists: Control and Effort**
Manual playlists require content to be individually added...
- `1_0aip7ru9`  7:01 – 7:13
- `1_0aip7ru9`  7:35 – 8:04

---
**Sources**
- **Open Kaltura #48: Podcasting and Kaltura** — `1_0aip7ru9` (1:01:33)
```

---

## Project structure

```
kaltura-genie-mcp/
├── server.py          # MCP server (FastMCP + Genie API client)
├── requirements.txt   # Python dependencies
├── install.sh         # Helper install script
└── README.md
```

---

## How it works

1. Claude calls `genie_query` with the user's question
2. The server resolves a valid KS:
   - **Mode A:** returns `GENIE_KS` directly
   - **Mode B:** reads the userId from `~/.kaltura_genie_user`, generates a KS via the Kaltura session API using admin credentials + the `genieid:<GENIE_ID>` privilege, and caches it for ~55 minutes
3. The server posts to `https://genie.nvp1.ovp.kaltura.com/assistant/converse` with `Authorization: KS <token>`
4. Genie returns a newline-delimited JSON (NDJSON) stream containing flashcard YAML, source metadata, and video clip citations
5. The server parses and assembles this into structured flashcards with pre-converted timestamps
6. With `markdown_output=true`, the result is pre-rendered as a markdown string — Claude outputs it as-is

### Why `GENIE_ID` matters

Kaltura's Genie service hosts multiple knowledge bases (one per KMS site). The `genieid:<id>` KS privilege tells Genie which knowledge base to search. Without it, programmatically-generated sessions are routed to an empty default — resulting in "I couldn't find an answer" responses. The Genie ID is specific to each KMS installation and is found in the KMS admin console under the genieai module settings.

### Why `KALTURA_USER_ID` is not in the config

In an enterprise deployment, the config file is identical for every user in the org — there's no personal information in it. Each user's identity is stored locally on their own machine (in `~/.kaltura_genie_user`) and set via the `genie_set_user` tool. This means IT can distribute or manage the config centrally without needing to know or handle individual user IDs, and users' identities are never exposed in a shared configuration file.

---

## License

MIT
