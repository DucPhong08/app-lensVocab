# AGENTS.md — AI Agent Guidance for LensVocab Backend

Welcome! This document provides immediate context, operational rules, and architectural boundaries for the **LensVocab Backend** service.

---

## 1. Tech Stack & Exact Versions

- **Runtime & Language:** Python 3.12 (managed via `.venv`)
- **Web Framework:** FastAPI 0.115.0 with Uvicorn 0.30.6
- **Database & ODM:** MongoDB Atlas with Beanie 1.26.0 & Motor 3.6.0
- **Cache & Rate Limiting:** Redis 5.1.0 (`hiredis`)
- **Validation & Serialization:** Pydantic 2.9.2 & Pydantic-Settings 2.5.2
- **Auth & Security:** Native Bcrypt >= 4.0.0, Python-Jose 3.3.0 (HS256 JWT)
- **AI Services (AWS SDK Boto3 >= 1.36.0):**
  - AWS Rekognition: Object & label detection
  - AWS Bedrock: `amazon.nova-lite-v1:0` (vocabulary & contextual examples generation)
  - AWS Bedrock Embeddings: `amazon.titan-embed-text-v2:0` (1024-dimensional semantic vectors)
  - AWS Polly: Text-to-speech audio synthesis (`Joanna` standard voice)
- **Linter & Code Style:** Ruff >= 0.6.0 (target Python 3.12, line length 100)

---

## 2. Key Domains & Module Map

| Directory / Module | Responsibility |
| :--- | :--- |
| [`app/main.py`](file:///home/phong/Môn%20học/app-LensVocab/app/main.py) | FastAPI application entrypoint, lifespan event (Beanie & Redis lifecycle), middleware, exception handlers. |
| [`app/models/`](file:///home/phong/Môn%20học/app-LensVocab/app/models) | **Beanie Documents (Active Record):** `User`, `GlobalFlashcard`, `UserFlashcard`, `ReviewLog`, `SystemSetting`. |
| [`app/schemas/`](file:///home/phong/Môn%20học/app-LensVocab/app/schemas) | **API Contracts (Pydantic DTOs):** Request/Response schemas for `auth`, `flashcard`, `review`, `user`, `vision`, `admin`. |
| [`app/services/`](file:///home/phong/Môn%20học/app-LensVocab/app/services) | **Pure Business Services:** `ai_service`, `auth_service`, `cache_service`, `degradation_service`, `flashcard_service`, `quota_service`, `review_service`, `sm2_service`, `tier_service`. |
| [`app/routers/`](file:///home/phong/Môn%20học/app-LensVocab/app/routers) | **Thin HTTP Controllers:** Dependency injection, error translation, delegating to services (`auth`, `flashcards`, `review`, `users`, `vision`, `admin`). |
| [`app/dependencies/`](file:///home/phong/Môn%20học/app-LensVocab/app/dependencies) | FastAPI authentication dependencies: `get_current_user`, `get_current_admin`. |
| [`docs/`](file:///home/phong/Môn%20học/app-LensVocab/docs) | Architecture guides, vision pipeline, SM-2 spaced repetition specs, and optimization rollout notes. |

---

## 3. Exact Operational Commands

Run all commands using the local virtual environment `.venv`:

| Action | Command | Makefile Alias |
| :--- | :--- | :--- |
| **Development Server** | `.venv/bin/uvicorn app.main:app --reload` | `make dev` |
| **Run All Tests** | `.venv/bin/python -m unittest discover tests` | `make test` |
| **Linter Check** | `.venv/bin/ruff check .` | `make lint` |
| **Format Code** | `.venv/bin/ruff format .` | `make format` |
| **Full QA Gate** | `bash .agents/scripts/run-qa-check.sh` | — |
| **Local Dependencies** | `docker compose up -d` | — |

---

## 4. Active Roles & Governance (Pruned Role Matrix)

Only roles with active responsibilities in this backend service are activated:

- **1. Tech Lead / Orchestrator:** Architecture decisions, Gate reviews, and trade-off resolutions.
- **2. Product Manager (PM):** Spaced repetition logic, learning workflows, quota tiers (Free vs. Premium), and guest trial rules.
- **5. Backend Engineer:** FastAPI routes, async business services, SSE streaming, AWS integrations, and error response contracts.
- **6. QA Engineer:** Regression test suite (`unittest`), test boundaries, test integrity, and mocking patterns.
- **9. Database Architect / DBA:** Beanie ODM schemas, MongoDB Atlas vector indexes, Redis caching policies, atomic quota pipelines.
- **10. Dependency & Version Manager:** Python package compatibility, lockless pinning in `requirements.txt`, security advisories.
- **11. Software Architect:** Layer boundaries (Router -> Service -> Active Record), degradation fallbacks, and anti-bloat standards.
- **12. Security Engineer:** JWT validation, bcrypt password limits (72 UTF-8 bytes), credential redaction in validation errors, and admin RBAC.
- **13. DevOps / SRE:** Docker containerization, health check endpoints, environment variable configuration, and AWS IAM policies.

*(Roles pruned: UI/UX Designer, Frontend/Mobile Engineer, Data Analyst, Data Engineer — UI is located in a separate repository `lenvocab-ui`; no ETL/data warehouse pipelines exist here).*

---

## 5. Execution Contract & Hard Constraints

1. **User Communication:** Always communicate with the user in **Vietnamese**. All code, commits, and comments remain in **English**.
2. **Two-Gate Discipline:**
   - **Gate 1 (Fact Check & Impact):** Inspect live code and schemas before proposing or implementing changes. Stop at Checkpoint 1 for architectural decisions; stop at Checkpoint 2 for destructive actions.
   - **Gate 2 (Observed Evidence & YAGNI):** Every change must be verified with observed command output. No speculative features or unrelated refactors.
3. **Hard Technical Constraints:**
   - **100% `snake_case`:** Strictly enforce `snake_case` across all API JSON responses, database fields, and schemas.
   - **NO `app/repositories/`:** Use Beanie Active Record directly.
   - **Vector Embedding Consistency:** Titan Embed v2 (1024 dimensions) is hardwired to MongoDB Atlas `vocab_embedding_index`.
   - **NO bare `git`:** Always use `rtk git <command>` (e.g. `rtk git status`, `rtk git commit`).
   - **Offline Unit Tests:** Use `User.model_construct(...)` instead of `User(...)` to prevent `CollectionWasNotInitialized` errors when MongoDB is offline.
