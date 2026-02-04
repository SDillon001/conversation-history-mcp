# Conversation History MCP Server

A local MCP server that indexes and searches your Claude Code conversation history with both keyword and semantic search.

## Features

- **Keyword Search** - Fast full-text search using SQLite FTS5
- **Semantic Search** - Find conceptually similar conversations using local embeddings
- **Session Listing** - Browse recent sessions with summaries
- **Context Retrieval** - Get full context from any past conversation
- **Fully Private** - All data stays local, no external API calls

## Installation

### Prerequisites

- Python 3.10+
- Claude Code CLI

### Quick Install

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/conversation-history-mcp.git
cd conversation-history-mcp

# Install dependencies
pip install -r requirements.txt

# Create symlink to Claude's MCP directory
mkdir -p ~/.claude/mcp-servers
ln -s "$(pwd)" ~/.claude/mcp-servers/conversation-history

# Add to Claude Code settings
# See Configuration section below
```

### Configuration

Add to your `~/.claude.json` under the `mcpServers` key:

```json
{
  "mcpServers": {
    "conversation-history": {
      "type": "stdio",
      "command": "python3",
      "args": ["server.py"],
      "cwd": "/path/to/conversation-history-mcp"
    }
  }
}
```

Replace `/path/to/conversation-history-mcp` with the actual path to your installation.

## Usage

Once configured, the following tools are available in any Claude Code session:

### search_sessions

Keyword search across all conversation history.

```
"Search for conversations about authentication"
→ Claude calls search_sessions(query="authentication")
```

### search_semantic

Semantic search to find conceptually similar conversations.

```
"Find conversations similar to database optimization"
→ Claude calls search_semantic(query="database optimization")
```

### list_sessions

List recent sessions with summaries.

```
"What did I work on last week?"
→ Claude calls list_sessions(days=7)
```

### get_session

Get full context from a specific session.

```
"Show me the full conversation from session abc123"
→ Claude calls get_session(session_id="abc123")
```

### rebuild_index

Force rebuild the search index (usually not needed).

```
"Rebuild the conversation index"
→ Claude calls rebuild_index()
```

## How It Works

1. **Indexing** - On startup, the server scans `~/.claude/projects/` for JSONL session files
2. **FTS5** - Messages are indexed in SQLite with full-text search
3. **Embeddings** - Using `sentence-transformers/all-MiniLM-L6-v2` (runs locally)
4. **Incremental** - Only new sessions are indexed after first run

## Data Storage

```
~/.claude/mcp-servers/conversation-history/
└── data/
    ├── index.db      # SQLite with FTS5 (keyword search)
    └── chroma/       # ChromaDB (semantic search embeddings)
```

Your conversation history stays in `~/.claude/projects/` - this server only creates a search index.

## Privacy

- **No external API calls** - Embeddings generated locally
- **No data leaves your machine** - Everything stays in `~/.claude/`
- **Read-only access** - Server only reads session files, never modifies them

## Development

```bash
# Run server directly for testing
python server.py

# Run tests
pytest tests/
```

## License

MIT
