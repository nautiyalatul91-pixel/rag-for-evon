import io
import json
import uuid
import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.services.db_service import db_service
from app.services.auth_service import create_access_token, hash_password

class TestDocumentAccessTagging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        # Setup standard users for test roles
        with db_service._get_sqlite_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p14_admin', ?, 'admin', '2026-09-03')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p14_cofounder', ?, 'co_founder', '2026-09-03')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p14_employee', ?, 'employee', '2026-09-03')", (hash_password('pass123'),))
            
            # Ensure standard default roles and can_upload flags
            conn.execute("UPDATE roles SET can_upload = 1, hierarchy_position = 1 WHERE role_id = 'admin'")
            conn.execute("UPDATE roles SET can_upload = 1, hierarchy_position = 2 WHERE role_id = 'co_founder'")
            conn.execute("UPDATE roles SET can_upload = 0, hierarchy_position = 3 WHERE role_id = 'programmer'")
            conn.execute("UPDATE roles SET can_upload = 0, hierarchy_position = 4 WHERE role_id = 'tester'")
            conn.execute("UPDATE roles SET can_upload = 0, hierarchy_position = 5 WHERE role_id = 'employee'")
            conn.commit()

        cls.admin_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p14_admin', 'role': 'admin'})}"}
        cls.cofounder_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p14_cofounder', 'role': 'co_founder'})}"}
        cls.emp_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p14_employee', 'role': 'employee'})}"}

    def _create_dummy_file(self, prefix: str = "doc"):
        uid = uuid.uuid4().hex[:8]
        filename = f"{prefix}_{uid}.txt"
        content = f"Unique document content {uid} for Phase 14 access tagging verification."
        return ("files", (filename, io.BytesIO(content.encode("utf-8")), "text/plain"))

    def test_01_get_roles_below_me(self):
        """Verify GET /admin/roles/below-me returns only downline roles."""
        # 1. Admin (position 1) -> all 4 roles below
        admin_resp = self.client.get("/admin/roles/below-me", headers=self.admin_headers)
        self.assertEqual(admin_resp.status_code, 200)
        admin_downline = [r["role_id"] for r in admin_resp.json()]
        self.assertNotIn("admin", admin_downline)
        self.assertIn("co_founder", admin_downline)
        self.assertIn("employee", admin_downline)

        # 2. Co-founder (position 2) -> roles with position > 2
        cf_resp = self.client.get("/admin/roles/below-me", headers=self.cofounder_headers)
        self.assertEqual(cf_resp.status_code, 200)
        cf_downline = [r["role_id"] for r in cf_resp.json()]
        self.assertNotIn("admin", cf_downline)
        self.assertNotIn("co_founder", cf_downline)
        self.assertIn("programmer", cf_downline)
        self.assertIn("tester", cf_downline)
        self.assertIn("employee", cf_downline)

    def test_02_cofounder_upload_with_downline_selection(self):
        """Co-founder uploads with downline role 'tester' -> access list is [admin, co_founder, tester]."""
        files = [self._create_dummy_file("cf_tagged")]
        data = {
            "collection": "company_knowledge_base_gemini_3072",
            "allowed_roles": json.dumps(["tester"])
        }
        resp = self.client.post("/admin/upload", files=files, data=data, headers=self.cofounder_headers)
        self.assertEqual(resp.status_code, 200)
        res_json = resp.json()
        self.assertEqual(len(res_json["uploaded"]), 1)
        doc_info = res_json["uploaded"][0]
        doc_id = doc_info["document_id"]
        self.assertEqual(doc_info["allowed_roles"], ["admin", "co_founder", "tester"])
        self.assertEqual(doc_info["uploader_username"], "p14_cofounder")
        self.assertEqual(doc_info["uploader_role"], "co_founder")

        # Verify directly in SQLite
        with db_service._get_sqlite_conn() as conn:
            row = conn.execute("SELECT allowed_roles, uploader_username, uploader_role FROM documents WHERE id = ?", (doc_id,)).fetchone()
            self.assertIsNotNone(row)
            stored_roles = json.loads(row["allowed_roles"])
            self.assertEqual(stored_roles, ["admin", "co_founder", "tester"])
            self.assertEqual(row["uploader_username"], "p14_cofounder")
            self.assertEqual(row["uploader_role"], "co_founder")

        # Verify dual-storage in ChromaDB metadata
        col = db_service.collection
        chunks = col.get(where={"document_id": doc_id}, include=["metadatas"])
        self.assertGreater(len(chunks["ids"]), 0)
        meta = chunks["metadatas"][0]
        self.assertTrue(meta.get("access_admin"))
        self.assertTrue(meta.get("access_co_founder"))
        self.assertTrue(meta.get("access_tester"))
        self.assertFalse(meta.get("access_employee", False))
        self.assertIn("admin,co_founder,tester", meta.get("allowed_roles", ""))

    def test_03_admin_upload_no_downline_selection(self):
        """Admin uploads with no downline roles -> access list is exactly [admin]."""
        files = [self._create_dummy_file("admin_strict")]
        data = {"collection": "company_knowledge_base_gemini_3072"}
        resp = self.client.post("/admin/upload", files=files, data=data, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        res_json = resp.json()
        self.assertEqual(len(res_json["uploaded"]), 1)
        doc_info = res_json["uploaded"][0]
        doc_id = doc_info["document_id"]
        self.assertEqual(doc_info["allowed_roles"], ["admin"])
        self.assertEqual(doc_info["uploader_username"], "p14_admin")
        self.assertEqual(doc_info["uploader_role"], "admin")

        # Verify directly in SQLite
        with db_service._get_sqlite_conn() as conn:
            row = conn.execute("SELECT allowed_roles, uploader_username, uploader_role FROM documents WHERE id = ?", (doc_id,)).fetchone()
            self.assertEqual(json.loads(row["allowed_roles"]), ["admin"])

        # Verify in ChromaDB
        col = db_service.collection
        chunks = col.get(where={"document_id": doc_id}, include=["metadatas"])
        meta = chunks["metadatas"][0]
        self.assertTrue(meta.get("access_admin"))
        self.assertFalse(meta.get("access_co_founder", False))

    def test_04_security_rejection_upline_role(self):
        """Attempting to select an upline role or invalid role returns 400 Bad Request."""
        # Co-founder attempting to manually select 'admin'
        files1 = [self._create_dummy_file("illegal_admin")]
        data1 = {"allowed_roles": json.dumps(["admin"])}
        resp1 = self.client.post("/admin/upload", files=files1, data=data1, headers=self.cofounder_headers)
        self.assertEqual(resp1.status_code, 400)
        err_msg = resp1.json().get("error") or resp1.json().get("detail") or ""
        self.assertIn("cannot be manually selected", err_msg)

        # Co-founder attempting to manually select own role 'co_founder'
        files2 = [self._create_dummy_file("illegal_self")]
        data2 = {"allowed_roles": json.dumps(["co_founder"])}
        resp2 = self.client.post("/admin/upload", files=files2, data=data2, headers=self.cofounder_headers)
        self.assertEqual(resp2.status_code, 400)

        # Co-founder attempting to select a nonexistent role
        files3 = [self._create_dummy_file("illegal_unknown")]
        data3 = {"allowed_roles": json.dumps(["super_vip"])}
        resp3 = self.client.post("/admin/upload", files=files3, data=data3, headers=self.cofounder_headers)
        self.assertEqual(resp3.status_code, 400)

    def test_05_preexisting_documents_migration_and_accountability(self):
        """Verify pre-existing documents have valid allowed_roles and display in /admin/documents."""
        # Verify via GET /admin/documents
        resp = self.client.get("/admin/documents", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        docs = resp.json()
        self.assertGreater(len(docs), 0)
        
        for doc in docs:
            self.assertIn("allowed_roles", doc)
            self.assertIsInstance(doc["allowed_roles"], list)
            self.assertGreater(len(doc["allowed_roles"]), 0)
            self.assertIn("uploader_username", doc)
            self.assertIn("uploader_role", doc)

if __name__ == "__main__":
    unittest.main()
