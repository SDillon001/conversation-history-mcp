#!/bin/bash
# Conversation History MCP Server - Installation Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$HOME/.claude/mcp-servers"
DATA_DIR="$MCP_DIR/conversation-history/data"
SETTINGS_FILE="$HOME/.claude/settings.json"

echo "Installing Conversation History MCP Server..."

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Found Python $PYTHON_VERSION"

# Install dependencies
echo "Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"

# Create MCP servers directory
mkdir -p "$MCP_DIR"

# Create symlink
if [ -L "$MCP_DIR/conversation-history" ]; then
    echo "Removing existing symlink..."
    rm "$MCP_DIR/conversation-history"
fi

echo "Creating symlink..."
ln -s "$SCRIPT_DIR" "$MCP_DIR/conversation-history"

# Create data directory
mkdir -p "$DATA_DIR"

# Update Claude Code settings
echo ""
echo "To complete installation, add this to your ~/.claude/settings.json:"
echo ""
echo '  "mcpServers": {'
echo '    "conversation-history": {'
echo '      "command": "python3",'
echo "      \"args\": [\"$SCRIPT_DIR/server.py\"]"
echo '    }'
echo '  }'
echo ""

# Check if settings file exists
if [ -f "$SETTINGS_FILE" ]; then
    echo "Your current settings.json exists. You can edit it with:"
    echo "  code $SETTINGS_FILE"
    echo "  # or"
    echo "  nano $SETTINGS_FILE"
else
    echo "No settings.json found. Create one with the above content."
fi

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Add the MCP server config to ~/.claude/settings.json"
echo "2. Restart Claude Code"
echo "3. Try: 'Search my conversation history for authentication'"
