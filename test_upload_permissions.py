import io
import uuid
import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.services.db_service import db_service
from app.services.auth_service import create_access_token, hash_password

class TestUploadPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        
        # Setup test accounts for different roles
        with db_service._get_sqlite_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p13_admin', ?, 'admin', '2026-09-03')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p13_cofounder', ?, 'co_founder', '2026-09-03')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p13_programmer', ?, 'programmer', '2026-09-03')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p13_tester', ?, 'tester', '2026-09-03')", (hash_password('pass123'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('p13_employee', ?, 'employee', '2026-09-03')", (hash_password('pass123'),))
            
            # Ensure standard default can_upload states
            conn.execute("UPDATE roles SET can_upload = 1, role_name = 'Admin' WHERE role_id = 'admin'")
            conn.execute("UPDATE roles SET can_upload = 1, role_name = 'Co-founder' WHERE role_id = 'co_founder'")
            conn.execute("UPDATE roles SET can_upload = 0, role_name = 'Programmer' WHERE role_id = 'programmer'")
            conn.execute("UPDATE roles SET can_upload = 0, role_name = 'Tester' WHERE role_id = 'tester'")
            conn.execute("UPDATE roles SET can_upload = 0, role_name = 'Employee' WHERE role_id = 'employee'")
            conn.commit()

        cls.admin_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p13_admin', 'role': 'admin'})}"}
        cls.cofounder_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p13_cofounder', 'role': 'co_founder'})}"}
        cls.prog_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p13_programmer', 'role': 'programmer'})}"}
        cls.tester_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p13_tester', 'role': 'tester'})}"}
        cls.emp_headers = {"Authorization": f"Bearer {create_access_token({'username': 'p13_employee', 'role': 'employee'})}"}

    def _create_dummy_file(self, prefix: str = "doc"):
        uid = uuid.uuid4().hex[:8]
        filename = f"{prefix}_{uid}.txt"
        content = f"Unique test document content {uid} for upload permissions."
        return ("files", (filename, io.BytesIO(content.encode("utf-8")), "text/plain"))

    def test_01_admin_can_upload(self):
        """Admin (can_upload=True) can upload documents successfully."""
        files = [self._create_dummy_file("admin_doc")]
        resp = self.client.post("/admin/upload", files=files, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["uploaded"]), 1)

    def test_02_cofounder_can_upload(self):
        """Co-founder (can_upload=True by default) can upload documents successfully."""
        files = [self._create_dummy_file("cofounder_doc")]
        resp = self.client.post("/admin/upload", files=files, headers=self.cofounder_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["uploaded"]), 1)

    def test_03_blocked_roles_rejected_403(self):
        """Programmer, Tester, and Employee (can_upload=False) get 403 Forbidden on upload."""
        for headers, role_name in [
            (self.prog_headers, "Programmer"),
            (self.tester_headers, "Tester"),
            (self.emp_headers, "Employee")
        ]:
            files = [self._create_dummy_file(f"blocked_{role_name.lower()}")]
            resp = self.client.post("/admin/upload", files=files, headers=headers)
            self.assertEqual(resp.status_code, 403)
            err = resp.json().get("detail") or resp.json().get("error")
            self.assertIn("does not have upload permissions", err)

    def test_04_dynamic_permission_toggle(self):
        """Toggle can_upload=True for Tester -> upload succeeds -> toggle back to False -> upload blocked."""
        # 1. Admin grants can_upload to tester
        patch_resp = self.client.patch("/admin/roles/tester", json={"can_upload": True}, headers=self.admin_headers)
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.json()["can_upload"])

        # 2. Tester uploads successfully
        files = [self._create_dummy_file("tester_elevated")]
        resp_elevated = self.client.post("/admin/upload", files=files, headers=self.tester_headers)
        self.assertEqual(resp_elevated.status_code, 200)
        self.assertEqual(len(resp_elevated.json()["uploaded"]), 1)

        # 3. Admin revokes can_upload from tester
        revoke_resp = self.client.patch("/admin/roles/tester", json={"can_upload": False}, headers=self.admin_headers)
        self.assertEqual(revoke_resp.status_code, 200)
        self.assertFalse(revoke_resp.json()["can_upload"])

        # 4. Tester upload is immediately blocked with 403
        files_blocked = [self._create_dummy_file("tester_revoked")]
        resp_blocked = self.client.post("/admin/upload", files=files_blocked, headers=self.tester_headers)
        self.assertEqual(resp_blocked.status_code, 403)

    def test_05_document_deletion_remains_admin_only(self):
        """Non-admin roles with can_upload (Co-founder) cannot delete documents."""
        # Fetch an existing document ID
        doc_list = self.client.get("/admin/documents", headers=self.admin_headers).json()
        self.assertGreater(len(doc_list), 0)
        target_doc_id = doc_list[0]["id"]

        # Co-founder attempts delete -> 403 Forbidden
        del_cofounder = self.client.delete(f"/admin/documents/{target_doc_id}", headers=self.cofounder_headers)
        self.assertEqual(del_cofounder.status_code, 403)

        # Admin attempts delete -> 200 OK
        del_admin = self.client.delete(f"/admin/documents/{target_doc_id}", headers=self.admin_headers)
        self.assertEqual(del_admin.status_code, 200)

    def test_06_auth_me_and_login_metadata(self):
        """Verify GET /auth/me and POST /auth/login return dynamic can_upload and is_admin metadata."""
        # Check login response
        login_resp = self.client.post("/auth/login", json={"username": "p13_cofounder", "password": "pass123"})
        self.assertEqual(login_resp.status_code, 200)
        data = login_resp.json()
        self.assertTrue(data["can_upload"])
        self.assertFalse(data["is_admin"])

        # Check /auth/me for co-founder
        me_cofounder = self.client.get("/auth/me", headers=self.cofounder_headers).json()
        self.assertTrue(me_cofounder["can_upload"])
        self.assertFalse(me_cofounder["is_admin"])

        # Check /auth/me for admin
        me_admin = self.client.get("/auth/me", headers=self.admin_headers).json()
        self.assertTrue(me_admin["can_upload"])
        self.assertTrue(me_admin["is_admin"])

        # Check /auth/me for employee
        me_emp = self.client.get("/auth/me", headers=self.emp_headers).json()
        self.assertFalse(me_emp["can_upload"])
        self.assertFalse(me_emp["is_admin"])

if __name__ == "__main__":
    unittest.main()
