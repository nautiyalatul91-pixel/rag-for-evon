import json
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
            
            # 2. Create roles table and seed default roles if empty
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roles (
                    role_id TEXT PRIMARY KEY,
                    role_name TEXT NOT NULL,
                    hierarchy_position INTEGER NOT NULL,
                    can_upload BOOLEAN NOT NULL DEFAULT 0,
                    is_protected BOOLEAN NOT NULL DEFAULT 0
                )
            """)
            cursor = conn.execute("SELECT COUNT(*) FROM roles")
            if cursor.fetchone()[0] == 0:
                logger.info("Seeding default roles into SQLite roles table...")
                default_roles = [
                    ("admin", "Admin", 1, 1, 1),
                    ("co_founder", "Co-founder", 2, 1, 0),
                    ("programmer", "Programmer", 3, 0, 0),
                    ("tester", "Tester", 4, 0, 0),
                    ("employee", "Employee", 5, 0, 0),
                ]
                conn.executemany(
                    """
                    INSERT INTO roles (role_id, role_name, hierarchy_position, can_upload, is_protected)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    default_roles
                )

            # 3. Check if documents table exists
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

            # 4. Check if allowed_roles, uploader_username, uploader_role exist on documents
            cursor = conn.execute("PRAGMA table_info(documents)")
            columns = [row['name'] for row in cursor.fetchall()]
            if 'allowed_roles' not in columns:
                logger.info("Migrating SQLite documents table to support allowed_roles...")
                conn.execute("ALTER TABLE documents ADD COLUMN allowed_roles TEXT")
            if 'uploader_username' not in columns:
                logger.info("Migrating SQLite documents table to support uploader_username...")
                conn.execute("ALTER TABLE documents ADD COLUMN uploader_username TEXT DEFAULT 'Unknown'")
            if 'uploader_role' not in columns:
                logger.info("Migrating SQLite documents table to support uploader_role...")
                conn.execute("ALTER TABLE documents ADD COLUMN uploader_role TEXT DEFAULT 'Unknown'")

            # One-time migration for existing documents missing allowed_roles
            all_roles_cursor = conn.execute("SELECT role_id FROM roles")
            all_role_ids = [r['role_id'] for r in all_roles_cursor.fetchall()]
            if all_role_ids:
                all_roles_json = json.dumps(all_role_ids)
                conn.execute("UPDATE documents SET allowed_roles = ? WHERE allowed_roles IS NULL", (all_roles_json,))
                conn.execute("UPDATE documents SET uploader_username = 'Unknown' WHERE uploader_username IS NULL")
                conn.execute("UPDATE documents SET uploader_role = 'Unknown' WHERE uploader_role IS NULL")

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

        # One-time migration & synchronization for ChromaDB chunks
        try:
            with self._get_sqlite_conn() as conn:
                role_ids = [r['role_id'] for r in conn.execute("SELECT role_id FROM roles").fetchall()]
            all_roles_str = ",".join(role_ids)
            for col_name, col_obj in self.collections.items():
                existing_chunks = col_obj.get(include=["metadatas"])
                if existing_chunks and existing_chunks["ids"]:
                    update_ids = []
                    update_metas = []
                    for idx, cid in enumerate(existing_chunks["ids"]):
                        m = dict(existing_chunks["metadatas"][idx] or {})
                        needs_update = False
                        if "allowed_roles" not in m or not m["allowed_roles"]:
                            # Legacy chunk: grant access to all roles
                            m["allowed_roles"] = all_roles_str
                            m["uploader_username"] = m.get("uploader_username", "Unknown")
                            m["uploader_role"] = m.get("uploader_role", "Unknown")
                            for r in role_ids:
                                m[f"access_{r}"] = True
                            needs_update = True
                        else:
                            # Tagged chunk: ensure access flags match ONLY allowed_roles
                            allowed_list = [r.strip() for r in m["allowed_roles"].split(",") if r.strip()]
                            for r in role_ids:
                                flag = f"access_{r}"
                                should_have = r in allowed_list
                                if m.get(flag) != should_have:
                                    m[flag] = should_have
                                    needs_update = True

                        if needs_update:
                            update_ids.append(cid)
                            update_metas.append(m)
                    if update_ids:
                        col_obj.update(ids=update_ids, metadatas=update_metas)
                        logger.info("Synchronized %d chunks in ChromaDB collection '%s' with exact access tags.", len(update_ids), col_name)
        except Exception as e:
            logger.warning("ChromaDB chunk migration notice: %s", e)

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

    def create_document_record(
        self,
        doc_id: str,
        filename: str,
        content_hash: str,
        collection_name: str = "company_knowledge_base_gemini_3072",
        allowed_roles: Optional[List[str]] = None,
        uploader_username: Optional[str] = "Unknown",
        uploader_role: Optional[str] = "Unknown"
    ) -> None:
        """Create a new document ingestion record with 'processing' status and role access tags."""
        upload_date = datetime.utcnow().isoformat() + "Z"
        roles_json = json.dumps(allowed_roles or [])
        with self._get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, filename, content_hash, upload_date, chunk_count, status, collection_name, allowed_roles, uploader_username, uploader_role)
                VALUES (?, ?, ?, ?, 0, 'processing', ?, ?, ?, ?)
                """,
                (doc_id, filename, content_hash, upload_date, collection_name, roles_json, uploader_username or "Unknown", uploader_role or "Unknown")
            )
            conn.commit()
        logger.info(
            "Created metadata record for document %s (ID: %s, collection: %s, uploader: %s, role: %s, allowed_roles: %s)",
            filename, doc_id, collection_name, uploader_username, uploader_role, allowed_roles
        )

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
        """Retrieve list of all documents metadata with parsed allowed_roles and uploader info."""
        with self._get_sqlite_conn() as conn:
            if collection_name:
                cursor = conn.execute(
                    "SELECT id, filename, upload_date, chunk_count, status, collection_name, allowed_roles, uploader_username, uploader_role FROM documents WHERE collection_name = ? ORDER BY upload_date DESC",
                    (collection_name,)
                )
            else:
                cursor = conn.execute(
                    "SELECT id, filename, upload_date, chunk_count, status, collection_name, allowed_roles, uploader_username, uploader_role FROM documents ORDER BY upload_date DESC"
                )
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("allowed_roles"):
                    try:
                        item["allowed_roles"] = json.loads(item["allowed_roles"])
                    except Exception:
                        item["allowed_roles"] = [s.strip() for s in item["allowed_roles"].split(",") if s.strip()]
                else:
                    item["allowed_roles"] = []
                results.append(item)
            return results

    def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific document's metadata with parsed allowed_roles and uploader info."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT id, filename, upload_date, chunk_count, status, collection_name, allowed_roles, uploader_username, uploader_role FROM documents WHERE id = ?",
                (doc_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("allowed_roles"):
                try:
                    item["allowed_roles"] = json.loads(item["allowed_roles"])
                except Exception:
                    item["allowed_roles"] = [s.strip() for s in item["allowed_roles"].split(",") if s.strip()]
            else:
                item["allowed_roles"] = []
            return item

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
        collection_name: str = "company_knowledge_base_gemini_3072",
        allowed_roles: Optional[List[str]] = None,
        uploader_username: Optional[str] = "Unknown",
        uploader_role: Optional[str] = "Unknown"
    ) -> None:
        """
        Save chunks and their embeddings into ChromaDB collection with dual-stored access tags.
        Each chunk receives:
          1. allowed_roles comma-separated string for human readability.
          2. Individual boolean flags (e.g. access_admin: True, access_tester: True) for fast, native filtering in Phase 15.
        """
        ids = []
        metadatas = []
        documents = []
        roles_list = allowed_roles or []
        roles_str = ",".join(roles_list)

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_{idx}"
            ids.append(chunk_id)
            documents.append(chunk["text"])
            meta = {
                "document_id": doc_id,
                "source_filename": filename,
                "content_hash": content_hash,
                "page_number": chunk["page_number"],
                "chunk_index": chunk["chunk_index"],
                "upload_timestamp": chunk["timestamp"],
                "allowed_roles": roles_str,
                "uploader_username": uploader_username or "Unknown",
                "uploader_role": uploader_role or "Unknown"
            }
            # Set boolean filter flag for each permitted role
            for r in roles_list:
                meta[f"access_{r}"] = True
            metadatas.append(meta)

        # Insert to ChromaDB
        target_collection = self.collections.get(collection_name, self.collection)
        target_collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
        logger.info("Stored %d chunks in ChromaDB for document %s (ID: %s, collection: %s, roles: %s)", len(chunks), filename, doc_id, collection_name, roles_str)

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

    # ==========================================
    # Phase 11: Dynamic Role Management Methods
    # ==========================================

    def list_roles(self) -> List[Dict[str, Any]]:
        """List all roles ordered by hierarchy_position ascending."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                SELECT role_id, role_name, hierarchy_position, can_upload, is_protected
                FROM roles
                ORDER BY hierarchy_position ASC
                """
            )
            rows = cursor.fetchall()
            return [
                {
                    "role_id": row["role_id"],
                    "role_name": row["role_name"],
                    "hierarchy_position": row["hierarchy_position"],
                    "can_upload": bool(row["can_upload"]),
                    "is_protected": bool(row["is_protected"])
                }
                for row in rows
            ]

    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific role by role_id."""
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                """
                SELECT role_id, role_name, hierarchy_position, can_upload, is_protected
                FROM roles
                WHERE role_id = ?
                """,
                (role_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "role_id": row["role_id"],
                "role_name": row["role_name"],
                "hierarchy_position": row["hierarchy_position"],
                "can_upload": bool(row["can_upload"]),
                "is_protected": bool(row["is_protected"])
            }

    def get_users_count_by_role(self, role_id: str, role_name: Optional[str] = None) -> int:
        """Count how many users in the users table currently have this role assigned."""
        with self._get_sqlite_conn() as conn:
            if role_name:
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) FROM users
                    WHERE LOWER(role) = LOWER(?) OR LOWER(role) = LOWER(?)
                    """,
                    (role_id, role_name)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) FROM users
                    WHERE LOWER(role) = LOWER(?)
                    """,
                    (role_id,)
                )
            return cursor.fetchone()[0]

    def create_role(
        self,
        role_name: str,
        insert_below_role_id: Optional[str] = None,
        can_upload: bool = False,
        role_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a new role, calculates its hierarchy position, and shifts existing roles down by one.
        """
        clean_name = role_name.strip()
        clean_id = (role_id.strip().lower() if role_id else clean_name.lower().replace(" ", "_").replace("-", "_"))

        with self._get_sqlite_conn() as conn:
            # Check for existing role_id
            cursor = conn.execute("SELECT 1 FROM roles WHERE role_id = ?", (clean_id,))
            if cursor.fetchone():
                raise ValueError(f"Role with ID '{clean_id}' already exists.")

            # Calculate target hierarchy position
            if insert_below_role_id:
                cursor = conn.execute("SELECT hierarchy_position FROM roles WHERE role_id = ?", (insert_below_role_id,))
                ref_row = cursor.fetchone()
                if not ref_row:
                    raise ValueError(f"Reference role '{insert_below_role_id}' not found.")
                target_pos = ref_row["hierarchy_position"] + 1
            else:
                cursor = conn.execute("SELECT MAX(hierarchy_position) FROM roles")
                max_row = cursor.fetchone()[0]
                target_pos = (max_row or 0) + 1

            # Shift existing roles down
            conn.execute(
                "UPDATE roles SET hierarchy_position = hierarchy_position + 1 WHERE hierarchy_position >= ?",
                (target_pos,)
            )

            # Insert new role
            conn.execute(
                """
                INSERT INTO roles (role_id, role_name, hierarchy_position, can_upload, is_protected)
                VALUES (?, ?, ?, ?, 0)
                """,
                (clean_id, clean_name, target_pos, 1 if can_upload else 0)
            )
            conn.commit()

        created = self.get_role(clean_id)
        if not created:
            raise RuntimeError("Failed to retrieve newly created role.")
        return created

    def update_role(
        self,
        role_id: str,
        role_name: Optional[str] = None,
        can_upload: Optional[bool] = None,
        insert_below_role_id: Optional[str] = None,
        hierarchy_position: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Updates role name, upload permission, or repositions role in the hierarchy.
        Protected roles cannot be repositioned.
        """
        current_role = self.get_role(role_id)
        if not current_role:
            raise ValueError(f"Role with ID '{role_id}' not found.")

        # Block repositioning protected roles
        if current_role["is_protected"] and (insert_below_role_id is not None or hierarchy_position is not None):
            raise ValueError(f"Cannot reposition protected role '{current_role['role_name']}'.")

        with self._get_sqlite_conn() as conn:
            current_pos = current_role["hierarchy_position"]
            target_pos = None

            if insert_below_role_id:
                if insert_below_role_id == role_id:
                    raise ValueError("Cannot position a role below itself.")
                ref_role = self.get_role(insert_below_role_id)
                if not ref_role:
                    raise ValueError(f"Reference role '{insert_below_role_id}' not found.")
                ref_pos = ref_role["hierarchy_position"]
                target_pos = ref_pos if current_pos < ref_pos else ref_pos + 1
            elif hierarchy_position is not None:
                cursor = conn.execute("SELECT MAX(hierarchy_position) FROM roles")
                max_pos = cursor.fetchone()[0] or 1
                target_pos = max(1, min(hierarchy_position, max_pos))

            # Apply repositioning shift if target_pos differs from current_pos
            if target_pos is not None and target_pos != current_pos:
                if target_pos > current_pos:
                    conn.execute(
                        "UPDATE roles SET hierarchy_position = hierarchy_position - 1 WHERE hierarchy_position > ? AND hierarchy_position <= ?",
                        (current_pos, target_pos)
                    )
                else:
                    conn.execute(
                        "UPDATE roles SET hierarchy_position = hierarchy_position + 1 WHERE hierarchy_position >= ? AND hierarchy_position < ?",
                        (target_pos, current_pos)
                    )
                conn.execute(
                    "UPDATE roles SET hierarchy_position = ? WHERE role_id = ?",
                    (target_pos, role_id)
                )

            # Update role_name if provided
            if role_name is not None and role_name.strip():
                conn.execute(
                    "UPDATE roles SET role_name = ? WHERE role_id = ?",
                    (role_name.strip(), role_id)
                )

            # Update can_upload if provided
            if can_upload is not None:
                conn.execute(
                    "UPDATE roles SET can_upload = ? WHERE role_id = ?",
                    (1 if can_upload else 0, role_id)
                )

            conn.commit()

        updated = self.get_role(role_id)
        if not updated:
            raise RuntimeError("Failed to retrieve updated role.")
        return updated

    def delete_role(self, role_id: str) -> bool:
        """
        Deletes a role if not protected and not assigned to any user.
        Shifts all roles below it up by one.
        """
        role = self.get_role(role_id)
        if not role:
            raise ValueError(f"Role with ID '{role_id}' not found.")

        if role["is_protected"]:
            raise ValueError(f"Cannot delete protected role '{role['role_name']}'.")

        user_count = self.get_users_count_by_role(role_id, role["role_name"])
        if user_count > 0:
            raise ValueError(f"Cannot delete role '{role['role_name']}': {user_count} user(s) are currently assigned to this role.")

        with self._get_sqlite_conn() as conn:
            del_pos = role["hierarchy_position"]
            conn.execute("DELETE FROM roles WHERE role_id = ?", (role_id,))
            conn.execute(
                "UPDATE roles SET hierarchy_position = hierarchy_position - 1 WHERE hierarchy_position > ?",
                (del_pos,)
            )
            conn.commit()

        return True

    def get_roles_below(self, role_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all roles positioned strictly below the given role in the hierarchy
        (i.e. hierarchy_position > current_role_position).
        """
        role = self.get_role(role_id)
        if not role:
            # Fallback lookup
            roles = self.list_roles()
            for r in roles:
                if role_id.lower() in (r["role_id"].lower(), r["role_name"].lower()):
                    role = r
                    break
        if not role:
            return []
        
        my_pos = role["hierarchy_position"]
        with self._get_sqlite_conn() as conn:
            cursor = conn.execute(
                "SELECT role_id, role_name, hierarchy_position, can_upload, is_protected FROM roles WHERE hierarchy_position > ? ORDER BY hierarchy_position ASC",
                (my_pos,)
            )
            return [dict(row) for row in cursor.fetchall()]

# Global database service instance
db_service = DBService()

