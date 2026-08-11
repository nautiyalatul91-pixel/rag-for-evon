import sqlite3
import chromadb
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from app.config import SQLITE_DB_PATH, CHROMA_DB_PATH, logger

class DBService:
    def __init__(self):
        self.sqlite_path = SQLITE_DB_PATH
        self.chroma_path = CHROMA_DB_PATH
        self._init_sqlite()
        self._init_chroma()

    def _get_sqlite_conn(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        logger.info("Initializing SQLite database at: %s", self.sqlite_path)
        with self._get_sqlite_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT UNIQUE,
                    content_hash TEXT UNIQUE,
                    upload_date TEXT,
                    chunk_count INTEGER,
                    status TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    hashed_password TEXT,
                    role TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()

    def _init_chroma(self):
        logger.info("Initializing ChromaDB persistent client at: %s", self.chroma_path)
        self.chroma_client = chromadb.PersistentClient(path=self.chroma_path)
        self.collection = self.chroma_client.get_or_create_collection(
            name="company_knowledge_base_gemini_3072"
        )

    def check_duplicate(self, filename: str, content_hash: str) -> Tuple[bool, Optional[str]]:
        """
        Check if the file has already been ingested successfully.
        Returns: (is_duplicate, reason_message)
        """
        with self._get_sqlite_conn() as conn:
            # Check by content hash first (exact content duplicate, ignoring failed records)
            cursor = conn.execute("SELECT id, filename, status FROM documents WHERE content_hash = ?", (content_hash,))
            row = cursor.fetchone()
            if row:
                if row['status'] == 'failed':
                    logger.info("Found stale failed record for content hash. Deleting it to allow re-upload.")
                    conn.execute("DELETE FROM documents WHERE id = ?", (row['id'],))
                    conn.commit()
                else:
                    return True, f"File with the same content already exists (ingested as '{row['filename']}')"

            # Check by filename (same name duplicate, ignoring failed records)
            cursor = conn.execute("SELECT id, status FROM documents WHERE filename = ?", (filename,))
            row = cursor.fetchone()
            if row:
                if row['status'] == 'failed':
                    logger.info("Found stale failed record for filename '%s'. Deleting it to allow re-upload.", filename)
                    conn.execute("DELETE FROM documents WHERE id = ?", (row['id'],))
                    conn.commit()
                else:
                    return True, f"File with the name '{filename}' already exists"

        return False, None

    def create_document_record(self, doc_id: str, filename: str, content_hash: str) -> None:
        """Create a new document ingestion record with 'processing' status."""
        upload_date = datetime.utcnow().isoformat() + "Z"
        with self._get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, filename, content_hash, upload_date, chunk_count, status)
                VALUES (?, ?, ?, ?, 0, 'processing')
                """,
                (doc_id, filename, content_hash, upload_date)
            )
            conn.commit()
        logger.info("Created metadata record for document %s (ID: %s)", filename, doc_id)

    def update_document_status(self, doc_id: str, status: str, chunk_count: int) -> None:
        """Update the status and chunk count of a document."""
        with self._get_sqlite_conn() as conn:
            conn.execute(
                "UPDATE documents SET status = ?, chunk_count = ? WHERE id = ?",
                (status, chunk_count, doc_id)
            )
            conn.commit()
        logger.info("Updated status of document %s to %s (chunks: %d)", doc_id, status, chunk_count)

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Retrieve list of all documents metadata."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT id, filename, upload_date, chunk_count, status FROM documents ORDER BY upload_date DESC"
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific document's metadata."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT id, filename, upload_date, chunk_count, status FROM documents WHERE id = ?",
                (doc_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_document(self, doc_id: str) -> bool:
        """
        Delete document record from SQLite and remove all associated vectors from ChromaDB.
        """
        doc = self.get_document_by_id(doc_id)
        if not doc:
            logger.warning("Document with ID %s not found in SQLite metadata", doc_id)
            return False

        filename = doc["filename"]
        logger.info("Deleting document: %s (ID: %s)", filename, doc_id)

        # Remove from ChromaDB
        try:
            self.collection.delete(where={"document_id": doc_id})
            logger.info("Removed chunks from ChromaDB for document ID %s", doc_id)
        except Exception as e:
            logger.error("Failed to delete chunks from ChromaDB for document ID %s: %s", doc_id, e)
            # Proceed to try and delete from sqlite anyway, or raise error. 
            # ChromaDB delete raises if something fails, but let's be resilient.

        # Remove from SQLite
        with self._get_sqlite_conn() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()

        logger.info("Successfully deleted document %s metadata from SQLite", filename)
        return True

    def add_chunks_to_chroma(
        self,
        doc_id: str,
        filename: str,
        content_hash: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]]
    ) -> None:
        """
        Save chunks and their embeddings into ChromaDB collection.
        Each chunk is a dict containing 'text', 'page_number', 'chunk_index', and 'timestamp'.
        """
        ids = []
        metadatas = []
        documents = []

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_{idx}"
            ids.append(chunk_id)
            documents.append(chunk["text"])
            metadatas.append({
                "document_id": doc_id,
                "source_filename": filename,
                "content_hash": content_hash,
                "page_number": chunk["page_number"],
                "chunk_index": chunk["chunk_index"],
                "upload_timestamp": chunk["timestamp"]
            })

        # Insert to ChromaDB
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
        logger.info("Stored %d chunks in ChromaDB for document %s (ID: %s)", len(chunks), filename, doc_id)

    def save_chat_message(self, conversation_id: str, role: str, content: str) -> None:
        """Save a message turn (user or assistant) to SQLite database."""
        timestamp = datetime.utcnow().isoformat() + "Z"
        with self._get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_history (conversation_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role, content, timestamp)
            )
            conn.commit()
        logger.info("Saved chat message for conversation_id %s (role: %s)", conversation_id, role)

    def get_chat_history(self, conversation_id: str, limit: int = 6) -> List[Dict[str, str]]:
        """Retrieve the last N messages of a conversation in chronological order."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                SELECT role, content FROM chat_history
                WHERE conversation_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (conversation_id, limit)
            )
            rows = cursor.fetchall()
            history = [{"role": row["role"], "content": row["content"]} for row in rows]
            history.reverse()  # Reverse to restore chronological order
            return history

    def create_user(self, username: str, hashed_password: str, role: str) -> int:
        """Insert a new user record in SQLite. Returns the row ID."""
        created_at = datetime.utcnow().isoformat() + "Z"
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, hashed_password, role, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (username, hashed_password, role, created_at)
            )
            conn.commit()
            return cursor.lastrowid

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Retrieve a user by username from SQLite."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT id, username, hashed_password, role, created_at FROM users WHERE username = ?",
                (username,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

# Global database service instance
db_service = DBService()
