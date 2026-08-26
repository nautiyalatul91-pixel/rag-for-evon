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

---

---

# Walkthrough - Phase 5: Supporting Multiple Knowledge Base Collections

We have successfully implemented and verified **Phase 5: Supporting Multiple Knowledge Base Collections** in the EVON corporate RAG chatbot. This allows users to ingest documents into, view documents from, and query specific collections (`company_knowledge_base_gemini_3072` or `evon_capabilities`) independently.

---

## Changes Implemented

### 1. Database & Schema Migrations
* **SQLite Table Migration**: Enhanced `_init_sqlite` in [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) with automatic, non-destructive migration. It creates a `collection_name` column in the `documents` table and updates constraints to composite checks: `UNIQUE(filename, collection_name)` and `UNIQUE(content_hash, collection_name)`.
* **ChromaDB Connection Mapping**: Refactored `_init_chroma` in [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) to initialize a mapping dictionary (`self.collections`) containing both standard collections, ensuring independent storage.

### 2. Back-end Scopes
* **Scoped Duplicates & Ingestion**: Updated `check_duplicate`, `create_document_record`, `add_chunks_to_chroma`, and `delete_document` in [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) to target the specific `collection_name` parsed from client requests.
* **API Routers & Models**:
  * [responses.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/models/responses.py): Added `collection_name` to `DocumentMetadata` so index lists return collection context.
  * [chat.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/models/chat.py): Extended `ChatRequest` schema with an optional `collection` parameter.
  * [admin.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/admin.py):
    * `POST /admin/upload`: Extracted the `collection` form parameter (defaulting to the leave policy collection for backward compatibility) and validated it.
    * `GET /admin/documents`: Enabled a `collection` query filter to index items from a specific collection only.
  * [chat.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/chat.py): Passed request-level collection selection to `retrieval_service`.
  * [retrieval_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/retrieval_service.py): Scoped document embedding query to the targeted ChromaDB collection.

### 3. Front-end Portal UI
* Added a `.kb-select` compact CSS dropdown selector format inside [index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html).
* **Admin Section**: Added an "Active KB" selector dropdown above the document table. Document lists now filter dynamically when changing selection, and uploads route automatically to the chosen collection. The table has an added "Collection" column to clearly identify document locations.
* **Chat Section**: Added a "Query Collection" selector in the header. Chat queries route to the selected knowledge base payload when sending messages.

---

## Verification Results

### Integration Tests
We created and ran `scratch/test_multi_collection.py` which validates:
1. Both ChromaDB collections are properly instantiated.
2. Duplicate checks prevent duplications inside a collection, but allow identical names/contents in different collections.
3. Metadata queries filter and index documents by collection correctly.
4. Chat query retrieval isolation works perfectly (queries scope vector candidate checks to the requested collection).

#### Test Output:
```bash
.venv\Scripts\python "C:\Users\Dell\.gemini\antigravity\brain\b182b695-7559-48d4-a621-801e019994a1/scratch/test_multi_collection.py"
...
2026-08-20 01:04:48,727 - rag_ingestion - INFO - Starting retrieval for question: 'leaves' in collection 'company_knowledge_base_gemini_3072' (k=5)
2026-08-20 01:04:48,736 - rag_ingestion - INFO - Retrieved chunk candidate from file: 'leave_policy.pdf' (page 1) | L2 Distance Score: 30.7203 (Threshold: 999.00)
2026-08-20 01:04:48,737 - rag_ingestion - INFO - Retrieval summary: 1 candidates found | 1 passed L2 threshold constraint.
2026-08-20 01:04:48,737 - rag_ingestion - INFO - Starting retrieval for question: 'leaves' in collection 'evon_capabilities' (k=5)
2026-08-20 01:04:48,743 - rag_ingestion - INFO - Retrieved chunk candidate from file: 'evon_capabilities.txt' (page 1) | L2 Distance Score: 30.7203 (Threshold: 999.00)
2026-08-20 01:04:48,743 - rag_ingestion - INFO - Retrieval summary: 1 candidates found | 1 passed L2 threshold constraint.
.
----------------------------------------------------------------------
Ran 4 tests in 0.284s

OK
```

All standard security unit tests in `test_auth_security.py` also pass without regression.

---

# Walkthrough - Phase 6: Supporting Company Web Research

We have successfully implemented and verified **Phase 6: Supporting Company Web Research** using the **Tavily Search API** combined with standard Gemini content generation (`models/gemini-3.6-flash`). This allows the EVON BD Agent to perform real-time, web-grounded research on a given company name, URL, or LinkedIn profile without hitting Google Search grounding quota blocks.

---

## Changes Implemented

### 1. Dependency & Config Integration
* **tavily-python**: Installed and appended `tavily-python==0.7.27` in `requirements.txt`.
* **Tavily Key Configuration**: Added `TAVILY_API_KEY` to [config.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/config.py) to read credentials from the environment.

### 2. Research Service Layer
* **Search Engine Backend**: Modified [research_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/research_service.py) to query Tavily Search using `tavily_client.search(query=search_query, search_depth="advanced", max_results=5)`.
* **Context Formulator**: Formats returned snippets (including source URLs, page titles, and content texts) into a clean context block.
* **Extraction completions**: Passes this context block to standard Gemini completions using the active working model `models/gemini-3.6-flash` (no grounding tools).
* **Grounding Citations**: Instructed the LLM to extract citation URLs from the search context headers and map them to their corresponding profile fields in the JSON output.

### 3. API Route & Rate Limiting
* **API Route**: Created `POST /research` in [research.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/research.py) requiring authentication.
* **Hour-Level Rate Limiter**: Enforces a strict sliding-window limit of **10 requests per hour per user**.
* **Audit Logs**: Correctly logs all successful and blocked queries in `data/audit.log`.

---

## Verification Results

We wrote and executed `scratch/test_live_companies.py` and `scratch/test_gaylord_xpress.py` to test the Tavily search integration against the running server. All queries completed successfully with a status of `200 OK` and returned fully grounded, structured profiles:

### 0. Query Suffix Bug Fix
* **The Issue**: Originally, the backend appended `"company profile tech stack recent news size"` to every query. For small or obscure local businesses (like "Gaylord Xpress"), this search term pollution caused Tavily to return irrelevant corporate results (like U.S. Xpress or Gaylord Entertainment), leading to completely empty/failed extractions.
* **The Fix**: Modified [research_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/research_service.py) to search Tavily using the raw, clean `company_input` directly. This enables Tavily to automatically prioritize the most relevant local business listings (Zomato, Tripadvisor, Justdial) for small businesses, while still perfectly resolving global enterprise profiles (Wikipedia, Yahoo Finance, etc.).

### 1. Obscure Local Business: Gaylord Xpress Profile
* **Source Citations**: Grounded in [zomato.com](https://www.zomato.com/dehradun/gaylord-xpress-paltan-bazaar), [justdial.com](https://www.justdial.com/Dehradun/Gaylord-express-Near-Clock-Tower-Paltan-Bazar/9999PX135-X135-091114120907-L2D2DC_BZDET), and [magicpin.in](https://magicpin.in/Dehradun/Paltan-Bazaar/Restaurant/Gaylord-Xpress/store/272692/menu)
* **Output Profile**:
```json
{
  "company_name": "Gaylord Xpress",
  "industry": "Food & Beverage / Restaurant & Bakery",
  "what_they_do": "Gaylord Xpress is a casual dining restaurant and bakery located in Paltan Bazaar, Dehradun. It serves a wide variety of items including North Indian, South Indian, and Chinese dishes, momos, fast food, pastries, cakes, desserts, beverages, and provides home delivery services.",
  "tech_stack": [],
  "size_stage": "not publicly available",
  "recent_news": [
    "not publicly available"
  ],
  "business_needs": [
    "Staff training and customer service improvement to address negative reviews regarding rude behavior...",
    "Enhanced food packaging and delivery handling to prevent damage to delicate items like cakes...",
    "Order accuracy, portion control, and billing transparency to resolve customer complaints..."
  ]
}
```

### 2. Apple Inc. Profile Output
* **Source Citations**: Grounded in `https://www.globaldata.com/company-profile/apple-inc`
* **Output Profile**:
```json
{
  "company_name": "Apple Inc.",
  "industry": "Technology and Communications",
  "what_they_do": "Apple Inc. designs, manufactures, and markets smartphones, tablets, personal computers, and wearable devices. It also offers software applications, related services, accessories, cloud services, payment services, and digital content distribution...",
  "tech_stack": ["iOS", "macOS", "iPadOS", "watchOS", "iCloud", "Apple Pay", "Apple NeuralHash"],
  "size_stage": "Publicly traded company (NASDAQ: AAPL) with 166,000 employees...",
  "recent_news": [
    "Plans to acquire PlasmaSolve, a materials science company (August 2026)",
    "Plans to roll out a paid subscription for advanced access to its AI Siri assistant (August 2026)",
    "Opened an Advanced Manufacturing Center in Houston (July 2026)",
    "Sued OpenAI, accusing it of stealing company secrets (July 2026)"
  ]
}
```

### 2. Tesla Inc. Profile Output
* **Source Citations**: Grounded in `https://www.britannica.com/money/Tesla-Motors` and `https://talent500.com/blog/tesla-tech-stack-open-source-advantage`
* **Output Profile**:
```json
{
  "company_name": "Tesla, Inc.",
  "industry": "Auto Manufacturers",
  "what_they_do": "Tesla designs, manufactures, and sells electric vehicles, energy generation and storage systems, and provides related services... also invests heavily in AI, robotics, and autonomous transportation solutions like Cybercab/Robotaxis.",
  "tech_stack": ["Warp (Custom-built ERP system)", "Open Source software solutions"],
  "recent_news": [
    "Tesla eyes tax breaks for new $10B solar cell manufacturing facility (Aug 8, 2026)",
    "Display of Tesla Optimus robot humanoid and Tesla Cybertruck at the Bund Conference"
  ]
}
```

### 3. Netflix Profile Output
* **Source Citations**: Grounded in `https://en.wikipedia.org/wiki/Netflix,_Inc.` and `https://stackshare.io/netflix/netflix`
* **Output Profile**:
```json
{
  "company_name": "Netflix, Inc.",
  "industry": "Internet television network",
  "what_they_do": "Netflix is an Internet television network offering TV shows, movies, original series, and mobile games to subscribers globally.",
  "tech_stack": ["React", "Swift", "Kotlin", "GraphQL", "Java", "Python", "Spring Boot", "Zuul", "Eureka", "Cassandra", "Chaos Monkey", "Spinnaker"]
}
```

---

# Walkthrough - Phase 7: Opportunity Identification

We have successfully implemented and verified **Phase 7: Opportunity Identification**. The system cross-references a company's research profile against the `"evon_capabilities"` ChromaDB collection to identify genuine sales opportunities backed by profile evidence.

---

## Changes Implemented

### 1. Database Persistence
* **SQLite Profiles Table**: Created a `research_profiles` table inside [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) to save successfully generated research runs.
* **Lookup Helpers**: Implemented `save_research_profile` and `get_research_profile` to store and retrieve company profiles.
* **UUID generation**: Endpoint `/research` now returns a unique `research_id` (a UUID string) mapping to the stored profile.

### 2. Opportunity Matcher Service
* **Matching Engine**: Created [opportunity_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/opportunity_service.py).
* **Direct Vector Query**: Queries `evon_capabilities` directly for each business need (or fallback) to retrieve the top 3 candidate chunks without discarding them via a strict L2 threshold. This delegates the intelligent filtering to the LLM.
* **Gemini Reasoning**: Prompts `models/gemini-3.6-flash` in JSON mode to evaluate the fit of each EVON capability against the company's pain points.
* **Zero-Overlap Support**: Instructs the LLM to output an empty list of opportunities with a logical explanation if the target company operates in an industry unrelated to EVON's services (e.g. local retail/florists).

### 3. API Router & Auditing
* **API Router**: Created `POST /opportunities` in [opportunities.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/opportunities.py), accepting either `research_id` or a direct `profile` payload.
* **Audit Logging**: Logs the count of identified opportunities and matching latency to `data/audit.log` and the server console.
* **Registration**: Mounted the router in [main.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/main.py).

---

## Verification Results

We executed `scratch/test_opportunities.py` against the local server to verify the opportunity matching logic:

### 1. Apple Inc. Match Output (High Confidence Match)
* **Status**: `200 OK`
* **Output Opportunities**:
```json
{
  "opportunities": [
    {
      "evon_service": "Data Engineering & AI/ML Solutions (Case Study 3: E-commerce Recommendation Engine)",
      "evidence_from_profile": "Apple's need for 'Expansion and monetization of subscription services (Apple Music, Apple TV+, Apple Arcade, Fitness+) to complement hardware device sales.'",
      "fit_explanation": "EVON has demonstrated capabilities in building custom recommendation systems and predictive analytics platforms. By leveraging collaborative filtering and AI/ML models similar to EVON's Case Study 3, EVON can help optimize content recommendation and personalization across Apple's subscription offerings..."
    },
    {
      "evon_service": "Data Engineering & AI/ML Solutions & Custom Software Development (Case Study 5: Legacy System Modernization and Supply Chain Integration)",
      "evidence_from_profile": "Apple's need for 'Optimization of worldwide supply chain, retail operations, and direct/third-party reseller channels across multiple global regions.'",
      "fit_explanation": "EVON specializes in data engineering, predictive analytics dashboards for retail/logistics, and system integration via secure APIs with supply chain partners. EVON can build custom analytical dashboards and automated data pipelines to streamline global reseller integration..."
    }
  ],
  "explanation": "Three strong opportunities were identified matching EVON's capabilities in machine learning recommendations, supply chain data engineering, and RAG-based AI knowledge management chatbots..."
}
```

### 2. Green Meadows Florist (Zero Overlap Case)
* **Status**: `200 OK`
* **Output**:
```json
{
  "opportunities": [],
  "explanation": "Green Meadows Florist is a small local retail business whose immediate pain points pertain to physical floral inventory sourcing (finding a local rose supplier) and domain-specific retail staffing (hiring a seasonal florist)... Because Green Meadows Florist operates using off-the-shelf platforms (Square POS, Wix) and has no current software engineering or cloud infrastructure needs, there are no genuine business opportunities for EVON Technologies."
}
```

### 3. Audit Logs
Verified that `data/audit.log` recorded the operations correctly:
```text
2026-08-21 01:47:38,925 - User: opp_test_e1dd87 | Role: admin | Endpoint: POST /research | Success: True | Details: Researched 'Apple Inc.' in 15.75s. Result company: 'Apple Inc.'
2026-08-21 01:47:56,093 - User: opp_test_e1dd87 | Role: admin | Endpoint: POST /opportunities | Success: True | Details: Identified 3 opportunities for 'Apple Inc.' in 17.15s
2026-08-21 01:48:35,941 - User: opp_test_e1dd87 | Role: admin | Endpoint: POST /opportunities | Success: True | Details: Identified 0 opportunities for 'Green Meadows Florist' in 39.82s
```

---

# Walkthrough - Phase 8: Draft Generation

We have successfully implemented and verified **Phase 8: Draft Generation**. The system takes a researched company's profile and matched opportunities, and generates both a raw internal analysis summary and a deeply personalized client outreach draft.

---

## Changes Implemented

### 1. Database Table
* **SQLite Drafts Table**: Created a `drafts` table inside [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py):
  - Fields: `id` (PRIMARY KEY UUID), `research_id` (TEXT, nullable), `company_name` (TEXT), `internal_draft` (TEXT), `outreach_draft` (TEXT), `status` (TEXT, default: `'pending_review'`), `created_at` (TEXT).
* **Helper Methods**: Implemented `save_draft`, `get_draft`, and `update_draft_status`.

### 2. Draft Generator Service
* **Model Selection**: Created [draft_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/draft_service.py) leveraging `models/gemini-3.6-flash`.
* **Internal Summary Generation**: Directed the LLM to output objective, matter-of-fact internal reasoning covering overview, technological needs, core EVON value fit, and tactical sales recommendations (along with realistic budget/off-the-shelf software objection handling).
* **Personalized Outreach Generation**: Structured a consultative email draft that anchors context in real researched details (like branch locations and brand names), maps it to a specific value proposition, and terminates with a low-pressure LinkedIn/keep-in-touch exit.

### 3. API Router & Auditing
* **API Router**: Created `POST /drafts` in [drafts.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/drafts.py) requiring JWT authentication.
* **On-the-Fly Opportunities**: Implemented automated fallback resolution. If `opportunities` is omitted in the request payload, the router automatically calls the matching engine on the fly.
* **Registration**: Registered the router in [main.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/main.py).

---

## Verification Results

We executed `scratch/test_drafts.py` to generate drafts for **Apple Inc.** (enterprise fit) and **The Black Gold Gym** (borderline fit).

### 1. The Black Gold Gym Output (Borderline adaptation)
* **Internal Draft (Extracts)**:
  * *Company Overview*: Identifies B2C local gym with branches in Chandrapur and Ballupur Chowk, India.
  * *Core EVON Value Fit / Reality Check*: *"While EVON offers full custom engineering, a small regional gym may not have capital... Fit is viable only if structured as a simple, high-value MVP."*
  * *Sales Recommendation & Objections*: Anticipates objections regarding custom software cost vs cheap COTS fitness SaaS (Mindbody/Glofox), suggesting pitching low-code integrations or localized Indian payment gateways.
* **Outreach Draft**:
```text
Subject: Streamlining member sign-ups for The Black Gold Gym

Hi [First Name / Team],

I’ve been following The Black Gold Gym’s presence in the fitness community—it’s great to see your "Old Skool Arena" atmosphere building such a strong following across your locations in Chandrapur and Ballupur Chowk. 

As a growing gym brand, I imagine your team spends a lot of time manually handling visit inquiries, staffed-hour questions, and membership details through social media DMs. 

At EVON Technologies, we build custom, easy-to-use web applications and software solutions designed to streamline day-to-day operations. We could help The Black Gold Gym set up an automated online booking and membership portal. This would allow prospective and existing members to view branch schedules, book visits, and manage subscriptions directly online—freeing up your team’s time to focus on coaching and community building.

Would you be open to a quick 10-minute introductory call next week to see if this might be helpful for your team? Alternatively, I'd be glad to send over a quick overview of how a system like this works.

Even if you don't have active software needs at the moment, I'd love to connect on LinkedIn to keep in touch for the future.
```

### 2. Audit Logs
Verified that the audit logging captures durations and unique draft IDs:
```text
2026-08-21 12:31:24,192 - User: draft_test_957055 | Role: admin | Endpoint: POST /drafts | Success: True | Details: Generated drafts for 'Apple Inc.' with ID a9f002af-e89f-4c8d-81f0-e7e7576e3673 in 27.57s
2026-08-21 12:32:27,473 - User: draft_test_957055 | Role: admin | Endpoint: POST /drafts | Success: True | Details: Generated drafts for 'The Black Gold Gym' with ID f8f35dc9-81a9-452b-b0ad-923811bd6827 in 46.28s
```



# Walkthrough - Phase 9: Review Queue & Approval Workflow

We have successfully implemented and verified **Phase 9: Review Queue & Approval Workflow** in the EVON BD Agent system. This phase delivers endpoints for administrative listing and decision updates on sales outreach drafts, security and role checks, persistent schema migrations, and a side-by-side split screen UI tab inside the Admin Control Center.

---

## Changes Implemented

### 1. Database Migrations & Schemas
* **SQLite Table Schema Expansion**: Safe start-up migrations inside [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) automatically alter the `drafts` table schema to include:
  - `profile_json` (TEXT): For storing the complete company profile payload as serialized JSON.
  - `opportunities_json` (TEXT): For storing the matched opportunities list as serialized JSON.
  - `rejection_reason` (TEXT, nullable): For storing user feedback in case of rejection.
* **Retrieval & Persistence Methods**: Refactored `save_draft`, `list_drafts`, and `get_draft` to handle JSON serialization and deserialization seamlessly.

### 2. Back-end Review Endpoints & Access Control
* **Endpoints**: Added the following endpoints inside [drafts.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/drafts.py):
  - `GET /drafts`: Lists all drafts with filters (e.g. `status_filter=pending_review`).
  - `GET /drafts/{draft_id}`: Retrieves specific draft details including profile, opportunities, drafts, and status.
  - `POST /drafts/{draft_id}/approve`: Sets status to `approved`. Requires `admin` role.
  - `POST /drafts/{draft_id}/reject`: Sets status to `rejected` and records an optional rejection reason. Requires `admin` role.
* **Role Check Dependency**: Integrated the `require_admin` dependency so only admins can execute approvals and rejections. Regular employee logins receive `403 Forbidden`.
* **Audit Logs**: Logged all approval/rejection operations directly to `data/audit.log` showing the requesting user, role, targeted draft, and rejection reason (if any).

### 3. Front-end "Review Queue" Portal UI
* **Sub-Tab Navigation**: Added tabs to toggle between the "Knowledge Index" and "Review Queue" inside the Admin Panel in [index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html).
* **Master-Detail Layout**:
  - **Left column**: List of all drafts currently in the `pending_review` status.
  - **Right column**: Complete details of the selected draft, displaying the target company profile (industry, size, what they do, tech stack badges, recent news items), matched opportunities with literal evidence quotes, the objective internal summary, and the personalized outreach message.
* **Action Handlers**:
  - Built clipboard copy buttons for copying draft texts instantly.
  - Implemented Approve and Reject buttons that trigger modern blur-overlay modal confirmation dialogs. Rejection modal opens a text area for an optional review feedback comment.

### 4. Rate/Quota Limit Exception Propagation
* Removed fallback templates from [draft_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/draft_service.py) so Gemini API exceptions bubble up normally.
* Intercepted exceptions in the `/drafts` route handler in [drafts.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/drafts.py). If a Gemini rate/quota exhaustion error occurs (indicated by status code 429 or `ResourceExhausted` exception):
  - Returns `429 Too Many Requests` status code with custom JSON response structure:
    `{"error": "quota_exceeded", "message": "Unable to generate drafts right now — the Gemini API daily quota has been reached. Please try again later once the quota resets, or switch to a different model/API key with available quota."}`
  - Prevents database record insertion (leaving drafts table clean).
  - Logs details to `data/audit.log` showing: `"Draft generation failed due to quota limit. No draft created."`
* Enhanced frontend error parsing in [index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html) to render user-facing warning alert cards inside the review queue list container whenever loading fails.

---

## Verification Results

We created and executed the integration test scripts `scratch/test_review.py` and `scratch/test_quota_exhausted.py`. All API interactions, database states, and audit logs passed perfectly:

### 1. Quota Exhaustion Failure Verification (`test_quota_exhausted.py`)
* Mocking a `ResourceExhausted` exception returns the correct status and body:
  - `Server response code: 429`
  - `Server response body: {"error":"quota_exceeded","message":"Unable to generate drafts right now — the Gemini API daily quota has been reached. Please try again later once the quota resets, or switch to a different model/API key with available quota."}`
* Database records remain clean:
  - `Drafts count in SQLite after test: 0`
  - `Database verification passed: No placeholder record was saved.`
* The failed attempt is logged to `data/audit.log` correctly:
  `2026-08-21 13:28:39,863 - User: quota_admin_497584 | Role: admin | Endpoint: POST /drafts | Success: False | Details: Draft generation failed due to quota limit. No draft created.`

### 2. Permission Restriction Verification (`test_review.py`)
* Attempting approval/rejection using a regular employee token correctly returns `403 Forbidden`:
  - `Employee Approve Status (expected 403): 403`
  - `Employee Reject Status (expected 403): 403`

### 3. Approval Action Verification (`test_review.py`)
* Approving a draft as admin returned success:
  - `Admin Approve Status: 200`
  - `Updated Apple Draft Status: approved`

### 4. Rejection Action Verification (`test_review.py`)
* Rejecting a draft as admin with reason returned success:
  - `Admin Reject Status: 200`
  - `Updated Gym Draft Status: rejected`
  - `Rejection Reason stored: 'Small business requires COTS SaaS, not EVON custom engineering'`

### 5. Audit Log Outputs
`data/audit.log` recorded the actions and outcomes verbatim:
```text
2026-08-21 13:15:22,171 - User: emp_fca0fe | Role: employee | Endpoint: Protected | Success: False | Details: Permission denied. Required: ['admin']
2026-08-21 13:15:22,177 - User: emp_fca0fe | Role: employee | Endpoint: Protected | Success: False | Details: Permission denied. Required: ['admin']
2026-08-21 13:15:22,190 - User: admin_744e7a | Role: admin | Endpoint: POST /drafts/44d78895-44be-4f55-b2cf-1dee647a49c7/approve | Success: True | Details: Approved draft for 'Apple Inc.'
2026-08-21 13:15:22,210 - User: admin_744e7a | Role: admin | Endpoint: POST /drafts/d62966eb-8027-416f-baa5-968c009d0dfd/reject | Success: True | Details: Rejected draft for 'The Black Gold Gym'. Reason: Small business requires COTS SaaS, not EVON custom engineering
2026-08-21 13:28:39,863 - User: quota_admin_497584 | Role: admin | Endpoint: POST /drafts | Success: False | Details: Draft generation failed due to quota limit. No draft created.
```

### 6. Backward Compatibility for Pre-Migration Drafts
* **The Bug**: Drafts generated prior to the Phase 9 schema migration had `NULL` values in the new `profile_json` column. Consequently, the Review Queue UI displayed placeholders like `"No description available"` despite the draft text containing rich, grounded details.
* **The Fix**: Added a dynamic backward-compatibility resolver inside `_enrich_draft_row` in [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py). If `profile_json` is missing but a valid `research_id` exists, the database service dynamically retrieves and parses the target company's profile directly from the `research_profiles` table, restoring complete UI data population.

### 7. Hallucination Prevention & Zero Opportunities Refusal
* **Draft Generation Block**: Refactored `generate_drafts` in [draft_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/draft_service.py). If the `opportunities` list is empty, the server now immediately bypasses all LLM calls (saving API quota) and returns a structured refusal message indicating that no EVON custom engineering opportunity was found:
  - **Internal Draft**: A formal assessment stating that no value fit exists and recommending no active sales outreach.
  - **Outreach Draft**: `"No genuine business opportunity was identified for this company — recommend not pursuing outreach"`.
* **Prompt Grounding Constraints**: Enhanced the Gemini prompt templates in `draft_service.py` to include a strict `CRITICAL GROUNDING INSTRUCTION`. This prohibits the model from using external pre-trained knowledge or assuming/extrapolating facts not present in the TARGET COMPANY PROFILE or IDENTIFIED OPPORTUNITIES context.
* **Database Auditing & Fabricated Record Deletion**:
  - Wrote and executed `scratch/audit_drafts.py` to inspect all stored drafts.
  - Successfully deleted the specific pre-migration Gaylord Xpress draft (ID: `92d78f50-664a-472d-bbde-21f2cae7fd8a`) containing fabricated details.
  - Audited all remaining draft records (Apple Inc., Black Gold Gym) and verified that all draft text claims are fully grounded in the source data.
  - Verified the zero-match refusal logic using `scratch/test_empty_opportunities.py`.

### 8. Python Empty List Falsey Serialization Fix
* **The Bug**: In Python, evaluating `if opportunities` checks if the list is truthy (non-empty). When the opportunities list was empty (`[]`), the `save_draft` method evaluated this check as `False` and saved it as `None` (NULL) in SQLite. This caused the UI to mistake new empty opportunity records for pre-migration records.
* **The Fix**: Changed the serialization verification inside `save_draft` in [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) to explicitly check `if opportunities is not None`. This guarantees that empty lists are correctly saved as the string `'[]'` in SQLite.
* **UI Text Improvements**: Changed the fallback message in [index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html) to say:
  - **Pre-migration records (NULL)**: `"Opportunities data not available for this older record."`
  - **Evaluated empty records (`[]`)**: `"No matching opportunities were found."`
* **Verification**: Ran `scratch/regenerate_drafts_v2.py` which confirmed that brand new Gym and Gaylord drafts are successfully stored with `opportunities_json = '[]'` inside the SQLite database, and the UI displays the correct text.

### 9. Database Clean-Slate Reset & Verification Seeding
* **Clean Reset**: Executed a complete database wipe in [populate_clean_reset_data.py](file:///C:/Users/Dell/.gemini/antigravity/brain/b182b695-7559-48d4-a621-801e019994a1/scratch/populate_clean_reset_data.py), clearing all rows from the `drafts` and `research_profiles` tables.
* **Seed Verification Data**: Seeded the database with exactly three clean, fully-populated, consistent records:
  1. **Apple Inc.** (`apple_draft_2026`): Contains a valid matched opportunity in `opportunities_json` and fully-grounded drafts.
  2. **The Black Gold Gym** (`gym_draft_2026`): Contains `[]` in `opportunities_json` (properly serialized as `'[]'` string in SQLite) and drafts indicating early refusal of sales outreach.
  3. **Gaylord Xpress** (`gaylord_draft_2026`): Contains `[]` in `opportunities_json` (properly serialized as `'[]'` string in SQLite) and drafts indicating early refusal of sales outreach.
* **Audit**: Verified that both tables contain exactly these 3 records, with clean column states and zero fabricated details.

---

# Walkthrough - Phase 10: Outreach Delivery Integration

We have successfully implemented and verified **Phase 10: Outreach Delivery Integration** in the EVON BD Agent system. This completes the end-to-end sales prospecting funnel, from initial web research and capability matching to admin-approved email transmission.

---

## Changes Implemented

### 1. Database Schema Migrations
* **Delivery Columns**: Added `sent_at`, `sent_to`, and `send_error` columns to the `drafts` SQLite table in [db_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/db_service.py) via safe, non-destructive startup migrations during database initialization.
* **Status Differentiation**: Modified the database list queries and send operations:
  - Real email deliveries are persisted with a status of `'sent'`.
  - Dry-run simulated email deliveries are persisted with a status of `'sent_dryrun'`.
  - Query filters for fetching `'sent'` items return both `'sent'` and `'sent_dryrun'` rows seamlessly.

### 2. Email Delivery Service
* **Integration Module**: Created [outreach_service.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/services/outreach_service.py) to support standard SMTP and SendGrid APIs.
* **Dry-Run / Mock Fallback Mode**: Designed an automatic fallback: if `.env` variables are empty or contain default placeholders, the system activates **Dry-Run Mode**. It logs the complete email payload to the server logs, updates database records to `'sent_dryrun'` status successfully, and returns a warning flags response, permitting full E2E testing without needing live credentials.

### 3. API Route Enhancements
* **Endpoint creation**: Exposed `POST /drafts/{draft_id}/send` in [drafts.py](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/app/routes/drafts.py), protected by the `require_admin` role dependency. It validates that the targeted draft status is `'approved'` before allowing delivery.
* **Contact Email Extractor**: Added a regular expression utility `extract_emails_from_any` to automatically harvest all email addresses found within the nested fields of a company's research profile JSON. Discovered emails are returned in a new `recipient_emails` list inside `DraftResponse`.

### 4. Front-end "Outreach Delivery" Tab & Send Confirmation Modal
* **Sub-Tab Navigation**: Added an "Outreach Delivery" tab in [index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html) beside the "Review Queue".
* **Dashboard Queue**:
  - **Left column**: Dual lists showing drafts **Approved for Outreach** and a complete **Sent Outreach Log** with timestamps.
  - **Right column**: Message composer displaying recipient email (with interactive suggestion chips representing extracted emails), subject, and body editor.
* **Unambiguous Dry-Run Indicators**:
  - **Badge in Queue**: Items in the Sent log show a bold yellow `DRY RUN` badge or a green `SENT` badge.
  - **Badge in Details**: The active details view shows a prominent yellow status badge: `⚠️ DRY RUN — NOT ACTUALLY SENT`.
  - **Banner Alert**: The details panel renders a distinct warning banner detailing the simulation date, recipient, and reminding the user that no actual email was transmitted.
* **Multi-Step Send Modal**: Clicking "Send via Email" opens a blur-overlay verification modal presenting recipient, subject, and body previews. The admin must check a verification box before the "Confirm & Send" delivery action becomes enabled.

---

## Verification Results

### 1. Automated Integration Tests
We wrote and executed `scratch/test_outreach_send.py` within the project's virtual environment:
* Confirmed that `extract_emails_from_any` successfully matches multiple emails nested inside the search news text.
* Verified that non-admin accounts (employees) are blocked with `403 Forbidden`.
* Verified that attempts to deliver non-approved drafts are blocked with `400 Bad Request`.
* Verified that a successful send in Dry-Run mode persists correct status (`sent_dryrun`), `sent_to`, and `sent_at` in the database.

### 2. Manual E2E Simulation
Ran `scratch/manual_verify_e2e.py` to seed and test a full execution loop:
* Approved the `gaylord_draft_2026` record -> status successfully updated from `pending_review` to `approved`.
* Delivered the outreach message -> server processed simulated send, generated ISO 8601 UTC timestamp, and updated database status to `sent_dryrun`.
* Verified that audit events are logged verbatim in `data/audit.log` showing:
  `User: verify_admin | Role: admin | Endpoint: POST /drafts/gaylord_draft_2026/send | Success: True | Details: Sent outreach to 'manager@gaylordxpress.com' via smtp (Dry-run) (Dry-run: True)`

---

## Supplemental: Company Research Pipeline Portal UI

We have successfully implemented and verified the **Company Research Tab** inside the RAG Portal Admin Control Center. This interface gives BDRs a clean, step-by-step pipeline to run the complete prospecting workflow on any target business.

### 1. Front-end Subtab & Layout Panels
* **Navigation Subtab**: Added a **Company Research** subtab button in [static/index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html) under the Admin Control Center, visible only to authenticated administrator users.
* **Prospect Input Form**: Built a custom text input and submit handler enabling BDRs to type a company name or website URL.
* **Pipeline Progress Tracker**: Added an interactive progress sidebar showing Step 1 (Web Research), Step 2 (Identify Opportunities), and Step 3 (Generate Drafts). The step indicators dynamically light up to guide the user.

### 2. Multi-Step Execution & Output
* **Step 1: Web Research**: Submit form calls `POST /research` to run the Tavily web search pipeline, displaying a structured layout containing company description, tech stack badges, recent news items, and business needs.
* **Step 2: Find Opportunities**: Displays a button to run capability matching (`POST /opportunities`), listing all retrieved EVON services along with evidence quotes and fit explanations (or a clear mismatch warning).
* **Step 3: Generate Drafts**: Triggers draft generation (`POST /drafts`), returning copyable summaries and customized outreach emails. The drafts are automatically saved to the persistent SQLite database in `pending_review` status.

### 3. Loading, Errors, & Logout Safety
* **Loading Indicators**: Added dedicated spinners and description labels for each backend request.
* **Bespoke Exception Handling**: Displays clear error messages if quota limit or network failures occur.
* **Logout Purges**: Added reset logic to `performLogout` to ensure that active company inputs and pipeline states are fully cleared when session tokens terminate.

---

## Supplemental: Company Research Pipeline Portal UI

We have successfully implemented and verified the **Company Research Tab** inside the RAG Portal Admin Control Center. This interface gives BDRs a clean, step-by-step pipeline to run the complete prospecting workflow on any target business.

### 1. Front-end Subtab & Layout Panels
* **Navigation Subtab**: Added a **Company Research** subtab button in [static/index.html](file:///c:/Users/Dell/Desktop/RAG%20for%20evon/static/index.html) under the Admin Control Center, visible only to authenticated administrator users.
* **Prospect Input Form**: Built a custom text input and submit handler enabling BDRs to type a company name or website URL.
* **Pipeline Progress Tracker**: Added an interactive progress sidebar showing Step 1 (Web Research), Step 2 (Identify Opportunities), and Step 3 (Generate Drafts). The step indicators dynamically light up to guide the user.

### 2. Multi-Step Execution & Output
* **Step 1: Web Research**: Submit form calls `POST /research` to run the Tavily web search pipeline, displaying a structured layout containing company description, tech stack badges, recent news items, and business needs.
* **Step 2: Find Opportunities**: Displays a button to run capability matching (`POST /opportunities`), listing all retrieved EVON services along with evidence quotes and fit explanations (or a clear mismatch warning).
* **Step 3: Generate Drafts**: Triggers draft generation (`POST /drafts`), returning copyable summaries and customized outreach emails. The drafts are automatically saved to the persistent SQLite database in `pending_review` status.

### 3. Loading, Errors, & Logout Safety
* **Loading Indicators**: Added dedicated spinners and description labels for each backend request.
* **Bespoke Exception Handling**: Displays clear error messages if quota limit or network failures occur.
* **Logout Purges**: Added reset logic to `performLogout` to ensure that active company inputs and pipeline states are fully cleared when session tokens terminate.
