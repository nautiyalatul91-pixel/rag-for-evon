import io
import json
import uuid
import unittest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app
from app.services.db_service import db_service
from app.services.retrieval_service import retrieval_service
from app.services.auth_service import create_access_token, hash_password

class TestRetrievalFiltering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        # Setup standard test users for all 5 roles
        with db_service._get_sqlite_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p15_admin', ?, 'admin', '2026-09-09')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p15_cofounder', ?, 'co_founder', '2026-09-09')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p15_programmer', ?, 'programmer', '2026-09-09')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p15_tester', ?, 'tester', '2026-09-09')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p15_employee', ?, 'employee', '2026-09-09')", (hash_password('pass123'),))

            conn.execute("UPDATE roles SET can_upload = 1, hierarchy_position = 1 WHERE role_id = 'admin'")
            conn.execute("UPDATE roles SET can_upload = 1, hierarchy_position = 2 WHERE role_id = 'co_founder'")
            conn.execute("UPDATE roles SET can_upload = 0, hierarchy_position = 3 WHERE role_id = 'programmer'")
            conn.execute("UPDATE roles SET can_upload = 0, hierarchy_position = 4 WHERE role_id = 'tester'")
            conn.execute("UPDATE roles SET can_upload = 0, hierarchy_position = 5 WHERE role_id = 'employee'")
            conn.commit()

        cls.admin_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p15_admin', 'role': 'admin'})}"}
        cls.cofounder_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p15_cofounder', 'role': 'co_founder'})}"}
        cls.programmer_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p15_programmer', 'role': 'programmer'})}"}
        cls.tester_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p15_tester', 'role': 'tester'})}"}
        cls.employee_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p15_employee', 'role': 'employee'})}"}

        # Cleanup any previous stale test documents
        docs = db_service.get_all_documents()
        for doc in docs:
            fn = doc.get("filename", "")
            if any(fn.startswith(pfx) for pfx in ("falcon_key_", "diwali_holiday_", "exec_bonus_", "secret_vault_")):
                db_service.delete_document(doc["id"])

    def setUp(self):
        self.created_doc_ids = []
        self.llm_patcher = patch("app.routes.chat.llm_service.generate_answer", side_effect=self._mock_generate_answer)
        self.mock_llm = self.llm_patcher.start()

    def tearDown(self):
        self.llm_patcher.stop()
        for did in getattr(self, "created_doc_ids", []):
            try:
                db_service.delete_document(did)
            except Exception:
                pass
        self.created_doc_ids = []

    def _mock_generate_answer(self, question, context_chunks, conversation_id=None):
        sources = [c["filename"] for c in context_chunks]
        texts = [c["text"] for c in context_chunks]
        combined = " ".join(texts)
        return (f"Factual answer based on {sources}: {combined}", False)

    def _upload_test_document(self, filename: str, content: str, uploader_headers: dict, allowed_roles: list = None):
        files = [("files", (filename, io.BytesIO(content.encode("utf-8")), "text/plain"))]
        data = {"collection": "company_knowledge_base_gemini_3072"}
        if allowed_roles is not None:
            data["allowed_roles"] = json.dumps(allowed_roles)
        resp = self.client.post("/admin/upload", files=files, data=data, headers=uploader_headers)
        self.assertEqual(resp.status_code, 200, f"Failed upload for {filename}: {resp.text}")
        res_json = resp.json()
        self.assertEqual(len(res_json["uploaded"]), 1)
        doc_id = res_json["uploaded"][0]["document_id"]
        self.created_doc_ids.append(doc_id)
        return doc_id

    def test_01_cofounder_upload_admin_tester_access(self):
        """
        Co-founder uploads document tagged for [admin, co_founder, tester].
        Admin and Tester retrieve it successfully.
        Programmer and Employee cannot retrieve it (0 chunks, standard fallback, zero leakage).
        """
        uid = uuid.uuid4().hex[:6]
        filename = f"falcon_key_{uid}.txt"
        secret_token = f"FALCON-SECRET-{uid.upper()}"
        content = f"The confidential Project Falcon production release token is {secret_token}. Access is restricted."
        
        doc_id = self._upload_test_document(
            filename=filename,
            content=content,
            uploader_headers=self.cofounder_headers,
            allowed_roles=["tester"]
        )

        question = "What is the confidential Project Falcon production release token?"

        # 1. Admin Query -> Should retrieve document
        resp_admin = self.client.post("/chat", json={"question": question}, headers=self.admin_headers)
        self.assertEqual(resp_admin.status_code, 200)
        data_admin = resp_admin.json()
        sources_admin = [s["filename"] for s in data_admin["sources"]]
        self.assertIn(filename, sources_admin)
        self.assertIn(secret_token, data_admin["answer"])

        # 2. Tester Query -> Should retrieve document
        resp_tester = self.client.post("/chat", json={"question": question}, headers=self.tester_headers)
        self.assertEqual(resp_tester.status_code, 200)
        data_tester = resp_tester.json()
        sources_tester = [s["filename"] for s in data_tester["sources"]]
        self.assertIn(filename, sources_tester)
        self.assertIn(secret_token, data_tester["answer"])

        # 3. Programmer Query -> Must NOT retrieve document
        resp_prog = self.client.post("/chat", json={"question": question}, headers=self.programmer_headers)
        self.assertEqual(resp_prog.status_code, 200)
        data_prog = resp_prog.json()
        sources_prog = [s["filename"] for s in data_prog["sources"]]
        self.assertNotIn(filename, sources_prog)
        self.assertNotIn(secret_token, data_prog["answer"])

        # 4. Employee Query -> Must NOT retrieve document
        resp_emp = self.client.post("/chat", json={"question": question}, headers=self.employee_headers)
        self.assertEqual(resp_emp.status_code, 200)
        data_emp = resp_emp.json()
        sources_emp = [s["filename"] for s in data_emp["sources"]]
        self.assertNotIn(filename, sources_emp)
        self.assertNotIn(secret_token, data_emp["answer"])

    def test_02_mixed_scenario_zero_leakage(self):
        """
        Mixed Scenario:
        - Doc A: Public holiday doc accessible to all roles.
        - Doc B: Executive bonus doc accessible only to [admin, co_founder].
        Programmer queries about both:
        - Receives answer from Doc A only.
        - Zero information from Doc B leaks into prompt or answer.
        """
        uid = uuid.uuid4().hex[:6]
        fn_pub = f"diwali_holiday_{uid}.txt"
        fn_priv = f"exec_bonus_{uid}.txt"
        bonus_secret = f"POOL-BONUS-{uid.upper()}-99MILLION"

        content_pub = f"The official corporate holiday for Diwali 2026 (Ref: {uid}) is November 12, 2026. All offices remain closed."
        content_priv = f"The executive secret bonus pool for 2026 (Ref: {uid}) is {bonus_secret} reserved exclusively for top leadership."

        # Upload Doc A as admin with downline: all roles
        self._upload_test_document(
            filename=fn_pub,
            content=content_pub,
            uploader_headers=self.admin_headers,
            allowed_roles=["co_founder", "programmer", "tester", "employee"]
        )

        # Upload Doc B as admin with no downline (admin only)
        self._upload_test_document(
            filename=fn_priv,
            content=content_priv,
            uploader_headers=self.admin_headers,
            allowed_roles=None
        )

        query = "What is the date for Diwali 2026 holiday and what is the executive bonus pool allocation?"

        # 1. Programmer query
        resp_prog = self.client.post("/chat", json={"question": query}, headers=self.programmer_headers)
        self.assertEqual(resp_prog.status_code, 200)
        data_prog = resp_prog.json()
        sources_prog = [s["filename"] for s in data_prog["sources"]]
        
        # Programmer sees only public doc, NOT private doc
        self.assertIn(fn_pub, sources_prog)
        self.assertNotIn(fn_priv, sources_prog)
        self.assertNotIn(bonus_secret, data_prog["answer"])
        self.assertNotIn("99MILLION", data_prog["answer"])
        self.assertIn("November 12", data_prog["answer"])

        # 2. Admin query
        resp_admin = self.client.post("/chat", json={"question": query}, headers=self.admin_headers)
        self.assertEqual(resp_admin.status_code, 200)
        data_admin = resp_admin.json()
        sources_admin = [s["filename"] for s in data_admin["sources"]]
        self.assertIn(fn_pub, sources_admin)
        self.assertIn(fn_priv, sources_admin)
        self.assertIn(bonus_secret, data_admin["answer"])

    def test_03_no_accessible_documents_returns_fallback_safely(self):
        """
        When a user queries a collection where zero documents are accessible to their role,
        the system returns the standard Phase 2 fallback gracefully without error.
        """
        question = "What is our quantum blockchain deployment protocol?"
        resp = self.client.post(
            "/chat",
            json={"question": question, "collection": "evon_capabilities"},
            headers=self.employee_headers
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["sources"], [])
        self.assertEqual(data["answer"], "I don't have information about that in the company knowledge base.")

    def test_04_direct_retrieval_service_where_clause(self):
        """
        Directly verify retrieval_service.retrieve_relevant_chunks:
        Ensures ChromaDB where clause filters out unauthorized chunks before L2 thresholding.
        """
        uid = uuid.uuid4().hex[:6]
        fn = f"secret_vault_{uid}.txt"
        secret_text = f"Vault passcode is VAULT-{uid.upper()}."

        self._upload_test_document(
            filename=fn,
            content=secret_text,
            uploader_headers=self.admin_headers,
            allowed_roles=None  # strictly admin
        )

        query = f"Vault passcode {uid.upper()}"

        # Admin retrieval
        chunks_admin, _ = retrieval_service.retrieve_relevant_chunks(
            question=query,
            k=5,
            collection_name="company_knowledge_base_gemini_3072",
            user_role="admin",
            username="p15_admin"
        )
        filenames_admin = [c["filename"] for c in chunks_admin]
        self.assertIn(fn, filenames_admin)

        # Programmer retrieval
        chunks_prog, _ = retrieval_service.retrieve_relevant_chunks(
            question=query,
            k=5,
            collection_name="company_knowledge_base_gemini_3072",
            user_role="programmer",
            username="p15_programmer"
        )
        filenames_prog = [c["filename"] for c in chunks_prog]
        self.assertNotIn(fn, filenames_prog)

if __name__ == "__main__":
    unittest.main()
