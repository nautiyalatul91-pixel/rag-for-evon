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
            # 1. Create chat_history and users tables
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_profiles (
                    id TEXT PRIMARY KEY,
                    company_name TEXT,
                    industry TEXT,
                    what_they_do TEXT,
                    tech_stack TEXT,
                    size_stage TEXT,
                    recent_news TEXT,
                    business_needs TEXT,
                    citations TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    research_id TEXT,
                    company_name TEXT,
                    profile_json TEXT,
                    opportunities_json TEXT,
                    internal_draft TEXT,
                    outreach_draft TEXT,
                    status TEXT,
                    rejection_reason TEXT,
                    sent_at TEXT,
                    sent_to TEXT,
                    send_error TEXT,
                    created_at TEXT
                )
            """)

            # Check and migrate drafts table to support profile_json, opportunities_json, and rejection_reason columns
            cursor = conn.execute("PRAGMA table_info(drafts)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "profile_json" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN profile_json TEXT")
            if "opportunities_json" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN opportunities_json TEXT")
            if "rejection_reason" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN rejection_reason TEXT")
            if "sent_at" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN sent_at TEXT")
            if "sent_to" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN sent_to TEXT")
            if "send_error" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN send_error TEXT")
            
            # 2. Check if documents table exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'")
            table_exists = cursor.fetchone()
            
            if not table_exists:
                # Create brand new table with composite constraints and collection_name
                conn.execute("""
                    CREATE TABLE documents (
                        id TEXT PRIMARY KEY,
                        filename TEXT,
                        content_hash TEXT,
                        upload_date TEXT,
                        chunk_count INTEGER,
                        status TEXT,
                        collection_name TEXT,
                        UNIQUE(filename, collection_name),
                        UNIQUE(content_hash, collection_name)
                    )
                """)
            else:
                # Check if collection_name column exists in documents table
                cursor = conn.execute("PRAGMA table_info(documents)")
                columns = [row['name'] for row in cursor.fetchall()]
                if 'collection_name' not in columns:
                    logger.info("Migrating SQLite documents table to support collections...")
                    # A. Rename existing table
                    conn.execute("ALTER TABLE documents RENAME TO documents_old")
                    
                    # B. Create new table with collection_name and composite unique constraints
                    conn.execute("""
                        CREATE TABLE documents (
                            id TEXT PRIMARY KEY,
                            filename TEXT,
                            content_hash TEXT,
                            upload_date TEXT,
                            chunk_count INTEGER,
                            status TEXT,
                            collection_name TEXT,
                            UNIQUE(filename, collection_name),
                            UNIQUE(content_hash, collection_name)
                        )
                    """)
                    
                    # C. Copy data from old table to new, setting default collection
                    conn.execute("""
                        INSERT INTO documents (id, filename, content_hash, upload_date, chunk_count, status, collection_name)
                        SELECT id, filename, content_hash, upload_date, chunk_count, status, 'company_knowledge_base_gemini_3072'
                        FROM documents_old
                    """)
                    
                    # D. Drop the old table
                    conn.execute("DROP TABLE documents_old")
                    logger.info("SQLite documents table migration completed successfully.")
            conn.commit()

    def _init_chroma(self):
        logger.info("Initializing ChromaDB persistent client at: %s", self.chroma_path)
        self.chroma_client = chromadb.PersistentClient(path=self.chroma_path)
        self.collections = {
            "company_knowledge_base_gemini_3072": self.chroma_client.get_or_create_collection(
                name="company_knowledge_base_gemini_3072"
            ),
            "evon_capabilities": self.chroma_client.get_or_create_collection(
                name="evon_capabilities"
            )
        }
        # Maintain backward compatibility reference for single-collection paths
        self.collection = self.collections["company_knowledge_base_gemini_3072"]

    def check_duplicate(self, filename: str, content_hash: str, collection_name: str = "company_knowledge_base_gemini_3072") -> Tuple[bool, Optional[str]]:
        """
        Check if the file has already been ingested successfully in the target collection.
        Returns: (is_duplicate, reason_message)
        """
        with self._get_sqlite_conn() as conn:
            # Check by content hash first (exact content duplicate, ignoring failed records)
            cursor = conn.execute(
                "SELECT id, filename, status FROM documents WHERE content_hash = ? AND collection_name = ?",
                (content_hash, collection_name)
            )
            row = cursor.fetchone()
            if row:
                if row['status'] == 'failed':
                    logger.info("Found stale failed record for content hash. Deleting it to allow re-upload.")
                    conn.execute("DELETE FROM documents WHERE id = ?", (row['id'],))
                    conn.commit()
                else:
                    return True, f"File with the same content already exists (ingested as '{row['filename']}')"

            # Check by filename (same name duplicate, ignoring failed records)
            cursor = conn.execute(
                "SELECT id, status FROM documents WHERE filename = ? AND collection_name = ?",
                (filename, collection_name)
            )
            row = cursor.fetchone()
            if row:
                if row['status'] == 'failed':
                    logger.info("Found stale failed record for filename '%s'. Deleting it to allow re-upload.", filename)
                    conn.execute("DELETE FROM documents WHERE id = ?", (row['id'],))
                    conn.commit()
                else:
                    return True, f"File with the name '{filename}' already exists"

        return False, None

    def create_document_record(self, doc_id: str, filename: str, content_hash: str, collection_name: str = "company_knowledge_base_gemini_3072") -> None:
        """Create a new document ingestion record with 'processing' status."""
        upload_date = datetime.utcnow().isoformat() + "Z"
        with self._get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, filename, content_hash, upload_date, chunk_count, status, collection_name)
                VALUES (?, ?, ?, ?, 0, 'processing', ?)
                """,
                (doc_id, filename, content_hash, upload_date, collection_name)
            )
            conn.commit()
        logger.info("Created metadata record for document %s (ID: %s, collection: %s)", filename, doc_id, collection_name)

    def update_document_status(self, doc_id: str, status: str, chunk_count: int) -> None:
        """Update the status and chunk count of a document."""
        with self._get_sqlite_conn() as conn:
            conn.execute(
                "UPDATE documents SET status = ?, chunk_count = ? WHERE id = ?",
                (status, chunk_count, doc_id)
            )
            conn.commit()
        logger.info("Updated status of document %s to %s (chunks: %d)", doc_id, status, chunk_count)

    def get_all_documents(self, collection_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve list of all documents metadata, optionally filtered by collection."""
        with self._get_sqlite_conn() as conn:
            if collection_name:
                cursor = conn.execute(
                    "SELECT id, filename, upload_date, chunk_count, status, collection_name FROM documents WHERE collection_name = ? ORDER BY upload_date DESC",
                    (collection_name,)
                )
            else:
                cursor = conn.execute(
                    "SELECT id, filename, upload_date, chunk_count, status, collection_name FROM documents ORDER BY upload_date DESC"
                )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific document's metadata."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT id, filename, upload_date, chunk_count, status, collection_name FROM documents WHERE id = ?",
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
        collection_name = doc.get("collection_name", "company_knowledge_base_gemini_3072")
        logger.info("Deleting document: %s (ID: %s) from collection %s", filename, doc_id, collection_name)

        # Remove from ChromaDB
        try:
            target_collection = self.collections.get(collection_name, self.collection)
            target_collection.delete(where={"document_id": doc_id})
            logger.info("Removed chunks from ChromaDB for document ID %s in collection %s", doc_id, collection_name)
        except Exception as e:
            logger.error("Failed to delete chunks from ChromaDB for document ID %s: %s", doc_id, e)

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
        embeddings: List[List[float]],
        collection_name: str = "company_knowledge_base_gemini_3072"
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
        target_collection = self.collections.get(collection_name, self.collection)
        target_collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
        logger.info("Stored %d chunks in ChromaDB for document %s (ID: %s, collection: %s)", len(chunks), filename, doc_id, collection_name)

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

    def save_research_profile(self, profile: dict) -> str:
        """
        Saves a company research profile to SQLite, returning a new research ID.
        """
        import uuid
        import json
        research_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat() + "Z"
        
        with self._get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO research_profiles (
                    id, company_name, industry, what_they_do, tech_stack,
                    size_stage, recent_news, business_needs, citations, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_id,
                    profile.get("company_name", ""),
                    profile.get("industry", ""),
                    profile.get("what_they_do", ""),
                    json.dumps(profile.get("tech_stack", [])),
                    profile.get("size_stage", ""),
                    json.dumps(profile.get("recent_news", [])),
                    json.dumps(profile.get("business_needs", [])),
                    json.dumps(profile.get("citations", {})),
                    created_at
                )
            )
            conn.commit()
        logger.info("Saved research profile for '%s' with ID %s", profile.get("company_name"), research_id)
        return research_id

    def get_research_profile(self, profile_id: str) -> Optional[dict]:
        """
        Retrieves and deserializes a previously saved research profile from SQLite.
        """
        import json
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, company_name, industry, what_they_do, tech_stack,
                       size_stage, recent_news, business_needs, citations
                FROM research_profiles WHERE id = ?
                """,
                (profile_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            # Helper to safely load JSON lists/dicts
            def safe_load_json(val, default_val):
                if not val:
                    return default_val
                try:
                    return json.loads(val)
                except Exception:
                    return default_val

            return {
                "research_id": row["id"],
                "company_name": row["company_name"],
                "industry": row["industry"],
                "what_they_do": row["what_they_do"],
                "tech_stack": safe_load_json(row["tech_stack"], []),
                "size_stage": row["size_stage"],
                "recent_news": safe_load_json(row["recent_news"], []),
                "business_needs": safe_load_json(row["business_needs"], []),
                "citations": safe_load_json(row["citations"], {})
            }

    def save_draft(
        self,
        research_id: Optional[str],
        company_name: str,
        internal_draft: str,
        outreach_draft: str,
        profile: Optional[dict] = None,
        opportunities: Optional[list] = None
    ) -> str:
        """
        Saves generated internal and outreach drafts to SQLite under 'pending_review' status.
        """
        import uuid
        import json
        draft_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat() + "Z"
        
        profile_json = json.dumps(profile) if profile is not None else None
        opportunities_json = json.dumps(opportunities) if opportunities is not None else None
        
        with self._get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO drafts (
                    id, research_id, company_name, profile_json, opportunities_json,
                    internal_draft, outreach_draft, status, rejection_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review', NULL, ?)
                """,
                (
                    draft_id,
                    research_id,
                    company_name,
                    profile_json,
                    opportunities_json,
                    internal_draft,
                    outreach_draft,
                    created_at
                )
            )
            conn.commit()
        logger.info("Saved drafts for '%s' with ID %s", company_name, draft_id)
        return draft_id

    def _enrich_draft_row(self, row) -> dict:
        import json
        
        def safe_load_json(val, default_val):
            if not val:
                return default_val
            try:
                return json.loads(val)
            except Exception:
                return default_val

        profile = safe_load_json(row["profile_json"], None)
        opportunities = safe_load_json(row["opportunities_json"], []) if row["opportunities_json"] is not None else None
        research_id = row["research_id"]
        
        # Dynamic backward-compatibility lookup for pre-migration drafts
        if not profile and research_id:
            try:
                with self._get_sqlite_conn() as conn:
                    prof_cursor = conn.execute(
                        """
                        SELECT company_name, industry, what_they_do, tech_stack,
                               size_stage, recent_news, business_needs, citations
                        FROM research_profiles WHERE id = ?
                        """,
                        (research_id,)
                    )
                    p_row = prof_cursor.fetchone()
                    if p_row:
                        profile = {
                            "research_id": research_id,
                            "company_name": p_row["company_name"],
                            "industry": p_row["industry"],
                            "what_they_do": p_row["what_they_do"],
                            "tech_stack": safe_load_json(p_row["tech_stack"], []),
                            "size_stage": p_row["size_stage"],
                            "recent_news": safe_load_json(p_row["recent_news"], []),
                            "business_needs": safe_load_json(p_row["business_needs"], []),
                            "citations": safe_load_json(p_row["citations"], {})
                        }
            except Exception as e:
                logger.error("Failed to dynamically enrich old draft row %s: %s", row["id"], e)

        return {
            "id": row["id"],
            "research_id": research_id,
            "company_name": row["company_name"],
            "profile": profile,
            "opportunities": opportunities,
            "internal_draft": row["internal_draft"],
            "outreach_draft": row["outreach_draft"],
            "status": row["status"],
            "rejection_reason": row["rejection_reason"],
            "sent_at": row["sent_at"] if "sent_at" in row.keys() else None,
            "sent_to": row["sent_to"] if "sent_to" in row.keys() else None,
            "send_error": row["send_error"] if "send_error" in row.keys() else None,
            "created_at": row["created_at"]
        }

    def get_draft(self, draft_id: str) -> Optional[dict]:
        """
        Retrieves a draft record by ID from SQLite.
        """
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, research_id, company_name, profile_json, opportunities_json,
                       internal_draft, outreach_draft, status, rejection_reason,
                       sent_at, sent_to, send_error, created_at
                FROM drafts WHERE id = ?
                """,
                (draft_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._enrich_draft_row(row)

    def list_drafts(self, status: Optional[str] = None) -> List[dict]:
        """
        Lists all saved drafts, optionally filtered by status.
        """
        query = """
            SELECT id, research_id, company_name, profile_json, opportunities_json,
                   internal_draft, outreach_draft, status, rejection_reason,
                   sent_at, sent_to, send_error, created_at
            FROM drafts
        """
        params = ()
        if status:
            if status == "sent":
                query += " WHERE status IN ('sent', 'sent_dryrun')"
            else:
                query += " WHERE status = ?"
                params = (status,)
        query += " ORDER BY created_at DESC"
        
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._enrich_draft_row(row) for row in rows]

    def update_draft_status(self, draft_id: str, status: str, rejection_reason: Optional[str] = None) -> bool:
        """
        Updates the review status of a draft in SQLite, with an optional rejection reason.
        """
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "UPDATE drafts SET status = ?, rejection_reason = ? WHERE id = ?",
                (status, rejection_reason, draft_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_sent_status(
        self,
        draft_id: str,
        status: str,
        sent_to: Optional[str] = None,
        sent_at: Optional[str] = None,
        send_error: Optional[str] = None
    ) -> bool:
        """
        Updates sending details and status for a draft.
        """
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE drafts
                SET status = ?, sent_to = ?, sent_at = ?, send_error = ?
                WHERE id = ?
                """,
                (status, sent_to, sent_at, send_error, draft_id)
            )
            conn.commit()
            return cursor.rowcount > 0

# Global database service instance
db_service = DBService()
