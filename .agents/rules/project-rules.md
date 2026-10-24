---
trigger: always_on
description: 'Architecture, coding, database, and security conventions for LensVocab Backend'
---

# LensVocab Backend Project Rules

These rules reflect the **confirmed codebase patterns** in LensVocab. All changes must adhere strictly to these standards.

---

## 1. Core Architectural Boundaries

- **Pattern:** Layered Service Architecture with Active Record:
  - **HTTP Routers (`app/routers/`):** Thin controllers responsible only for dependency resolution, HTTP status mapping, error envelope formatting, and delegating work to services.
  - **Business Services (`app/services/`):** Pure async business logic. Services handle validation, third-party integrations (AWS, Redis, Mongo), transaction boundaries, and domain exceptions.
  - **Entities / Documents (`app/models/`):** Beanie ODM (`Document`) representing collections in MongoDB Atlas.
  - **API Contracts (`app/schemas/`):** Pydantic v2 models for request/response validation.
- **NO Repository Layer:** Do not create `app/repositories/` or repository wrapper classes. Interact directly with Beanie models (`Model.find()`, `Model.find_one()`, `model.save()`, `model.insert()`).

---

## 2. Naming, Contracts & JSON Serialization

- **100% `snake_case` Enforcement:** All JSON request/response keys, database fields, query parameters, and variable names must use `snake_case`. Never introduce `camelCase`.
- **Response Envelopes & Error Redaction:**
  - Standard errors follow `{"status_code": int, "message": str, "error": str, "data": null, "detail": ...}`.
  - In `422 Unprocessable Entity` validation responses, never echo the raw `input` field to prevent leaking user passwords or sensitive inputs.
- **Direct-Purpose Function Naming:**
  - Strip ceremonial filler prefixes (`build`, `perform`, `execute`) where a concise verb suffices (`filter`, `paginate`, `query`, `to_dto`).
  - No class/module stuttering inside service methods (`auth_service.register_user` or `authenticate_user`).

---

## 3. Database & Caching Conventions

- **MongoDB (Motor + Beanie):**
  - Document models: `User`, `GlobalFlashcard`, `UserFlashcard`, `ReviewLog`, `SystemSetting`.
  - Atomic Quota Deductions: Quota checks and decrements must run as atomic pipeline updates via Motor (`find_one_and_update` with `$cond` and `$subtract`) to prevent race conditions.
  - Vector Index: `vocab_embedding_index` is hardwired to 1024-dimensional vectors from `amazon.titan-embed-text-v2:0`. Do NOT change embedding dimensions without an Atlas vector index migration.
- **Redis Cache & Keyspace:**
  - Daily quota rate limiting / scan cooldowns.
  - Flashcard cache: `flashcard:kw:{keyword}` and vector cache (TTL: 7 days).
  - Draft flashcards: ephemeral TTL (30 minutes).

---

## 4. Spaced Repetition (SM-2) & Quota Rules

- **SM-2 Algorithm (`app/services/sm2_service.py`):**
  - Quality score: `0..5`.
  - Quality `< 3`: reset repetition to 0, interval to 1 day.
  - Quality `>= 3`: increment repetition, multiply interval by easiness factor `efactor` (minimum `1.30`).
- **Anti-Demotivation Cap:** Daily review queues are capped by `daily_review_goal` (default 15) to prevent learner burnout.

---

## 5. Security & Authentication

- **Password Hashing:** Native `bcrypt.hashpw` with salt. Never use outdated hashing or insecure defaults.
- **Password Length Boundary:** Enforce a maximum of **72 UTF-8 bytes** on registration passwords (`validate_password` in `RegisterRequest`) to prevent silent bcrypt truncation.
- **JWT Authentication:**
  - Bearer token signed with `HS256`.
  - Subject claim (`sub`) must be a string UUID corresponding to `User.id`.
  - Always verify `user.is_active` in `get_current_user`.
  - Admin endpoints require `get_current_admin` (`is_admin=True`).

---

## 6. Testing & Tooling Standards

- **Offline Unit Testing (Crucial Beanie Rule):**
  - When writing offline unit tests without an active MongoDB connection, NEVER instantiate `User(...)` directly.
  - Always use `User.model_construct(...)` (or `GlobalFlashcard.model_construct(...)`) to avoid Beanie's `CollectionWasNotInitialized` runtime exception.
- **Git Execution Rule:**
  - NEVER execute bare `git` commands. Always prefix with `rtk`: `rtk git status`, `rtk git add .`, `rtk git commit -m "..."`.
