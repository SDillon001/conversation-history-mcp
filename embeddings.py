"""
Embedding Generation and Semantic Search

Uses sentence-transformers for local embeddings and ChromaDB for vector storage.
"""

import os
from typing import Optional
from dataclasses import dataclass

# Lazy imports for faster startup
_model = None
_chroma_client = None


@dataclass
class SemanticResult:
    """A semantic search result."""
    session_id: str
    message_id: str
    content: str
    distance: float
    project: str
    timestamp: str


def get_embedding_model():
    """Lazy load the embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        # all-MiniLM-L6-v2: 22M params, 384 dims, good quality/speed balance
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def get_chroma_client(persist_dir: str):
    """Get or create ChromaDB client."""
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        from chromadb.config import Settings
        os.makedirs(persist_dir, exist_ok=True)
        _chroma_client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=persist_dir,
            anonymized_telemetry=False,
        ))
    return _chroma_client


class EmbeddingIndex:
    """ChromaDB-based semantic search index."""

    COLLECTION_NAME = "conversation_history"
    CHUNK_SIZE = 500  # Characters per chunk
    CHUNK_OVERLAP = 50

    def __init__(self, persist_dir: str):
        self.persist_dir = persist_dir
        self.model = get_embedding_model()
        self.client = get_chroma_client(persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks."""
        if len(text) <= self.CHUNK_SIZE:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + self.CHUNK_SIZE
            chunk = text[start:end]

            # Try to break at sentence or word boundary
            if end < len(text):
                # Look for sentence end
                for sep in [". ", "? ", "! ", "\n"]:
                    last_sep = chunk.rfind(sep)
                    if last_sep > self.CHUNK_SIZE // 2:
                        chunk = chunk[:last_sep + len(sep)]
                        end = start + len(chunk)
                        break

            chunks.append(chunk.strip())
            start = end - self.CHUNK_OVERLAP

        return chunks

    def add_message(
        self,
        session_id: str,
        message_id: str,
        content: str,
        project: str,
        timestamp: str,
        role: str,
    ):
        """Add a message to the embedding index."""
        chunks = self._chunk_text(content)

        for i, chunk in enumerate(chunks):
            chunk_id = f"{message_id}_{i}"

            # Generate embedding
            embedding = self.model.encode(chunk).tolist()

            self.collection.add(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[{
                    "session_id": session_id,
                    "message_id": message_id,
                    "project": project,
                    "timestamp": timestamp,
                    "role": role,
                    "chunk_index": i,
                }]
            )

    def search(self, query: str, limit: int = 10, project: Optional[str] = None) -> list[SemanticResult]:
        """Search for semantically similar content."""
        # Generate query embedding
        query_embedding = self.model.encode(query).tolist()

        # Build where filter
        where_filter = None
        if project:
            where_filter = {"project": project}

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=where_filter,
        )

        # Deduplicate by message_id (since we chunk messages)
        seen_messages = set()
        semantic_results = []

        if results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i]
                message_id = metadata["message_id"]

                if message_id in seen_messages:
                    continue
                seen_messages.add(message_id)

                semantic_results.append(SemanticResult(
                    session_id=metadata["session_id"],
                    message_id=message_id,
                    content=results["documents"][0][i],
                    distance=results["distances"][0][i] if results["distances"] else 0,
                    project=metadata["project"],
                    timestamp=metadata["timestamp"],
                ))

        return semantic_results

    def get_stats(self) -> dict:
        """Get index statistics."""
        return {
            "total_chunks": self.collection.count(),
        }

    def clear(self):
        """Clear the entire index."""
        self.client.delete_collection(self.COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )


def build_embedding_index(
    persist_dir: str,
    db_path: str,
    force: bool = False
) -> dict:
    """Build embedding index from the SQLite index."""
    from indexer import SearchIndex

    embedding_index = EmbeddingIndex(persist_dir)
    search_index = SearchIndex(db_path)

    if force:
        embedding_index.clear()

    # Get all sessions
    sessions = search_index.list_sessions(days=365, limit=10000)

    indexed = 0
    for session in sessions:
        messages = search_index.get_session_messages(session["session_id"])

        for msg in messages:
            try:
                embedding_index.add_message(
                    session_id=msg["session_id"],
                    message_id=msg["message_id"],
                    content=msg["content"],
                    project=msg["project"],
                    timestamp=msg["timestamp"],
                    role=msg["role"],
                )
                indexed += 1
            except Exception as e:
                print(f"Error embedding message {msg['message_id']}: {e}")

    search_index.close()

    stats = embedding_index.get_stats()
    return {
        "messages_indexed": indexed,
        **stats,
    }


if __name__ == "__main__":
    import sys

    persist_dir = os.path.expanduser("~/.claude/mcp-servers/conversation-history/data/chroma")
    db_path = os.path.expanduser("~/.claude/mcp-servers/conversation-history/data/index.db")
    force = "--force" in sys.argv

    print(f"Building embedding index at {persist_dir}...")
    result = build_embedding_index(persist_dir, db_path, force=force)
    print(f"Done: {result}")
