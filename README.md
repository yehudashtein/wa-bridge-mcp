# wa-bridge-mcp

A small **Model Context Protocol (MCP) server** that lets an AI assistant (Claude
Desktop, Claude Code, etc.) read and send WhatsApp messages through the
**api-supp WhatsApp bridge**.

It's an HTTP client only — every operation goes through the bridge's REST API, so
there's no local database, no QR pairing here, and nothing sensitive stored on
your machine. You authenticate with a **tenant token** that scopes the MCP to
*your own* WhatsApp account.

> This is a fork of [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp)
> (MIT), re-worked to talk to a hosted bridge over HTTP. See `NOTICE`.

## What you need

- **Python 3.11+** (and [`uv`](https://docs.astral.sh/uv/) recommended)
- Your **bridge URL** (default `https://api.api-supp.com/v1`) and a **tenant token**
  (`wat_…`) — provided to you during onboarding.

## Install

```bash
git clone https://github.com/yehudashtein/wa-bridge-mcp.git
cd wa-bridge-mcp
uv sync        # or: pip install -e .
```

## Configure

The MCP reads its settings from environment variables:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `WHATSAPP_BRIDGE_TOKEN` | ✅ | — | Your tenant bearer (`wat_…`) — keep it secret |
| `WHATSAPP_BRIDGE_URL` | — | `https://api.api-supp.com/v1` | Bridge base URL (include `/v1`) |
| `WHATSAPP_HTTP_TIMEOUT` | — | `30` | Per-request timeout (seconds) |

Copy `.env.example` → `.env` for local runs, or set them in your MCP client config.

### Claude Desktop / Claude Code

Add to your MCP config (e.g. `claude_desktop_config.json` or `.claude.json`):

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/wa-bridge-mcp", "run", "main.py"],
      "env": {
        "WHATSAPP_BRIDGE_URL": "https://api.api-supp.com/v1",
        "WHATSAPP_BRIDGE_TOKEN": "wat_your_tenant_token_here"
      }
    }
  }
}
```

Restart the client and the WhatsApp tools appear.

## Tools

| Tool | What it does |
|---|---|
| `search_contacts` | Search your contacts by name or number |
| `list_chats` | List chats (with last message) |
| `get_chat` / `get_contact_chats` / `get_direct_chat_by_contact` | Look up chats |
| `list_messages` | Read messages with filters (chat, time, sender, media, …) |
| `get_message_context` | Messages around a specific one |
| `get_last_interaction` | Most recent message with a contact |
| `send_message` | Send a text message |
| `send_file` / `send_audio_message` | Send media / voice notes |
| `download_media` | Download media from a message |
| `sync_history` | Ask the bridge to re-sync history |

## Notes

- The token scopes you to **your own** WhatsApp account; it can't see other users' data.
- Non-ASCII text (Hebrew/Arabic/emoji) sends fine — the bridge handles encoding.
- Nothing is stored locally; the MCP is a thin HTTP client.

## License

MIT — see [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE) (derivative of
`lharries/whatsapp-mcp`).
