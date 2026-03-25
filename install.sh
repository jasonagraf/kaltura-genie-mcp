#!/bin/bash
# Kaltura Genie MCP — one-time install script
# Run this from the genie_mcp folder: bash install.sh

set -e

PYTHON="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing Genie MCP dependencies with Python 3.12..."
$PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt" -q

echo ""
echo "✅ Done! Now add the following block to your Claude Desktop config:"
echo "   ~/Library/Application Support/Claude/claude_desktop_config.json"
echo ""
echo '  "kaltura-genie": {'
echo '    "command": "'"$PYTHON"'",'
echo '    "args": ["'"$SCRIPT_DIR/server.py"'"],'
echo '    "env": {'
echo '      "GENIE_KS": "PASTE_YOUR_KS_HERE",'
echo '      "GENIE_URL": "https://genie.nvp1.ovp.kaltura.com/assistant/converse"'
echo '    }'
echo '  }'
echo ""
echo "Then quit and reopen Claude Desktop."
