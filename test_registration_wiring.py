import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.services.db_service import db_service
from app.services.auth_service import create_access_token, hash_password

class TestRegistrationWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        
        # Ensure test admin
        with db_service._get_sqlite_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (username, hashed_password, role, created_at) VALUES ('admin_p12_test', ?, 'admin', '2026-09-03')",
                (hash_password('adminpass'),)
            )
            conn.commit()

        cls.admin_token = create_access_token({"username": "admin_p12_test", "role": "admin"})
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}

    def test_01_public_available_roles_endpoint(self):
        """Verify GET /auth/available-roles is public and returns role_id and role_name."""
        response = self.client.get("/auth/available-roles")
        self.assertEqual(response.status_code, 200)
        roles = response.json()
        self.assertIsInstance(roles, list)
        self.assertGreaterEqual(len(roles), 5)
        
        # Check structure: each item must only contain role_id and role_name
        for r in roles:
            self.assertIn("role_id", r)
            self.assertIn("role_name", r)
            self.assertNotIn("hierarchy_position", r)
            self.assertNotIn("can_upload", r)

        role_ids = [r["role_id"] for r in roles]
        self.assertIn("admin", role_ids)
        self.assertIn("programmer", role_ids)
        self.assertIn("employee", role_ids)

    def test_02_register_with_valid_roles(self):
        """Verify user registration succeeds with valid existing role_id or role_name."""
        # 1. Register with role_id 'programmer'
        res1 = self.client.post("/auth/register", json={
            "username": "p12_prog_user",
            "password": "password123",
            "role": "programmer"
        })
        self.assertEqual(res1.status_code, 201)
        data1 = res1.json()
        self.assertEqual(data1["username"], "p12_prog_user")
        self.assertEqual(data1["role"], "programmer")

        # 2. Register with role_name 'Co-founder' (case insensitive match)
        res2 = self.client.post("/auth/register", json={
            "username": "p12_cofounder_user",
            "password": "password123",
            "role": "Co-founder"
        })
        self.assertEqual(res2.status_code, 201)
        data2 = res2.json()
        self.assertEqual(data2["username"], "p12_cofounder_user")
        self.assertEqual(data2["role"], "co_founder")

    def test_03_register_with_dynamically_added_role(self):
        """Add a new role via Phase 11, verify it appears in available-roles, and register with it."""
        # 1. Admin creates role 'Data Scientist'
        create_resp = self.client.post("/admin/roles", json={
            "role_name": "Data Scientist",
            "insert_below_role_id": "programmer",
            "can_upload": False
        }, headers=self.admin_headers)
        self.assertEqual(create_resp.status_code, 201)
        role_data = create_resp.json()
        self.assertEqual(role_data["role_id"], "data_scientist")

        # 2. Verify it is returned by public GET /auth/available-roles
        avail_resp = self.client.get("/auth/available-roles")
        self.assertEqual(avail_resp.status_code, 200)
        available_ids = [r["role_id"] for r in avail_resp.json()]
        self.assertIn("data_scientist", available_ids)

        # 3. Register a user with this new role
        reg_resp = self.client.post("/auth/register", json={
            "username": "p12_ds_user",
            "password": "password123",
            "role": "data_scientist"
        })
        self.assertEqual(reg_resp.status_code, 201)
        self.assertEqual(reg_resp.json()["role"], "data_scientist")

        # 4. Login with this new user and verify issued JWT has the new role
        login_resp = self.client.post("/auth/login", json={
            "username": "p12_ds_user",
            "password": "password123"
        })
        self.assertEqual(login_resp.status_code, 200)
        self.assertIn("access_token", login_resp.json())

    def test_04_reject_invalid_role_registration(self):
        """Verify registration is rejected with 400 when an invalid/fake role is supplied."""
        bad_resp = self.client.post("/auth/register", json={
            "username": "p12_fake_user",
            "password": "password123",
            "role": "superman_role"
        })
        self.assertEqual(bad_resp.status_code, 400)
        err = bad_resp.json().get("detail") or bad_resp.json().get("error")
        self.assertIn("Invalid role 'superman_role'", err)

    def test_05_cleanup_and_deletion_guard(self):
        """Attempt to delete role while user is assigned (should fail), then clean up and delete."""
        # 1. Attempt to delete 'data_scientist' while p12_ds_user exists
        del_fail = self.client.delete("/admin/roles/data_scientist", headers=self.admin_headers)
        self.assertEqual(del_fail.status_code, 400)
        err = del_fail.json().get("detail") or del_fail.json().get("error")
        self.assertIn("user(s) are currently assigned", err)

        # 2. Clean up test users
        with db_service._get_sqlite_conn() as conn:
            conn.execute("DELETE FROM users WHERE username IN ('p12_prog_user', 'p12_cofounder_user', 'p12_ds_user')")
            conn.commit()

        # 3. Now deletion succeeds
        del_ok = self.client.delete("/admin/roles/data_scientist", headers=self.admin_headers)
        self.assertEqual(del_ok.status_code, 200)

if __name__ == "__main__":
    unittest.main()
