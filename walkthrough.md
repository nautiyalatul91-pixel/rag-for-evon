# Enterprise RAG Knowledge Portal - Project Walkthrough

This document outlines the architecture, implementations, and verification results across all four phases of the conversational RAG chatbot project.

---

## Phase 1 & 2: Ingestion & Gemini Grounding

- **Gemini Embeddings**: Integrates `models/gemini-embedding-001` (3072-dimensional vector spaces).
- **Gemini 1.5 Flash LLM**: Powers semantic chat query formulation, outputting fully grounded responses.
- **SQLite Database**: Serves as the tracking index for document metadata, checksum verification, and conversational thread storage.
- **ChromaDB Collection**: Stores text chunks (500–800 token overlap splits) and their associated vector coordinates under the name `company_knowledge_base_gemini_3072`.

---

## Phase 3: JWT Security, Auth & RBAC

- **Access Capping**: In-memory sliding-window rate limiter restricting users to **20 requests per minute** on `POST /chat`.
- **Security Checkpoints**: 
  - Token-free access requests return `401 Unauthorized`.
  - Roles are split between `admin` (access to uploads/deletes) and `employee` (access to chat queries only).
  - Unauthorized employee attempts to access admin panels yield a `403 Forbidden` response.
- **Centralized Auditing**: All authentication actions, token parsing attempts, block statuses, and API requests to protected endpoints are logged to `data/audit.log`.

---

## Phase 4: Single Page Application & Docker Deployment

### 1. Web Portal (SPA)
We created a beautiful Single-Page Application served directly from the FastAPI backend (via `FastAPI.staticfiles.StaticFiles` mount).
- **In-Memory JWT Storage**: Following security best practices, the access token is kept in-memory as a JS variable and is never persisted to `localStorage` or `sessionStorage`.
- **Admin Control Panel**: Features multi-file drag-and-drop document uploading, live progress queues, status badges (Success, Duplicate, Error), and a document database catalog with confirmation overlays for file deletions.
- **Conversational Chat Interface**: Employs chat bubbles, pulsing loading states, active session indicators, and citation cards mapping sources (filenames and pages) directly beneath LLM answers.
- **Session Auto-Recovery**: Detects token expiration and returns the user to the login screen with clear notification banners.

### 2. Docker Tooling
- **Dockerfile**: Compiles application assets inside a containerized python-slim workspace.
- **docker-compose.yml**:
  - Builds and starts the container mapping external port `8001` to internal container port `8000`.
  - Mounts a local volume `./data` to the container `/app/data` directory to safeguard database files and audit logs from container restarts.
  - Pulls runtime configuration variables dynamically from the local `.env` file.

---

## Integration Test Verification Results

All automated tests executed successfully on the virtual machine test database environments:

### `test_auth_security.py` Execution Log
```text
Ran 6 tests in 4.307s

OK

Initializing test Client...

Testing user registration...
  [PASS] Admin registered successfully: admin_user (admin)
  [PASS] Employee registered successfully: employee_user (employee)
  [PASS] Prevented duplicate username registration
  [PASS] Prevented invalid role registration ('guest')

Testing user login...
  [PASS] Admin login returned valid token
  [PASS] Employee login returned valid token
  [PASS] Rejected login with incorrect password

Testing protected admin endpoints...
  [PASS] GET /admin/documents rejected access without token (401)
  [PASS] GET /admin/documents rejected Employee access (403)
  [PASS] GET /admin/documents allowed Admin access (200)
  [PASS] POST /admin/upload rejected Employee access (403)

Testing chat endpoint authentication...
  [PASS] POST /chat rejected access without token (401)
  [PASS] POST /chat allowed Employee access (200)
  [PASS] POST /chat allowed Admin access (200)

Testing in-memory rate limiting on POST /chat...
  [PASS] Rate limiting triggered successfully on the 21st request (429)

Testing audit logging output...
  [PASS] Audit logs verified. Found successful and blocked events in data/audit.log
```

---

## Phase 4: Frontend Integration & Timeout Fixes

We identified and successfully resolved a critical frontend integration bug that was causing the chat loading indicator to spin indefinitely:

### Root Cause Analysis
1. **Missing Frontend Utility Function (`removeElement`)**:
   - The UI script was calling `removeElement(loaderId)` in both the `try` block (to remove the typing loader bubble after response success) and the `catch` block (to remove it after failure).
   - However, the `removeElement` helper was never defined in `static/index.html`.
   - This threw a `ReferenceError: removeElement is not defined` immediately upon receiving the response.
   - The error then triggered the `catch` block, which also called `removeElement`, raising a second uncaught exception that halted script execution. As a result, the typing indicator stayed in the DOM forever and the UI hung.

2. **Absence of API Client & Server Timeouts**:
   - Neither the backend Gemini calls nor the frontend `fetch` wrapper had explicit timeouts set, leaving them vulnerable to indefinite hanging in case of a slow network.
   - The client-side testing library (`httpx`) also had a default timeout of 5.0 seconds, which was occasionally exceeded by the Gemini API response times (e.g. 5.02s), leading to false-positive timeout errors during integration checks.

### Resolution Implemented
* **Defined `removeElement(id)`**: Added the missing DOM manipulation helper in the scripting block of `static/index.html`.
* **Integrated Abort-Based Client Timeout**: Configured a `30` seconds client-side timeout in `apiFetch()` using `AbortController` to automatically abort stuck requests and print user-friendly errors.
* **Added Gemini Backend Timeouts**: Wrapped live model content generation and embedding calls with a `30.0` seconds timeout constraint to ensure backend stability.
* **Verified Network Flow**: Verified full end-to-end integration and confirmed that requests return successfully in ~4 seconds without hanging the page.

---

## Phase 4: Logout Data Leakage & Visibility Fixes

We identified and successfully resolved a visual leakage bug after user sign-out:

### Root Cause Analysis
1. **CSS Specificity Override**:
   - The main application container was styled using the ID selector `#main-app-layout { display: flex; }`.
   - ID selectors have higher specificity in CSS than class selectors (such as `.view-section { display: none; }`).
   - Consequently, even after removing the `.active` class upon logout, the browser prioritized the `display: flex` layout from the ID rule, keeping the entire logged-in workspace (sidebar and data columns) visible on-screen alongside the login container.
2. **Incomplete DOM Clearing**:
   - The original logout handler cleared chat feeds and progress lists but left the internal document catalog tables (`docsTableBody`) and user badges populated, exposing administrative data.

### Resolution Implemented
* **Scoped ID Styles**: Restricted the layout flex rules to `#main-app-layout.active` so it falls back cleanly to `display: none` when the user session terminates.
* **Centralized `performLogout` Handler**: Unified session expiration and user-driven sign-outs under a single cleaning function to purge all token variables, reset profile text, empty message lists, and reset the admin files table.

---

## Phase 4: Git Synchronization & Railway Cloud Deployment

We successfully pushed the codebase to the user's remote GitHub repository and deployed it to the **Railway cloud platform** using a Docker container, achieving full production functionality:

### 1. Git Repository Configuration
- **Ignoring Sensitive Data**: Confirmed `.gitignore` correctly ignores `.env`, `data/`, `__pycache__/`, and `.venv/`.
- **Commit History Sanitization**: Verified via `git log` that `.env` and any secret credentials have never been tracked or committed in the git history.
- **GitHub Synced**: Pushed all Phase 1–4 commits to the remote origin: `https://github.com/nautiyalatul91-pixel/rag-for-evon.git`.

### 2. Railway Container Deployment & Configurations
- **Build Configuration**: Created a `railway.json` file in the project root to explicitly instruct Railway's builder to compile the application using the `Dockerfile` directly, bypassing Docker Compose mapping constraints.
- **Secure Environment Variables**: Successfully injected all application variables directly via the Railway settings dashboard:
  - `GEMINI_API_KEY`: *Active Google Gemini developer key*
  - `MOCK_EMBEDDINGS`: `false`
  - `JWT_SECRET_KEY`: *Random JWT signing secret key*
  - `JWT_ALGORITHM`: `HS256`
  - `RETRIEVAL_THRESHOLD`: `0.8` (optimized after real embedding scale evaluation)
  - `ALLOWED_ORIGINS`: `https://rag-for-evon-production.up.railway.app`
- **Volume Mounting**: Mounted a persistent storage volume to `/app/data` to ensure SQLite tables and ChromaDB vector embeddings remain intact across future container builds and redeploys.

### 3. Remote Cloud End-to-End Verification Results
We ran a remote client script directly targeting the live deployment at `https://rag-for-evon-production.up.railway.app` and verified all services perform correctly:
- **Role-Based Access Control (RBAC)**: Verified that employee accounts are strictly blocked (`403 Forbidden`) from admin metadata endpoints (`/admin/documents`).
- **Document Ingestion**: Successfully uploaded the official PDF/TXT company policies from the local machine directly to the remote Railway server.
- **Grounded Semantic Q&A**: Queried the chatbot with: `"how many paid leaves do i get"`. The system called the real Google Gemini Embeddings API, retrieved the relevant context chunk, called the Gemini LLM, and successfully generated a correct grounded response citing **`Evon_Leave_Policy.pdf`** with `is_mock: false`.
