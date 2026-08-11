import os
import sys
import time
import shutil
import unittest

# Clean up database files BEFORE importing app to avoid Win32 file locking issues
for path in ["data/test_metadata.db", "data/test_chroma_db", "data/audit.log"]:
    if os.path.exists(path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except Exception as e:
            print(f"Warning: could not clean up {path}: {e}")

# Configure test database paths BEFORE importing app to force config to read test paths
os.environ["SQLITE_DB_PATH"] = "data/test_metadata.db"
os.environ["CHROMA_DB_PATH"] = "data/test_chroma_db"
os.environ["MOCK_EMBEDDINGS"] = "true"  # Force mock embeddings to skip Gemini calls
os.environ["JWT_SECRET_KEY"] = "78b4a7df2cb6b4d32a9e52bf319207cf64b85eeea7a83d7bb024debfcb6b1a3e"

from app.main import app
from app.services.db_service import db_service
from fastapi.testclient import TestClient

# ANSI colors for nice terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

class TestAuthAndSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print(f"\n{CYAN}Initializing test Client...{RESET}")
        cls.client = TestClient(app)

    def test_01_user_registration(self):
        print(f"\n{YELLOW}Testing user registration...{RESET}")
        
        # 1. Admin registration (Self-assigned role, known limitation)
        payload = {
            "username": "admin_user",
            "password": "adminpassword",
            "role": "admin"
        }
        res = self.client.post("/auth/register", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["username"], "admin_user")
        self.assertEqual(data["role"], "admin")
        self.assertIn("id", data)
        print(f"  [PASS] Admin registered successfully: {data['username']} ({data['role']})")

        # 2. Employee registration
        payload = {
            "username": "employee_user",
            "password": "employeepassword",
            "role": "employee"
        }
        res = self.client.post("/auth/register", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["username"], "employee_user")
        self.assertEqual(data["role"], "employee")
        print(f"  [PASS] Employee registered successfully: {data['username']} ({data['role']})")

        # 3. Duplicate registration check
        res = self.client.post("/auth/register", json=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.json())
        print("  [PASS] Prevented duplicate username registration")

        # 4. Invalid role registration check
        payload["username"] = "invalid_user"
        payload["role"] = "guest"
        res = self.client.post("/auth/register", json=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.json())
        print("  [PASS] Prevented invalid role registration ('guest')")

    def test_02_user_login(self):
        print(f"\n{YELLOW}Testing user login...{RESET}")
        
        # 1. Admin login
        payload = {"username": "admin_user", "password": "adminpassword"}
        res = self.client.post("/auth/login", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.__class__.admin_token = data["access_token"]
        print("  [PASS] Admin login returned valid token")

        # 2. Employee login
        payload = {"username": "employee_user", "password": "employeepassword"}
        res = self.client.post("/auth/login", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.__class__.employee_token = data["access_token"]
        print("  [PASS] Employee login returned valid token")

        # 3. Invalid password login
        payload = {"username": "admin_user", "password": "wrongpassword"}
        res = self.client.post("/auth/login", json=payload)
        self.assertEqual(res.status_code, 401)
        print("  [PASS] Rejected login with incorrect password")

    def test_03_protected_endpoints_admin(self):
        print(f"\n{YELLOW}Testing protected admin endpoints...{RESET}")
        
        # 1. Access GET /admin/documents without token
        res = self.client.get("/admin/documents")
        self.assertEqual(res.status_code, 401)
        print("  [PASS] GET /admin/documents rejected access without token (401)")

        # 2. Access GET /admin/documents with Employee token
        headers = {"Authorization": f"Bearer {self.employee_token}"}
        res = self.client.get("/admin/documents", headers=headers)
        self.assertEqual(res.status_code, 403)
        print("  [PASS] GET /admin/documents rejected Employee access (403)")

        # 3. Access GET /admin/documents with Admin token
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        res = self.client.get("/admin/documents", headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), list)
        print("  [PASS] GET /admin/documents allowed Admin access (200)")

        # 4. Access POST /admin/upload with Employee token
        headers = {"Authorization": f"Bearer {self.employee_token}"}
        res = self.client.post("/admin/upload", files={}, headers=headers)
        self.assertEqual(res.status_code, 403)
        print("  [PASS] POST /admin/upload rejected Employee access (403)")

    def test_04_chat_endpoint_protection(self):
        print(f"\n{YELLOW}Testing chat endpoint authentication...{RESET}")
        
        # 1. Access POST /chat without token
        payload = {"question": "Hello", "k": 3}
        res = self.client.post("/chat", json=payload)
        self.assertEqual(res.status_code, 401)
        print("  [PASS] POST /chat rejected access without token (401)")

        # 2. Access POST /chat with Employee token
        headers = {"Authorization": f"Bearer {self.employee_token}"}
        res = self.client.post("/chat", json=payload, headers=headers)
        # Should succeed or return grounded answer (using fallback/no-answer here is 200 OK since no doc uploaded)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("answer", data)
        self.assertEqual(data["is_mock"], True)  # Mock embeddings are true
        print("  [PASS] POST /chat allowed Employee access (200)")

        # 3. Access POST /chat with Admin token
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        res = self.client.post("/chat", json=payload, headers=headers)
        self.assertEqual(res.status_code, 200)
        print("  [PASS] POST /chat allowed Admin access (200)")

    def test_05_chat_rate_limiting(self):
        print(f"\n{YELLOW}Testing in-memory rate limiting on POST /chat...{RESET}")
        
        # Register a fresh user to test rate limit in isolation
        reg_payload = {"username": "limit_user", "password": "limitpassword", "role": "employee"}
        self.client.post("/auth/register", json=reg_payload)
        
        # Log in to get fresh token
        login_payload = {"username": "limit_user", "password": "limitpassword"}
        login_res = self.client.post("/auth/login", json=login_payload)
        token = login_res.json()["access_token"]
        
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"question": "Rate limit test", "k": 1}
        
        # Submit 20 requests (should succeed)
        for i in range(20):
            res = self.client.post("/chat", json=payload, headers=headers)
            self.assertEqual(res.status_code, 200)
            
        # The 21st request must trigger 429 Too Many Requests
        res = self.client.post("/chat", json=payload, headers=headers)
        self.assertEqual(res.status_code, 429)
        self.assertIn("Rate limit exceeded", res.json()["error"])
        print("  [PASS] Rate limiting triggered successfully on the 21st request (429)")

    def test_06_audit_logging(self):
        print(f"\n{YELLOW}Testing audit logging output...{RESET}")
        audit_path = "data/audit.log"
        self.assertTrue(os.path.exists(audit_path))
        
        # Read audit log lines
        with open(audit_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        self.assertTrue(len(lines) > 0)
        
        # Check that we have logs for successes and failures
        has_success = False
        has_failure = False
        for line in lines:
            if "Success: True" in line:
                has_success = True
            if "Success: False" in line:
                has_failure = True
                
        self.assertTrue(has_success, "No success logs found in audit file")
        self.assertTrue(has_failure, "No failure logs found in audit file")
        print(f"  [PASS] Audit logs verified. Found successful and blocked events in data/audit.log")

if __name__ == "__main__":
    unittest.main()
