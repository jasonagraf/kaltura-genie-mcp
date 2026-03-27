#!/bin/bash
# Kaltura Genie MCP — one-time install script
# Run this from the genie_mcp folder: bash install.sh

set -e

PYTHON="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing Genie MCP dependencies with Python 3.12..."
$PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt" -q

echo ""
echo "✅ Dependencies installed!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Add ONE of the following blocks to your Claude Desktop config:"
echo "  ~/Library/Application Support/Claude/claude_desktop_config.json"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "── Mode A: Static KS (personal / development) ──────────"
echo ""
echo '  "kaltura-genie": {'
echo '    "command": "'"$PYTHON"'",'
echo '    "args": ["'"$SCRIPT_DIR/server.py"'"],'
echo '    "env": {'
echo '      "GENIE_KS":  "PASTE_YOUR_KS_TOKEN_HERE",'
echo '      "GENIE_URL": "https://genie.nvp1.ovp.kaltura.com/assistant/converse"'
echo '    }'
echo '  }'
echo ""
echo "  Get your KS token: open your MediaSpace Genie page, open browser"
echo "  DevTools → Network, ask any question, copy the Authorization header"
echo "  value (everything after 'KS ')."
echo ""
echo "── Mode B: Enterprise / programmatic (recommended for teams) ──"
echo ""
echo '  "kaltura-genie": {'
echo '    "command": "'"$PYTHON"'",'
echo '    "args": ["'"$SCRIPT_DIR/server.py"'"],'
echo '    "env": {'
echo '      "KALTURA_PARTNER_ID":   "YOUR_PARTNER_ID",'
echo '      "KALTURA_ADMIN_SECRET": "YOUR_ADMIN_SECRET",'
echo '      "GENIE_ID":             "YOUR_GENIE_ID",'
echo '      "GENIE_URL":            "https://genie.nvp1.ovp.kaltura.com/assistant/converse"'
echo '    }'
echo '  }'
echo ""
echo "  KALTURA_PARTNER_ID / KALTURA_ADMIN_SECRET: KMC → Settings → Integration Settings"
echo "  GENIE_ID: KMS Admin → genieai module → Genie ID field (numeric)"
echo ""
echo "  After restarting Claude Desktop, each user runs this once in Claude:"
echo "    \"Set up my Kaltura Genie access with user ID your@email.com\""
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Then quit and reopen Claude Desktop."
