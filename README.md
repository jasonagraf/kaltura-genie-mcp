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
- A Kaltura MediaSpace account with Genie access
- A valid Kaltura Session (KS) token from your MediaSpace Genie page

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

**3. Get your Kaltura Session (KS) token**

The KS token must come from your **MediaSpace Genie page** — not from the KMC admin console. The MediaSpace KS routes to your specific Genie knowledge base.

To grab it:
1. Open your MediaSpace site and navigate to the Genie chat page
2. Open browser DevTools → Network tab
3. Ask Genie any question
4. Find the request to `genie.nvp1.ovp.kaltura.com/assistant/converse`
5. Copy the value of the `Authorization` header — it looks like `KS djJ8NDk2...`
6. Use only the token part (everything after `KS `)

> **Note:** MediaSpace KS tokens expire (typically within 24 hours). If you get a 401 error, grab a fresh token using the steps above.

**4. Add to Claude Desktop config**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and add the following inside the `"mcpServers"` block:

```json
"kaltura-genie": {
  "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
  "args": ["/path/to/kaltura-genie-mcp/server.py"],
  "env": {
    "GENIE_KS": "YOUR_KS_TOKEN_HERE",
    "GENIE_URL": "https://genie.nvp1.ovp.kaltura.com/assistant/converse"
  }
}
```

Replace `/path/to/kaltura-genie-mcp/server.py` with the actual path on your machine, and paste your KS token.

**5. Restart Claude Desktop**

Quit and reopen Claude Desktop. You should see `kaltura-genie` listed under connected integrations.

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
2. The server posts to `https://genie.nvp1.ovp.kaltura.com/assistant/converse` with `Authorization: KS <token>`
3. Genie returns a newline-delimited JSON (NDJSON) stream containing flashcard YAML, source metadata, and video clip citations
4. The server parses and assembles this into structured flashcards with pre-converted timestamps
5. With `markdown_output=true`, the result is pre-rendered as a markdown string — Claude outputs it as-is

---

## License

MIT
