#!/usr/bin/env python3
"""
Embeddings module for semantic search.
Uses sentence-transformers and ChromaDB for vector storage.
"""

import os
import sqlite3
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# Lazy load sentence transformers (heavy import)
_model = None


def get_model():
    """Lazy load the embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model


@dataclass
class SearchResult:
    """A semantic search result."""
    session_id: str
    project: str
    content: str
    timestamp: str
    distance: float


class EmbeddingIndex:
    """ChromaDB-based embedding index for semantic search."""

    def __init__(self, persist_dir: str):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False)
        )

        self.collection = self.client.get_or_create_collection(
            name="conversations",
            metadata={"hnsw:space": "cosine"}
        )

    def add_message(self, msg_id: str, content: str, metadata: Dict[str, Any]):
        """Add a message to the embedding index."""
        model = get_model()
        embedding = model.encode(content).tolist()

        self.collection.upsert(
            ids=[msg_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[metadata]
        )

    def search(self, query: str, limit: int = 10, project: Optional[str] = None) -> List[SearchResult]:
        """Search for semantically similar messages."""
        model = get_model()
        query_embedding = model.encode(query).tolist()

        where_filter = None
        if project:
            where_filter = {"project": project}

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=where_filter,
            include=["documents", "metadatas", "distances"]
        )

        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                search_results.append(SearchResult(
                    session_id=metadata.get("session_id", ""),
                    project=metadata.get("project", ""),
                    content=results["documents"][0][i] if results["documents"] else "",
                    timestamp=metadata.get("timestamp", ""),
                    distance=results["distances"][0][i] if results["distances"] else 0.0
                ))

        return search_results

    def get_count(self) -> int:
        """Get the number of indexed messages."""
        return self.collection.count()


def build_embedding_index(persist_dir: str, db_path: str, force: bool = False) -> Dict[str, Any]:
    """Build embedding index from the SQLite database."""
    index = EmbeddingIndex(persist_dir)

    if force:
        # Delete and recreate collection
        try:
            index.client.delete_collection("conversations")
        except Exception:
            pass
        index.collection = index.client.create_collection(
            name="conversations",
            metadata={"hnsw:space": "cosine"}
        )

    # Read messages from SQLite
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("""
        SELECT id, session_id, role, content, timestamp, project
        FROM messages
        WHERE role IN ('human', 'assistant') AND length(content) > 50
    """)

    messages_indexed = 0
    total_chunks = 0

    for row in cursor:
        try:
            content = row["content"]
            # Chunk long messages
            chunks = chunk_text(content, max_length=500)

            for i, chunk in enumerate(chunks):
                msg_id = f"{row['id']}_{i}"
                metadata = {
                    "session_id": row["session_id"],
                    "project": row["project"],
                    "timestamp": row["timestamp"] or "",
                    "role": row["role"]
                }
                index.add_message(msg_id, chunk, metadata)
                total_chunks += 1

            messages_indexed += 1

        except Exception as e:
            logger.warning(f"Error indexing message {row['id']}: {e}")

    conn.close()

    return {
        "messages_indexed": messages_indexed,
        "total_chunks": total_chunks
    }


def chunk_text(text: str, max_length: int = 500) -> List[str]:
    """Split text into chunks for embedding."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    sentences = text.replace('\n', ' ').split('. ')
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 2 <= max_length:
            current_chunk += sentence + ". "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + ". "

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text[:max_length]]
