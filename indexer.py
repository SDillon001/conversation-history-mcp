"""
JSONL Session Parser and SQLite FTS5 Indexer

Parses Claude Code session files and indexes them for fast full-text search.
"""

import json
import sqlite3
import os
import glob
from pathlib import Path
from datetime import datetime, timedelta
from typing import Generator, Optional
from dataclasses import dataclass


@dataclass
class Message:
    """A single message from a conversation."""
    session_id: str
    message_id: str
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    project: str


@dataclass
class Session:
    """A conversation session with metadata."""
    session_id: str
    project: str
    summary: Optional[str]
    first_timestamp: str
    last_timestamp: str
    message_count: int


class SessionParser:
    """Parse JSONL session files from Claude Code."""

    def __init__(self, projects_dir: Optional[str] = None):
        self.projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")

    def get_all_session_files(self) -> list[Path]:
        """Find all JSONL session files."""
        pattern = os.path.join(self.projects_dir, "*", "*.jsonl")
        files = glob.glob(pattern)
        # Filter out agent-* files (subagent conversations)
        return [Path(f) for f in files if not os.path.basename(f).startswith("agent-")]

    def get_project_name(self, session_path: Path) -> str:
        """Extract project name from session path."""
        # Path is like ~/.claude/projects/-Users-dsdillon-project-name/session.jsonl
        project_dir = session_path.parent.name
        # Convert -Users-dsdillon-project-name to project-name
        parts = project_dir.split("-")
        # Skip the leading empty string and user path components
        if len(parts) > 4:
            return "-".join(parts[4:])
        return project_dir

    def parse_session_file(self, path: Path) -> Generator[dict, None, None]:
        """Parse a JSONL session file, yielding each record."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
        except (IOError, OSError):
            return

    def extract_messages(self, path: Path) -> Generator[Message, None, None]:
        """Extract user and assistant messages from a session file."""
        project = self.get_project_name(path)

        for record in self.parse_session_file(path):
            record_type = record.get("type")

            if record_type in ("user", "assistant"):
                message = record.get("message", {})
                content = message.get("content", "")

                # Handle content that might be a list of blocks
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts)

                if content:
                    yield Message(
                        session_id=record.get("sessionId", ""),
                        message_id=record.get("uuid", ""),
                        role=record_type,
                        content=content,
                        timestamp=record.get("timestamp", ""),
                        project=project,
                    )

    def extract_session_summary(self, path: Path) -> Optional[str]:
        """Extract the session summary if present."""
        for record in self.parse_session_file(path):
            if record.get("type") == "summary":
                return record.get("summary")
        return None

    def get_session_metadata(self, path: Path) -> Optional[Session]:
        """Get metadata about a session."""
        messages = list(self.extract_messages(path))
        if not messages:
            return None

        session_id = path.stem  # filename without extension
        project = self.get_project_name(path)
        summary = self.extract_session_summary(path)

        timestamps = [m.timestamp for m in messages if m.timestamp]
        first_ts = min(timestamps) if timestamps else ""
        last_ts = max(timestamps) if timestamps else ""

        return Session(
            session_id=session_id,
            project=project,
            summary=summary,
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            message_count=len(messages),
        )


class SearchIndex:
    """SQLite FTS5 search index for conversation history."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()

        # Messages table with FTS5
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_id,
                message_id,
                role,
                content,
                timestamp,
                project,
                tokenize='porter unicode61'
            )
        """)

        # Sessions metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project TEXT,
                summary TEXT,
                first_timestamp TEXT,
                last_timestamp TEXT,
                message_count INTEGER,
                indexed_at TEXT
            )
        """)

        # Index tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_state (
                file_path TEXT PRIMARY KEY,
                mtime REAL,
                indexed_at TEXT
            )
        """)

        self.conn.commit()

    def is_file_indexed(self, path: Path) -> bool:
        """Check if a file has already been indexed (and hasn't changed)."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mtime FROM index_state WHERE file_path = ?",
            (str(path),)
        )
        row = cursor.fetchone()
        if row is None:
            return False

        current_mtime = path.stat().st_mtime
        return row[0] >= current_mtime

    def index_session(self, path: Path, parser: SessionParser):
        """Index a session file."""
        cursor = self.conn.cursor()

        # Index messages
        for message in parser.extract_messages(path):
            cursor.execute("""
                INSERT INTO messages_fts (session_id, message_id, role, content, timestamp, project)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                message.session_id,
                message.message_id,
                message.role,
                message.content,
                message.timestamp,
                message.project,
            ))

        # Index session metadata
        session = parser.get_session_metadata(path)
        if session:
            cursor.execute("""
                INSERT OR REPLACE INTO sessions
                (session_id, project, summary, first_timestamp, last_timestamp, message_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id,
                session.project,
                session.summary,
                session.first_timestamp,
                session.last_timestamp,
                session.message_count,
                datetime.now().isoformat(),
            ))

        # Mark file as indexed
        cursor.execute("""
            INSERT OR REPLACE INTO index_state (file_path, mtime, indexed_at)
            VALUES (?, ?, ?)
        """, (
            str(path),
            path.stat().st_mtime,
            datetime.now().isoformat(),
        ))

        self.conn.commit()

    def _escape_fts_query(self, query: str) -> str:
        """Escape special FTS5 characters and wrap terms in quotes."""
        # If query already has FTS5 operators, use as-is
        fts_operators = ['AND', 'OR', 'NOT', 'NEAR', '"']
        if any(op in query.upper() for op in fts_operators):
            return query

        # Otherwise, wrap each term in quotes to handle special chars like dots
        terms = query.split()
        escaped = [f'"{term}"' for term in terms]
        return ' '.join(escaped)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search messages using FTS5."""
        cursor = self.conn.cursor()
        escaped_query = self._escape_fts_query(query)
        cursor.execute("""
            SELECT session_id, message_id, role, content, timestamp, project,
                   bm25(messages_fts) as score
            FROM messages_fts
            WHERE messages_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """, (escaped_query, limit))

        results = []
        for row in cursor.fetchall():
            results.append({
                "session_id": row[0],
                "message_id": row[1],
                "role": row[2],
                "content": row[3][:500] + "..." if len(row[3]) > 500 else row[3],
                "timestamp": row[4],
                "project": row[5],
                "score": row[6],
            })
        return results

    def list_sessions(self, days: int = 7, project: Optional[str] = None, limit: int = 50) -> list[dict]:
        """List recent sessions."""
        cursor = self.conn.cursor()

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        if project:
            cursor.execute("""
                SELECT session_id, project, summary, first_timestamp, last_timestamp, message_count
                FROM sessions
                WHERE last_timestamp > ? AND project = ?
                ORDER BY last_timestamp DESC
                LIMIT ?
            """, (cutoff, project, limit))
        else:
            cursor.execute("""
                SELECT session_id, project, summary, first_timestamp, last_timestamp, message_count
                FROM sessions
                WHERE last_timestamp > ?
                ORDER BY last_timestamp DESC
                LIMIT ?
            """, (cutoff, limit))

        results = []
        for row in cursor.fetchall():
            results.append({
                "session_id": row[0],
                "project": row[1],
                "summary": row[2],
                "first_timestamp": row[3],
                "last_timestamp": row[4],
                "message_count": row[5],
            })
        return results

    def get_session_messages(self, session_id: str) -> list[dict]:
        """Get all messages from a specific session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT session_id, message_id, role, content, timestamp, project
            FROM messages_fts
            WHERE session_id = ?
            ORDER BY timestamp
        """, (session_id,))

        results = []
        for row in cursor.fetchall():
            results.append({
                "session_id": row[0],
                "message_id": row[1],
                "role": row[2],
                "content": row[3],
                "timestamp": row[4],
                "project": row[5],
            })
        return results

    def get_stats(self) -> dict:
        """Get index statistics."""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM sessions")
        session_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages_fts")
        message_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT project) FROM sessions")
        project_count = cursor.fetchone()[0]

        return {
            "sessions": session_count,
            "messages": message_count,
            "projects": project_count,
        }

    def clear(self):
        """Clear the entire index."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM messages_fts")
        cursor.execute("DELETE FROM sessions")
        cursor.execute("DELETE FROM index_state")
        self.conn.commit()

    def close(self):
        """Close the database connection."""
        self.conn.close()


def build_index(db_path: str, projects_dir: Optional[str] = None, force: bool = False) -> dict:
    """Build or update the search index."""
    parser = SessionParser(projects_dir)
    index = SearchIndex(db_path)

    if force:
        index.clear()

    files = parser.get_all_session_files()
    indexed = 0
    skipped = 0

    for path in files:
        if not force and index.is_file_indexed(path):
            skipped += 1
            continue

        try:
            index.index_session(path, parser)
            indexed += 1
        except Exception as e:
            print(f"Error indexing {path}: {e}")

    stats = index.get_stats()
    index.close()

    return {
        "files_found": len(files),
        "files_indexed": indexed,
        "files_skipped": skipped,
        **stats,
    }


if __name__ == "__main__":
    # Test the indexer
    import sys

    db_path = os.path.expanduser("~/.claude/mcp-servers/conversation-history/data/index.db")
    force = "--force" in sys.argv

    print(f"Building index at {db_path}...")
    result = build_index(db_path, force=force)
    print(f"Done: {result}")
