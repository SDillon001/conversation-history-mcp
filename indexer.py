#!/usr/bin/env python3
"""
Indexer module for conversation history.
Provides SQLite FTS5-based search index.
"""

import os
import json
import sqlite3
import glob
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class SearchIndex:
    """SQLite FTS5-based search index for conversation history."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Initialize database schema."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project TEXT,
                summary TEXT,
                first_timestamp TEXT,
                last_timestamp TEXT,
                message_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                project TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content_rowid='id'
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
            CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON sessions(last_timestamp);
        """)
        self.conn.commit()

    def add_session(self, session_id: str, project: str, messages: List[Dict]):
        """Add or update a session with its messages."""
        if not messages:
            return

        # Extract timestamps
        timestamps = [m.get("timestamp", "") for m in messages if m.get("timestamp")]
        first_ts = min(timestamps) if timestamps else ""
        last_ts = max(timestamps) if timestamps else ""

        # Generate summary from first user message
        summary = ""
        for m in messages:
            if m.get("role") == "human" and m.get("content"):
                content = m["content"]
                if isinstance(content, list):
                    # Handle structured content
                    text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    content = " ".join(text_parts)
                summary = content[:200] + "..." if len(content) > 200 else content
                break

        # Upsert session
        self.conn.execute("""
            INSERT OR REPLACE INTO sessions (session_id, project, summary, first_timestamp, last_timestamp, message_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, project, summary, first_ts, last_ts, len(messages)))

        # Delete old messages for this session
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

        # Insert new messages
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(text_parts)

            if not content:
                continue

            cursor = self.conn.execute("""
                INSERT INTO messages (session_id, role, content, timestamp, project)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, m.get("role", ""), content, m.get("timestamp", ""), project))

            # Add to FTS index
            self.conn.execute("""
                INSERT INTO messages_fts (rowid, content) VALUES (?, ?)
            """, (cursor.lastrowid, content))

        self.conn.commit()

    def search(self, query: str, limit: int = 20) -> List[Dict]:
        """Search messages using FTS5."""
        try:
            cursor = self.conn.execute("""
                SELECT m.session_id, m.role, m.content, m.timestamp, m.project
                FROM messages_fts f
                JOIN messages m ON f.rowid = m.id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # Fallback to LIKE search if FTS query is invalid
            cursor = self.conn.execute("""
                SELECT session_id, role, content, timestamp, project
                FROM messages
                WHERE content LIKE ?
                LIMIT ?
            """, (f"%{query}%", limit))
            return [dict(row) for row in cursor.fetchall()]

    def list_sessions(self, days: int = 7, project: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """List recent sessions."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        if project:
            cursor = self.conn.execute("""
                SELECT session_id, project, summary, message_count, last_timestamp
                FROM sessions
                WHERE last_timestamp >= ? AND project = ?
                ORDER BY last_timestamp DESC
                LIMIT ?
            """, (cutoff, project, limit))
        else:
            cursor = self.conn.execute("""
                SELECT session_id, project, summary, message_count, last_timestamp
                FROM sessions
                WHERE last_timestamp >= ?
                ORDER BY last_timestamp DESC
                LIMIT ?
            """, (cutoff, limit))

        return [dict(row) for row in cursor.fetchall()]

    def get_session_messages(self, session_id: str) -> List[Dict]:
        """Get all messages from a session."""
        cursor = self.conn.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> Dict[str, int]:
        """Get index statistics."""
        sessions = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"sessions": sessions, "messages": messages}

    def close(self):
        """Close database connection."""
        self.conn.close()


def build_index(db_path: str, projects_dir: str, force: bool = False) -> Dict[str, Any]:
    """Build the search index from conversation history files."""
    index = SearchIndex(db_path)

    if force:
        # Clear existing data
        index.conn.executescript("""
            DELETE FROM messages_fts;
            DELETE FROM messages;
            DELETE FROM sessions;
        """)
        index.conn.commit()

    files_found = 0
    files_indexed = 0
    total_sessions = 0
    total_messages = 0

    # Find all conversation JSON files
    pattern = os.path.join(projects_dir, "**", "*.jsonl")
    files = glob.glob(pattern, recursive=True)
    files_found = len(files)

    for filepath in files:
        try:
            # Extract project name from path
            rel_path = os.path.relpath(filepath, projects_dir)
            parts = rel_path.split(os.sep)
            project = parts[0] if parts else "unknown"

            # Parse session ID from filename
            session_id = os.path.splitext(os.path.basename(filepath))[0]

            # Read and parse messages
            messages = []
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            msg = json.loads(line)
                            messages.append(msg)
                        except json.JSONDecodeError:
                            continue

            if messages:
                index.add_session(session_id, project, messages)
                files_indexed += 1
                total_sessions += 1
                total_messages += len(messages)

        except Exception as e:
            logger.warning(f"Error indexing {filepath}: {e}")

    index.close()

    return {
        "files_found": files_found,
        "files_indexed": files_indexed,
        "sessions": total_sessions,
        "messages": total_messages
    }
