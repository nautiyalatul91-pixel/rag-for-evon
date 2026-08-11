import os
import sys
import shutil
from pathlib import Path
from unittest.mock import patch

# Configure sys.path so the test script can import app modules directly
sys.path.append(str(Path(__file__).resolve().parent))

# Set dummy environment variables if they are not already set
os.environ.setdefault("GEMINI_API_KEY", "your_gemini_api_key_here")
os.environ.setdefault("CHROMA_DB_PATH", "data/test_chroma_db")
os.environ.setdefault("SQLITE_DB_PATH", "data/test_metadata.db")

from fastapi.testclient import TestClient
from app.main import app
from app.services.db_service import db_service
from app.services.embedding_service import embedding_service
from app.services.llm_service import llm_service

# Define test files
TEST_POLICY = "leave_policy.txt"

# Color helpers
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

def log_test(name, passed, detail=""):
    status_str = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    detail_str = f" - {detail}" if detail else ""
    print(f"  {status_str} {name}{detail_str}")

def cleanup_test_files():
    print(f"{CYAN}Cleaning up temporary files...{RESET}")
    if os.path.exists(TEST_POLICY):
        try:
            os.remove(TEST_POLICY)
        except Exception:
            pass

def create_test_policy():
    print(f"{CYAN}Generating leave policy file for RAG context...{RESET}")
    with open(TEST_POLICY, "w", encoding="utf-8") as f:
        f.write(
            "Evon Leave Policy Guidelines.\n"
            "All regular full-time employees are entitled to 15 days of paid annual leave.\n"
            "Maternity leave is capped at 90 calendar days.\n"
            "All leaves of absence must be requested in advance and approved by the Team Lead.\n"
        )

def get_dummy_embeddings(texts, max_retries=5, initial_delay=1.0):
    """
    Mock embeddings. Returns a far-away vector if checking for 'password'
    to trigger the similarity threshold fallback.
    """
    embeddings = []
    for text in texts:
        if "password" in text.lower():
            embeddings.append([9.9] * 3072)
        else:
            embeddings.append([0.0] * 3072)
    return embeddings

def run_tests():
    print(f"{CYAN}Initializing TestClient and Database...{RESET}")
    
    # Force db_service to use test paths
    db_service.sqlite_path = "data/test_metadata.db"
    db_service.chroma_path = "data/test_chroma_db"
    db_service._init_sqlite()
    db_service._init_chroma()
    
    # Reset SQLite tables to ensure clean test state
    with db_service._get_sqlite_conn() as conn:
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM chat_history")
        conn.commit()

    # Reset ChromaDB collection to bypass file locks and start fresh
    try:
        db_service.chroma_client.delete_collection("company_knowledge_base_gemini")
    except Exception:
        pass
    db_service.collection = db_service.chroma_client.get_or_create_collection("company_knowledge_base_gemini")
    
    client = TestClient(app)
    
    has_api_key = os.getenv("GEMINI_API_KEY") != "your_gemini_api_key_here" and bool(os.getenv("GEMINI_API_KEY"))
    
    # We patch embeddings if no real API key is present
    patchers = []
    if not has_api_key:
        print(f"{YELLOW}No Gemini API key configured. Bypassing API calls with Mock Mode.{RESET}")
        
        # Patch embedding generation
        p_embed = patch.object(embedding_service, "get_embeddings", side_effect=get_dummy_embeddings)
        p_embed.start()
        patchers.append(p_embed)
        
        # Set client to Mock
        embedding_service.client_configured = False
        llm_service.client_configured = False
    else:
        print(f"{GREEN}Real Gemini API Key detected. Using live RAG completions.{RESET}")

    all_passed = True

    try:
        # Step 1: Upload the policy file
        print(f"\n{CYAN}Step 1: Uploading Context Document '{TEST_POLICY}'...{RESET}")
        with open(TEST_POLICY, "rb") as f:
            res = client.post("/admin/upload", files=[("files", (TEST_POLICY, f, "text/plain"))])
        
        passed = res.status_code == 200 and len(res.json()["uploaded"]) == 1
        all_passed = all_passed and passed
        log_test("Document ingestion", passed, f"Uploaded: {res.json().get('uploaded')}")

        # Step 2: Ask an answerable question
        print(f"\n{CYAN}Step 2: Querying /chat with answerable question...{RESET}")
        chat_req = {
            "question": "How many days of paid leave do I get?",
            "conversation_id": "test-chat-session"
        }
        res = client.post("/chat", json=chat_req)
        passed = res.status_code == 200
        if passed:
            data = res.json()
            # Verify sources cited
            passed = len(data["sources"]) > 0 and data["sources"][0]["filename"] == TEST_POLICY
            
            # If live OpenAI completion, check for '15' in answer. If Mock, verify structure.
            if not has_api_key:
                passed = passed and data["is_mock"] is True and TEST_POLICY in data["answer"]
            else:
                passed = passed and "15" in data["answer"]
            
            log_test("Grounded Q&A retrieval", passed, f"Answer: {data['answer']}")
        else:
            log_test("Grounded Q&A retrieval", False, f"Status Code: {res.status_code}, Body: {res.text}")
        all_passed = all_passed and passed

        # Step 3: Ask an unanswerable question (should trigger fallback)
        print(f"\n{CYAN}Step 3: Querying /chat with unanswerable question (should fallback)...{RESET}")
        chat_req_unanswerable = {
            "question": "What is the secret root password to the central database server?",
            "conversation_id": "test-chat-session"
        }
        res = client.post("/chat", json=chat_req_unanswerable)
        passed = res.status_code == 200
        if passed:
            data = res.json()
            # Fallback answer should be exact
            passed = data["answer"] == "I don't have information about that in the company knowledge base."
            passed = passed and len(data["sources"]) == 0
            log_test("Unanswerable query fallback", passed, f"Answer: {data['answer']} | Sources cited: {data['sources']}")
        else:
            log_test("Unanswerable query fallback", False, f"Status Code: {res.status_code}")
        all_passed = all_passed and passed

        # Step 4: Verify conversation history (multi-turn logic)
        print(f"\n{CYAN}Step 4: Querying follow-up question referencing history...{RESET}")
        
        # We ask a question that writes to memory first: "How many days of maternity leave do I get?"
        chat_turn_1 = {
            "question": "How many days of maternity leave do I get?",
            "conversation_id": "multi-turn-session"
        }
        client.post("/chat", json=chat_turn_1)
        
        # Follow up asking "Who must approve it?" (pronoun "it" refers to maternity leave in the history)
        chat_turn_2 = {
            "question": "Who must approve it?",
            "conversation_id": "multi-turn-session"
        }
        res = client.post("/chat", json=chat_turn_2)
        passed = res.status_code == 200
        if passed:
            data = res.json()
            if not has_api_key:
                # Mock Mode just parses context
                passed = len(data["sources"]) > 0 and data["sources"][0]["filename"] == TEST_POLICY
                log_test("Multi-turn conversation (Mock Mode)", passed, f"Mock Answer: {data['answer']}")
            else:
                # Live OpenAI completions should parse history and understand "it" = leave,
                # and context says leave is approved by "Team Lead"
                passed = "team lead" in data["answer"].lower()
                log_test("Multi-turn conversational context (OpenAI)", passed, f"Answer: {data['answer']}")
        else:
            log_test("Multi-turn conversational context", False, f"Status Code: {res.status_code}")
        all_passed = all_passed and passed

    finally:
        for p in patchers:
            p.stop()

    print("\n" + "=" * 50)
    if all_passed:
        print(f"{GREEN}ALL PHASE 2 TESTS PASSED SUCCESSFULLY! RAG Chat pipeline is verified.{RESET}")
    else:
        print(f"{RED}SOME TESTS FAILED. Please review the errors above.{RESET}")
    print("=" * 50)

if __name__ == "__main__":
    cleanup_test_files()
    try:
        create_test_policy()
        run_tests()
    finally:
        cleanup_test_files()
