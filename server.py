#!/usr/bin/env python3
"""
Conversation History MCP Server

An MCP server that provides search across Claude Code conversation history.
Supports both keyword (FTS5) and semantic (embedding) search.
"""

import os
import asyncio
import logging
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default paths
DEFAULT_DATA_DIR = os.path.expanduser("~/.claude/mcp-servers/conversation-history/data")
DEFAULT_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Lazy-loaded indexes
_search_index = None
_embedding_index = None


def get_search_index():
    """Lazy load the SQLite search index."""
    global _search_index
    if _search_index is None:
        from indexer import SearchIndex
        db_path = os.path.join(DEFAULT_DATA_DIR, "index.db")
        _search_index = SearchIndex(db_path)
    return _search_index


def get_embedding_index():
    """Lazy load the embedding index."""
    global _embedding_index
    if _embedding_index is None:
        from embeddings import EmbeddingIndex
        persist_dir = os.path.join(DEFAULT_DATA_DIR, "chroma")
        _embedding_index = EmbeddingIndex(persist_dir)
    return _embedding_index


def ensure_index_built():
    """Ensure the index has been built at least once."""
    from indexer import build_index, SearchIndex

    db_path = os.path.join(DEFAULT_DATA_DIR, "index.db")

    # Check if index exists and has data
    if os.path.exists(db_path):
        idx = SearchIndex(db_path)
        stats = idx.get_stats()
        idx.close()
        if stats["sessions"] > 0:
            return  # Index already has data

    # Build index
    logger.info("Building conversation index (first run)...")
    result = build_index(db_path, DEFAULT_PROJECTS_DIR)
    logger.info(f"Index built: {result}")


# Create MCP server
server = Server("conversation-history")


@server.list_tools()
async def list_tools():
    """List available tools."""
    return [
        Tool(
            name="search_sessions",
            description="Search conversation history using keywords. Uses full-text search (FTS5) for fast, accurate keyword matching.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords, phrases, or FTS5 syntax like 'word1 AND word2')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 20)",
                        "default": 20
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="search_semantic",
            description="Search conversation history using semantic similarity. Finds conceptually related content even if exact words don't match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what you're looking for"
                    },
                    "project": {
                        "type": "string",
                        "description": "Filter to a specific project (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 10)",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="list_sessions",
            description="List recent conversation sessions with their summaries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look back (default: 7)",
                        "default": 7
                    },
                    "project": {
                        "type": "string",
                        "description": "Filter to a specific project (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions (default: 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="get_session",
            description="Get the full conversation from a specific session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID to retrieve"
                    }
                },
                "required": ["session_id"]
            }
        ),
        Tool(
            name="rebuild_index",
            description="Force rebuild the conversation search index. Usually not needed as indexing is incremental.",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_embeddings": {
                        "type": "boolean",
                        "description": "Also rebuild semantic search embeddings (slower)",
                        "default": False
                    }
                }
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls."""

    if name == "search_sessions":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 20)

        index = get_search_index()
        results = index.search(query, limit=limit)

        if not results:
            return [TextContent(type="text", text="No results found.")]

        output = f"Found {len(results)} results:\n\n"
        for r in results:
            output += f"**Session:** {r['session_id']}\n"
            output += f"**Project:** {r['project']}\n"
            output += f"**Time:** {r['timestamp']}\n"
            output += f"**Role:** {r['role']}\n"
            output += f"**Content:** {r['content']}\n"
            output += "---\n\n"

        return [TextContent(type="text", text=output)]

    elif name == "search_semantic":
        query = arguments.get("query", "")
        project = arguments.get("project")
        limit = arguments.get("limit", 10)

        index = get_embedding_index()
        results = index.search(query, limit=limit, project=project)

        if not results:
            return [TextContent(type="text", text="No semantically similar content found.")]

        output = f"Found {len(results)} semantically similar results:\n\n"
        for r in results:
            output += f"**Session:** {r.session_id}\n"
            output += f"**Project:** {r.project}\n"
            output += f"**Time:** {r.timestamp}\n"
            output += f"**Similarity:** {1 - r.distance:.2%}\n"
            output += f"**Content:** {r.content}\n"
            output += "---\n\n"

        return [TextContent(type="text", text=output)]

    elif name == "list_sessions":
        days = arguments.get("days", 7)
        project = arguments.get("project")
        limit = arguments.get("limit", 50)

        index = get_search_index()
        sessions = index.list_sessions(days=days, project=project, limit=limit)

        if not sessions:
            return [TextContent(type="text", text=f"No sessions found in the last {days} days.")]

        output = f"Found {len(sessions)} sessions:\n\n"
        for s in sessions:
            output += f"**Session:** {s['session_id']}\n"
            output += f"**Project:** {s['project']}\n"
            output += f"**Summary:** {s['summary'] or '(no summary)'}\n"
            output += f"**Messages:** {s['message_count']}\n"
            output += f"**Last active:** {s['last_timestamp']}\n"
            output += "---\n\n"

        return [TextContent(type="text", text=output)]

    elif name == "get_session":
        session_id = arguments.get("session_id", "")

        index = get_search_index()
        messages = index.get_session_messages(session_id)

        if not messages:
            return [TextContent(type="text", text=f"Session '{session_id}' not found.")]

        output = f"Session {session_id} ({len(messages)} messages):\n\n"
        for m in messages:
            role = "User" if m["role"] == "user" else "Assistant"
            content = m["content"]
            if len(content) > 1000:
                content = content[:1000] + "... [truncated]"
            output += f"**{role}:** {content}\n\n"

        return [TextContent(type="text", text=output)]

    elif name == "rebuild_index":
        include_embeddings = arguments.get("include_embeddings", False)

        from indexer import build_index

        db_path = os.path.join(DEFAULT_DATA_DIR, "index.db")
        result = build_index(db_path, DEFAULT_PROJECTS_DIR, force=True)

        output = f"Index rebuilt:\n"
        output += f"- Files found: {result['files_found']}\n"
        output += f"- Files indexed: {result['files_indexed']}\n"
        output += f"- Sessions: {result['sessions']}\n"
        output += f"- Messages: {result['messages']}\n"

        if include_embeddings:
            from embeddings import build_embedding_index
            persist_dir = os.path.join(DEFAULT_DATA_DIR, "chroma")
            emb_result = build_embedding_index(persist_dir, db_path, force=True)
            output += f"\nEmbeddings rebuilt:\n"
            output += f"- Messages indexed: {emb_result['messages_indexed']}\n"
            output += f"- Total chunks: {emb_result['total_chunks']}\n"

        # Reset cached indexes
        global _search_index, _embedding_index
        _search_index = None
        _embedding_index = None

        return [TextContent(type="text", text=output)]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Main entry point."""
    # Ensure index is built on startup
    ensure_index_built()

    # Run the server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
