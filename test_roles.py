import unittest
import unittest.mock
import sqlite3
from fastapi.testclient import TestClient
from app.main import app
from app.services.db_service import db_service
from app.services.auth_service import create_access_token, hash_password

class TestDynamicRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        
        # Ensure clean test admin user and standard baseline roles
        with db_service._get_sqlite_conn() as conn:
            conn.execute("DELETE FROM users WHERE role NOT IN ('admin', 'employee')")
            conn.execute("DELETE FROM roles WHERE role_id NOT IN ('admin', 'co_founder', 'programmer', 'tester', 'employee')")
            conn.execute("UPDATE roles SET role_name = 'Admin', hierarchy_position = 1, can_upload = 1, is_protected = 1 WHERE role_id = 'admin'")
            conn.execute("UPDATE roles SET role_name = 'Co-founder', hierarchy_position = 2, can_upload = 1, is_protected = 0 WHERE role_id = 'co_founder'")
            conn.execute("UPDATE roles SET role_name = 'Programmer', hierarchy_position = 3, can_upload = 0, is_protected = 0 WHERE role_id = 'programmer'")
            conn.execute("UPDATE roles SET role_name = 'Tester', hierarchy_position = 4, can_upload = 0, is_protected = 0 WHERE role_id = 'tester'")
            conn.execute("UPDATE roles SET role_name = 'Employee', hierarchy_position = 5, can_upload = 0, is_protected = 0 WHERE role_id = 'employee'")
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('admin_role_test', ?, 'admin', '2026-09-02')", (hash_password('adminpass'),))
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('employee_role_test', ?, 'employee', '2026-09-02')", (hash_password('employeepass'),))
            conn.commit()

        cls.admin_token = create_access_token({"username": "admin_role_test", "role": "admin"})
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        
        cls.employee_token = create_access_token({"username": "employee_role_test", "role": "employee"})
        cls.employee_headers = {"Authorization": f"Bearer {cls.employee_token}"}

    def test_01_default_roles_seeded(self):
        """Verify default 5 roles are seeded in exact hierarchy order."""
        response = self.client.get("/admin/roles", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        roles = response.json()
        
        self.assertEqual(len(roles), 5)
        
        expected = [
            ("admin", "Admin", 1, True, True),
            ("co_founder", "Co-founder", 2, True, False),
            ("programmer", "Programmer", 3, False, False),
            ("tester", "Tester", 4, False, False),
            ("employee", "Employee", 5, False, False),
        ]
        
        for idx, exp in enumerate(expected):
            self.assertEqual(roles[idx]["role_id"], exp[0])
            self.assertEqual(roles[idx]["role_name"], exp[1])
            self.assertEqual(roles[idx]["hierarchy_position"], exp[2])
            self.assertEqual(roles[idx]["can_upload"], exp[3])
            self.assertEqual(roles[idx]["is_protected"], exp[4])

    def test_02_create_role_with_shifting(self):
        """Create 'Designer' inserted below 'Programmer' and verify hierarchy shifts."""
        payload = {
            "role_name": "Designer",
            "insert_below_role_id": "programmer",
            "can_upload": False
        }
        create_resp = self.client.post("/admin/roles", json=payload, headers=self.admin_headers)
        self.assertEqual(create_resp.status_code, 201)
        created = create_resp.json()
        
        self.assertEqual(created["role_id"], "designer")
        self.assertEqual(created["role_name"], "Designer")
        self.assertEqual(created["hierarchy_position"], 4)
        self.assertEqual(created["can_upload"], False)
        self.assertEqual(created["is_protected"], False)
        
        # Verify full list and shifted positions
        list_resp = self.client.get("/admin/roles", headers=self.admin_headers)
        self.assertEqual(list_resp.status_code, 200)
        roles_by_id = {r["role_id"]: r for r in list_resp.json()}
        
        self.assertEqual(roles_by_id["admin"]["hierarchy_position"], 1)
        self.assertEqual(roles_by_id["co_founder"]["hierarchy_position"], 2)
        self.assertEqual(roles_by_id["programmer"]["hierarchy_position"], 3)
        self.assertEqual(roles_by_id["designer"]["hierarchy_position"], 4)
        self.assertEqual(roles_by_id["tester"]["hierarchy_position"], 5)  # Shifted down
        self.assertEqual(roles_by_id["employee"]["hierarchy_position"], 6) # Shifted down

    def test_03_block_delete_protected_role(self):
        """Attempt to delete 'admin' role and confirm it is blocked."""
        response = self.client.delete("/admin/roles/admin", headers=self.admin_headers)
        self.assertEqual(response.status_code, 400)
        err = response.json().get("detail") or response.json().get("error")
        self.assertIn("Cannot delete protected role", err)

    def test_04_block_delete_assigned_role(self):
        """Attempt to delete role assigned to an existing user and confirm blocked."""
        # Create a test user with role 'tester'
        with db_service._get_sqlite_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('test_assigned_user', 'hash', 'tester', '2026-09-02')")
            conn.commit()
            
        response = self.client.delete("/admin/roles/tester", headers=self.admin_headers)
        self.assertEqual(response.status_code, 400)
        err = response.json().get("detail") or response.json().get("error")
        self.assertIn("user(s) are currently assigned", err)
        
        # Clean up assigned test user
        with db_service._get_sqlite_conn() as conn:
            conn.execute("DELETE FROM users WHERE username = 'test_assigned_user'")
            conn.commit()

    def test_05_update_role_and_reposition(self):
        """Update Designer role name, upload permission, and reposition immediately below Admin."""
        payload = {
            "role_name": "Lead Designer",
            "can_upload": True,
            "insert_below_role_id": "admin"
        }
        update_resp = self.client.patch("/admin/roles/designer", json=payload, headers=self.admin_headers)
        self.assertEqual(update_resp.status_code, 200)
        updated = update_resp.json()
        
        self.assertEqual(updated["role_name"], "Lead Designer")
        self.assertEqual(updated["can_upload"], True)
        self.assertEqual(updated["hierarchy_position"], 2) # Now immediately below Admin
        
        # Verify all positions
        list_resp = self.client.get("/admin/roles", headers=self.admin_headers)
        roles = list_resp.json()
        ordered_ids = [r["role_id"] for r in roles]
        self.assertEqual(ordered_ids, ["admin", "designer", "co_founder", "programmer", "tester", "employee"])
        
        for idx, r in enumerate(roles):
            self.assertEqual(r["hierarchy_position"], idx + 1)

    def test_06_successful_deletion_and_shift_up(self):
        """Delete Designer and confirm lower roles shift back up."""
        del_resp = self.client.delete("/admin/roles/designer", headers=self.admin_headers)
        self.assertEqual(del_resp.status_code, 200)
        
        list_resp = self.client.get("/admin/roles", headers=self.admin_headers)
        roles = list_resp.json()
        self.assertEqual(len(roles), 5)
        
        ordered_ids = [r["role_id"] for r in roles]
        self.assertEqual(ordered_ids, ["admin", "co_founder", "programmer", "tester", "employee"])
        
        for idx, r in enumerate(roles):
            self.assertEqual(r["hierarchy_position"], idx + 1)

    def test_07_auth_and_permissions(self):
        """Verify non-admin cannot access role management endpoints."""
        # Employee
        r1 = self.client.get("/admin/roles", headers=self.employee_headers)
        self.assertEqual(r1.status_code, 403)
        
        r2 = self.client.post("/admin/roles", json={"role_name": "Hacker"}, headers=self.employee_headers)
        self.assertEqual(r2.status_code, 403)
        
        # Unauthenticated
        r3 = self.client.get("/admin/roles")
        self.assertEqual(r3.status_code, 401)

    def test_08_regression_smoke_test(self):
        """Confirm existing admin document listing and chat endpoint still work without regression."""
        # 1. Admin document listing
        doc_resp = self.client.get("/admin/documents", headers=self.admin_headers)
        self.assertEqual(doc_resp.status_code, 200)
        
        # 2. Conversational chat query (mock LLM call to prevent quota exhaustion)
        with unittest.mock.patch("app.routes.chat.llm_service.generate_answer", return_value=("Mocked policy answer", False)):
            chat_resp = self.client.post(
                "/chat",
                json={"question": "What is the policy?"},
                headers=self.employee_headers
            )
            self.assertEqual(chat_resp.status_code, 200)
            self.assertIn("answer", chat_resp.json())
            self.assertEqual(chat_resp.json()["answer"], "Mocked policy answer")

if __name__ == "__main__":
    unittest.main()
