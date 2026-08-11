# Private RAG Chatbot Backend - Phase 1 Ingestion Pipeline

A robust, private RAG (Retrieval-Augmented Generation) document ingestion pipeline built with Python and FastAPI. This backend service allows admins to upload company documents, parses and cleans their text, breaks them into token-bounded chunks, generates vector embeddings using OpenAI's `text-embedding-3-small` model, and stores the vectors and raw content in a local persistent ChromaDB database.

## Architecture

- **Web Server**: FastAPI (succeeds synchronously in request/response cycles for Phase 1).
- **Relational metadata DB**: SQLite (`data/metadata.db`) - acts as single source of truth for ingested document status, upload tracking, and duplicate checks.
- **Vector DB**: ChromaDB (`data/chroma_db`) - stores document chunk texts, embeddings, and metadata.
- **Embedding model**: OpenAI's `text-embedding-3-small` (1536-dimensional vectors).
- **Parsers**: 
  - PDF: PyMuPDF (`fitz`) - extracts text page-by-page.
  - DOCX: `python-docx` - extracts paragraphs and tables.
  - XLSX: `openpyxl` - extracts cells sheet-by-sheet.
  - TXT: standard utf-8 plain read.
- **Chunking**: Sentence-boundary-respecting tokenizer splitter using `tiktoken` (chunks of 500-800 tokens with 50-100 token overlap).

---

## Getting Started

### Prerequisites

- Python 3.9+ (Python 3.13 supported natively with precompiled wheels).
- Internet connection (for installing dependencies and calling the OpenAI API).

### Installation

1. **Clone or navigate to the workspace directory**:
   ```bash
   cd "c:/Users/Dell/Desktop/RAG for evon"
   ```

2. **Create and activate a virtual environment**:
   - **Windows (PowerShell)**:
     ```powershell
     python -m venv .venv
     .venv\Scripts\Activate.ps1
     ```
   - **macOS/Linux**:
     ```bash
     python -m venv .venv
     source .venv/bin/activate
     ```

3. **Install the dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   Create a `.env` file in the root directory (you can copy `.env.example` as a template):
   ```bash
   cp .env.example .env
   ```
   Open the `.env` file and set your OpenAI API key:
   ```env
   OPENAI_API_KEY=sk-proj-YOUR_ACTUAL_OPENAI_KEY_HERE
   ```

---

## Running the Application

Start the server using `uvicorn` (the configuration handles directory creations automatically):
```bash
python app/main.py
```
Or run via command line:
```bash
.venv\Scripts\uvicorn app.main:app --reload
```
Once started, the API is accessible at `http://127.0.0.1:8000`. You can view the interactive API documentation at:
- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- ReDoc: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## API Endpoints

### 1. Ingest Documents
- **Endpoint**: `POST /admin/upload`
- **Content-Type**: `multipart/form-data`
- **Request**: Accept one or more files in the key `files`.
- **Validations**:
  - File extension must be `.pdf`, `.docx`, `.xlsx`, or `.txt` (returns HTTP 400 otherwise).
  - File size must be under 20MB (returns HTTP 400 otherwise).
  - Duplicate check (returns description of failure in the failed uploads report if content hash or filename is already ingested).

### 2. List Ingested Documents
- **Endpoint**: `GET /admin/documents`
- **Response**: List of document records:
  ```json
  [
    {
      "id": "uuid-string-here",
      "filename": "document.pdf",
      "upload_date": "2026-08-05T22:18:27Z",
      "chunk_count": 2,
      "status": "completed"
    }
  ]
  ```

### 3. Delete Document
- **Endpoint**: `DELETE /admin/documents/{document_id}`
- **Description**: Removes the document from the SQLite metadata table and deletes all associated chunks and vector embeddings from ChromaDB.

---

## Testing Locally

We provide two automated integration scripts for testing. Both scripts run offline and free by default if no active OpenAI key is provided!

### 1. Ingestion Pipeline Verification (Phase 1)
```bash
python test_ingestion.py
```

### 2. Conversational Chat & Query Verification (Phase 2)
```bash
python test_chat.py
```

---

---

## Phase 3 & 4: Security, Authentication, and Docker Deployment

We have added JWT-based authentication, Role-Based Access Control (RBAC), user management, in-memory rate limiting, structured audit logs, a high-fidelity frontend single-page application (SPA), and Docker packaging.

### Access Control Rules
- Public Endpoints: `POST /auth/register`, `POST /auth/login`, `GET /` (serves the frontend SPA).
- Admin-Only Endpoints (Requires valid JWT with role `"admin"`): `POST /admin/upload`, `GET /admin/documents`, `DELETE /admin/documents/{id}`.
- Authenticated Endpoints (Requires valid JWT with role `"admin"` or `"employee"`): `POST /chat`.

### Known Architectural Limitations (CRITICAL WARNINGS)
* **Self-assigned roles during registration [MUST FIX BEFORE PRODUCTION]**: The `POST /auth/register` endpoint currently allows users to self-assign their role (either `"admin"` or `"employee"`). This is acceptable for local testing and private/internal APIs, but for a real production deployment, this **must** be locked down (e.g. only existing admins should be able to create new admin accounts, or role selection should be disabled entirely).
* **In-memory Rate Limiter**: The rate limiter (enforcing a maximum of 20 RPM per user on `POST /chat`) is stored completely in-memory. This means rate limits will reset upon server restart and are only effective for a single-process deployment. For production scaling across multiple workers/servers, a persistent backend like Redis should be introduced.

---

## Deployment with Docker & Docker Compose

To build and run the entire application using Docker:

### 1. Configure the `.env` file
Ensure your `.env` contains your active Gemini API key, database locations, and JWT security keys:
```env
GEMINI_API_KEY=your_gemini_api_key_here
MOCK_EMBEDDINGS=false
RETRIEVAL_THRESHOLD=0.8

JWT_SECRET_KEY=78b4a7df2cb6b4d32a9e52bf319207cf64b85eeea7a83d7bb024debfcb6b1a3e
JWT_ALGORITHM=HS256
ALLOWED_ORIGINS=http://localhost:8001,http://127.0.0.1:8001

CHROMA_DB_PATH=data/chroma_db
SQLITE_DB_PATH=data/metadata.db
```

### 2. Run with Docker Compose
Run the following command in the project root directory:
```bash
docker-compose up --build -d
```
This builds the python backend, mounts a volume on `./data` to persist your ingested documents, metadata, and log files, and starts the container on port **`8001`**.

### 3. Access the Web Frontend
Open your browser and navigate to:
* **Web Portal (SPA)**: [http://localhost:8001](http://localhost:8001)
* **API Documentation**: [http://localhost:8001/docs](http://localhost:8001/docs)

---

## API Endpoints (Quick Reference)

### Authentication
* **`POST /auth/register`**: Register a new user with a username, password, and role.
* **`POST /auth/login`**: Authenticate and retrieve a JWT Access Token.

### Ingestion & Documents (Admin Only)
* **`POST /admin/upload`**: Upload and ingest document files synchronously.
* **`GET /admin/documents`**: List metadata for all ingested documents.
* **`DELETE /admin/documents/{id}`**: Delete document metadata and its associated vector chunks.

### Chat & Q&A (All Logged-In Users)
* **`POST /chat`**: Submit natural language questions to retrieve grounded document answers.

---

## Testing Locally (Python Scripts)

We provide three automated integration scripts for testing. All scripts run offline and free by default!

1. **Ingestion Pipeline Verification (Phase 1)**:
   ```bash
   python test_ingestion.py
   ```
2. **Conversational Chat & Query Verification (Phase 2)**:
   ```bash
   python test_chat.py
   ```
3. **Authentication, Security & Rate Limiting Verification (Phase 3)**:
   ```bash
   python test_auth_security.py
   ```

---

## Sample cURL Commands (With Authentication)

**1. Register a new user (role can be 'admin' or 'employee')**:
```bash
curl -X POST "http://127.0.0.1:8001/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"username": "john_admin", "password": "securepassword", "role": "admin"}'
```

**2. Log in to get the JWT Access Token**:
```bash
curl -X POST "http://127.0.0.1:8001/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "john_admin", "password": "securepassword"}'
```
*Response returns: `{"access_token": "YOUR_JWT_TOKEN", "token_type": "bearer"}`*

**3. Ingest a document (Admin token required)**:
```bash
curl -X POST "http://127.0.0.1:8001/admin/upload" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: multipart/form-data" \
  -F "files=@Evon_Lead_Policy.pdf"
```

**4. Submit a chat question (Any authenticated token)**:
```bash
curl -X POST "http://127.0.0.1:8001/chat" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "How many days of paid leave do I get?", "conversation_id": "session-123"}'
```

**5. List documents (Admin token required)**:
```bash
curl -X GET "http://127.0.0.1:8001/admin/documents" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```
