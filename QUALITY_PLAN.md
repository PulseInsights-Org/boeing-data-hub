# Boeing Data Hub - Quality Assurance & Testing Plan

**Version:** 1.0
**Author:** Quality Engineering Team
**Date:** February 2026
**Status:** Proposed

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State Assessment](#2-current-state-assessment)
3. [Testing Strategy & Test Suite Plan](#3-testing-strategy--test-suite-plan)
4. [Priority Implementation Plan (P0/P1/P2)](#4-priority-implementation-plan-p0p1p2)
5. [Coverage Gating (85% Minimum)](#5-coverage-gating-85-minimum)
6. [Pre-Commit Hook Design](#6-pre-commit-hook-design)
7. [Change Summary Script Specification](#7-change-summary-script-specification)
8. [Implementation Timeline](#8-implementation-timeline)
9. [Appendices](#9-appendices)

---

## 1. Executive Summary

This document outlines a comprehensive quality assurance strategy for the Boeing Data Hub, a full-stack application integrating Boeing aviation parts API with Shopify e-commerce. The plan establishes:

- **Testing pyramid** covering unit, integration, and E2E tests across backend (FastAPI/Celery) and frontend (React/TypeScript)
- **85% minimum code coverage** enforced via CI/CD gates
- **Pre-commit hooks** ensuring code quality before commits reach the repository
- **Automated change summaries** for improved code review quality

### Key Metrics Targets

| Metric | Current | Target | Timeline |
|--------|---------|--------|----------|
| Backend Unit Test Coverage | ~15% | 85% | P1 (4 weeks) |
| Frontend Test Coverage | 0% | 85% | P1 (4 weeks) |
| Integration Test Coverage | ~5% | 70% | P2 (6 weeks) |
| Pre-commit Hook Pass Rate | N/A | 100% | P0 (1 week) |
| PR Review Time Reduction | N/A | 30% | P2 (6 weeks) |

---

## 2. Current State Assessment

### 2.1 Existing Test Infrastructure

**Backend (`/backend/tests/`)**
- Framework: pytest 8.3.4 with pytest-asyncio
- Test files: 6 modules (test_logout.py, test_sync_dispatcher.py, test_sync_helpers.py, test_sync_store.py)
- Fixtures: Basic TestClient, mock Cognito token
- Coverage tool: pytest-cov (installed, not enforced)
- Markers defined: `unit`, `integration`, `e2e`, `slow`

**Frontend (`/frontend/`)**
- Test framework: **None installed**
- No test files present
- ESLint configured but not running in CI

### 2.2 Gap Analysis

| Component | Gap | Risk Level |
|-----------|-----|------------|
| Backend API Routes | 8 route files, only auth tested | HIGH |
| Backend Services | 4 services untested | HIGH |
| Backend Clients | shopify_client.py (50KB) untested | CRITICAL |
| Celery Tasks | 6 task files, partial coverage | HIGH |
| Frontend Components | 15+ dashboard components untested | HIGH |
| Frontend Services | 8 API service files untested | HIGH |
| Database Stored Procedures | 10+ RPC functions untested | MEDIUM |
| Pre-commit Hooks | Not configured | MEDIUM |
| CI/CD Testing | Tests not run in pipeline | CRITICAL |

### 2.3 Critical Untested Paths

1. **Shopify Client** (`shopify_client.py`) - 50.6KB of untested API integration
2. **Bulk Operations** (`bulk.py`) - 20.7KB of batch processing logic
3. **Publishing Tasks** (`publishing.py`) - 19KB of Shopify publishing
4. **Supabase Store** (`supabase_store.py`) - 31.8KB of database operations
5. **Frontend Auth Flow** - AWS Cognito integration untested

---

## 3. Testing Strategy & Test Suite Plan

### 3.1 Testing Pyramid

```
                    ┌─────────────┐
                    │    E2E      │  5%  - Critical user journeys
                    │   Tests     │       - Browser automation
                    ├─────────────┤
                    │ Integration │  25% - API contracts
                    │   Tests     │       - Database operations
                    │             │       - External service mocks
                    ├─────────────┤
                    │   Unit      │  70% - Business logic
                    │   Tests     │       - Utility functions
                    │             │       - Component rendering
                    └─────────────┘
```

### 3.2 Backend Test Suite Plan

#### 3.2.1 Unit Tests

**Location:** `backend/tests/unit/`

| Module | Test File | Priority | Coverage Target |
|--------|-----------|----------|-----------------|
| `app/core/config.py` | `test_config.py` | P1 | 100% |
| `app/core/auth.py` | `test_auth.py` | P0 | 100% |
| `app/core/cognito.py` | `test_cognito.py` | P0 | 95% |
| `app/utils/boeing_normalize.py` | `test_boeing_normalize.py` | P1 | 95% |
| `app/utils/rate_limiter.py` | `test_rate_limiter.py` | P1 | 95% |
| `app/utils/sync_helpers.py` | `test_sync_helpers.py` | P1 | 90% (exists, expand) |
| `app/schemas/*.py` | `test_schemas.py` | P2 | 90% |

#### 3.2.2 Integration Tests

**Location:** `backend/tests/integration/`

| Component | Test File | External Dependencies | Mock Strategy |
|-----------|-----------|----------------------|---------------|
| Boeing Client | `test_boeing_client_integration.py` | Boeing API | respx HTTP mocking |
| Shopify Client | `test_shopify_client_integration.py` | Shopify API | respx HTTP mocking |
| Supabase Store | `test_supabase_integration.py` | Supabase | Test database instance |
| Celery Tasks | `test_celery_integration.py` | Redis | fakeredis |
| API Routes | `test_routes_integration.py` | All services | Mock services layer |

#### 3.2.3 E2E Tests

**Location:** `backend/tests/e2e/`

| Scenario | Test File | Description |
|----------|-----------|-------------|
| Product Search Flow | `test_product_search_e2e.py` | Full search → extract → normalize |
| Publish Flow | `test_publish_e2e.py` | Staging → Shopify publish |
| Sync Cycle | `test_sync_cycle_e2e.py` | Hourly sync dispatch |
| Authentication | `test_auth_e2e.py` | Login → JWT → Logout |

### 3.3 Frontend Test Suite Plan

#### 3.3.1 Testing Framework Setup

**Required Dependencies:**
- @testing-library/react
- @testing-library/jest-dom
- @testing-library/user-event
- vitest
- @vitest/coverage-v8
- jsdom
- msw

#### 3.3.2 Component Tests

**Location:** `frontend/src/**/*.test.tsx`

| Component | Test File | Priority | Key Scenarios |
|-----------|-----------|----------|---------------|
| AuthContext | `contexts/AuthContext.test.tsx` | P0 | Login, logout, token refresh |
| ProductTable | `components/dashboard/ProductTable.test.tsx` | P1 | Render, sort, filter, select |
| SyncStatusCards | `components/dashboard/SyncStatusCards.test.tsx` | P1 | Status display, loading states |
| BulkOperationsPanel | `components/dashboard/BulkOperationsPanel.test.tsx` | P1 | Batch start, progress, cancel |
| AutoSyncPanel | `components/dashboard/AutoSyncPanel.test.tsx` | P1 | Enable/disable, schedule view |

#### 3.3.3 Service/Hook Tests

| Service/Hook | Test File | Priority |
|--------------|-----------|----------|
| authService | `services/authService.test.ts` | P0 |
| boeingService | `services/boeingService.test.ts` | P1 |
| shopifyService | `services/shopifyService.test.ts` | P1 |
| bulkService | `services/bulkService.test.ts` | P1 |
| useBulkOperations | `hooks/useBulkOperations.test.ts` | P1 |
| useSyncDashboard | `hooks/useSyncDashboard.test.ts` | P1 |

### 3.4 Database Test Strategy

#### 3.4.1 Stored Procedure Tests

**Location:** `backend/tests/database/`

| RPC Function | Test File | Scenarios |
|--------------|-----------|-----------|
| `increment_batch_extracted` | `test_batch_rpcs.py` | Increment, concurrent updates |
| `record_batch_failure` | `test_batch_rpcs.py` | Failure recording, JSONB append |
| `get_batch_stats` | `test_batch_rpcs.py` | Stats calculation, edge cases |
| `check_batch_completion` | `test_batch_rpcs.py` | Completion detection, status update |

---

## 4. Priority Implementation Plan (P0/P1/P2)

### 4.1 P0 - Critical (Week 1-2)

**Objective:** Establish quality gates that prevent regressions in production.

| Task | Description | Owner | Deliverable |
|------|-------------|-------|-------------|
| P0-1 | Configure pre-commit hooks | DevOps | `.pre-commit-config.yaml` |
| P0-2 | Add CI test stage | DevOps | GitHub Actions workflow |
| P0-3 | Backend auth tests | Backend | `tests/unit/test_auth.py` |
| P0-4 | Backend cognito tests | Backend | `tests/unit/test_cognito.py` |
| P0-5 | Frontend test setup | Frontend | Vitest config + setup |
| P0-6 | Frontend AuthContext tests | Frontend | `AuthContext.test.tsx` |

**P0 Acceptance Criteria:**
- Pre-commit hooks run on every commit
- CI pipeline fails if tests fail
- Auth module has 100% test coverage
- Frontend has test infrastructure ready

### 4.2 P1 - High Priority (Week 3-6)

**Objective:** Achieve 85% coverage on core business logic.

| Task | Description | Owner | Target Coverage |
|------|-------------|-------|-----------------|
| P1-1 | Boeing client tests | Backend | 95% |
| P1-2 | Shopify client tests | Backend | 90% |
| P1-3 | Sync dispatcher tests | Backend | 90% (expand existing) |
| P1-4 | Rate limiter tests | Backend | 95% |
| P1-5 | Boeing normalize tests | Backend | 95% |
| P1-6 | Supabase store tests | Backend | 85% |
| P1-7 | Celery task tests | Backend | 80% |
| P1-8 | Dashboard components | Frontend | 85% |
| P1-9 | API service tests | Frontend | 90% |
| P1-10 | Custom hooks tests | Frontend | 90% |

**P1 Milestones:**
- Week 3: Backend clients tested (Boeing, Shopify)
- Week 4: Backend services & utils tested
- Week 5: Frontend components tested
- Week 6: Integration tests & coverage enforcement

### 4.3 P2 - Medium Priority (Week 7-10)

**Objective:** Complete integration tests and quality automation.

| Task | Description | Owner | Deliverable |
|------|-------------|-------|-------------|
| P2-1 | E2E test suite | QA | `tests/e2e/` directory |
| P2-2 | Database RPC tests | Backend | `tests/database/` |
| P2-3 | Change summary script | DevOps | `scripts/change_summary.py` |
| P2-4 | PR template update | DevOps | `.github/PULL_REQUEST_TEMPLATE.md` |
| P2-5 | Test data factories | Backend | `tests/factories/` |
| P2-6 | Visual regression tests | Frontend | Playwright config |
| P2-7 | Performance benchmarks | Backend | `tests/benchmarks/` |

---

## 5. Coverage Gating (85% Minimum)

### 5.1 Coverage Configuration

**Backend Configuration:**
- Use pytest-cov with `--cov-fail-under=85`
- Generate reports in term-missing, html, and xml formats
- Enable branch coverage
- Exclude test files, pycache, and migrations from coverage

**Frontend Configuration:**
- Use Vitest with v8 coverage provider
- Set global thresholds for branches, functions, lines, and statements at 85%
- Exclude node_modules, test files, type definitions, and shadcn UI components

### 5.2 CI/CD Coverage Gates

The GitHub Actions workflow should:
1. Run backend linting (ruff, black, mypy)
2. Run backend tests with coverage threshold
3. Run frontend linting and type checking
4. Run frontend tests with coverage threshold
5. Upload coverage reports to Codecov
6. Fail the build if any threshold is not met

### 5.3 Coverage Exceptions

Some files may be excluded from coverage requirements with proper justification:

| Path | Reason | Alternative |
|------|--------|-------------|
| `app/core/config.py` | Configuration loading | Manual verification |
| `celery_app/celery_config.py` | Celery setup | Integration tests |
| `frontend/src/components/ui/*` | Third-party shadcn | Upstream tested |

---

## 6. Pre-Commit Hook Design

### 6.1 Overview

The pre-commit hook system ensures code quality before commits reach the repository. It runs in stages with progressive checks, failing fast on critical issues.

### 6.2 Hook Stages

**Stage 1: Quick Checks (under 5 seconds)**
- Trailing whitespace trimming
- End of file fixing
- YAML and JSON syntax validation
- Large file detection (max 1000KB)
- Merge conflict marker detection
- Private key detection
- Block direct commits to main branch

**Stage 2: Python Formatting (under 10 seconds)**
- Black formatting with 100 character line length
- Ruff linting with auto-fix

**Stage 3: TypeScript/Frontend Checks**
- ESLint with zero warnings tolerance
- TypeScript compiler type checking

**Stage 4: Python Type Checking (under 30 seconds)**
- MyPy with ignore-missing-imports flag

**Stage 5: Targeted Tests (Changed files only)**
- Backend: Run tests mapped to changed source files
- Frontend: Run tests for changed components/services

**Stage 6: Security Checks**
- Bandit security scanning for Python code
- Exclude test files from security checks

**Stage 7: Commit Message Validation**
- Commitizen for conventional commit format

### 6.3 Targeted Test Runner

The targeted test runner script should:
1. Accept a list of changed files as input
2. Map source files to their corresponding test files
3. Detect if core files changed that require full test suite
4. Run only the relevant tests
5. Provide clear failure explanations with next steps

**Test Mapping Examples:**
- `app/core/auth.py` → `tests/unit/test_auth.py`
- `app/clients/boeing_client.py` → `tests/unit/test_boeing_client.py`, `tests/integration/test_boeing_client_integration.py`
- `celery_app/tasks/sync_dispatcher.py` → `tests/test_sync_dispatcher.py`

**Full Suite Triggers:**
- `app/main.py`
- `celery_app/celery_config.py`
- `conftest.py`
- `pytest.ini`

### 6.4 Failure Message Guidelines

When hooks fail, provide:
- Clear identification of which check failed
- Explanation of what the problem is
- Specific instructions on how to fix it
- Commands to run to resolve the issue
- Links to documentation for complex issues

---

## 7. Change Summary Script Specification

### 7.1 Overview

The change summary script automatically generates human-readable summaries of code changes for pull request descriptions, reducing review time and improving documentation.

### 7.2 Features

**Change Classification:**
- Feature, bugfix, refactor, docs, test, chore, security, performance

**Risk Assessment:**
- LOW: Utilities, UI components
- MEDIUM: API routes, business services, React hooks
- HIGH: Core auth/config, database layer, API clients, Celery tasks
- CRITICAL: Database schema, credentials handling

**Component Detection:**
- Map file paths to component names
- Count files changed per component
- Aggregate risk levels

**Output Generation:**
- Change overview with metrics (files, additions, deletions)
- Risk level with visual indicator
- Migration and test change flags
- Components affected table
- Breaking change warnings
- Testing recommendations
- Review checklist

### 7.3 Integration

**GitHub Actions workflow should:**
1. Run on pull request open and synchronize events
2. Generate change summary comparing to base branch
3. Update PR description with auto-generated section
4. Use markers to replace existing summary on updates

---

## 8. Implementation Timeline

```
Week 1-2: P0 - Foundation
├── Day 1-2: Pre-commit hook setup & testing
├── Day 3-4: CI test pipeline configuration
├── Day 5-7: Backend auth module tests (100% coverage)
├── Day 8-10: Frontend test infrastructure setup
└── Day 11-14: Frontend AuthContext tests + integration

Week 3-4: P1 - Core Coverage (Backend)
├── Day 15-17: Boeing client tests
├── Day 18-20: Shopify client tests
├── Day 21-23: Celery task tests
└── Day 24-28: Utilities & sync tests (expand existing)

Week 5-6: P1 - Core Coverage (Frontend)
├── Day 29-31: Dashboard component tests
├── Day 32-34: Service layer tests (MSW mocking)
├── Day 35-37: Custom hooks tests
└── Day 38-42: Integration tests & coverage verification

Week 7-8: P2 - Integration & Automation
├── Day 43-45: E2E test suite setup
├── Day 46-48: Database RPC tests
├── Day 49-51: Change summary script
└── Day 52-56: PR template & workflow updates

Week 9-10: P2 - Polish & Documentation
├── Day 57-59: Test data factories
├── Day 60-62: Performance benchmarks
├── Day 63-65: Documentation updates
└── Day 66-70: Team training & handoff
```

---

## 9. Appendices

### 9.1 File Structure After Implementation

```
boeing-data-hub/
├── .github/
│   ├── workflows/
│   │   ├── deploy-backend.yml      # Existing
│   │   ├── test.yml                # NEW: Test & coverage
│   │   └── pr-summary.yml          # NEW: PR summaries
│   └── PULL_REQUEST_TEMPLATE.md    # NEW: PR template
├── .pre-commit-config.yaml         # NEW
├── backend/
│   ├── pytest.ini                  # Updated
│   ├── pyproject.toml              # NEW: Tool configs
│   ├── scripts/
│   │   ├── run_targeted_tests.py   # NEW
│   │   └── change_summary.py       # NEW
│   └── tests/
│       ├── conftest.py             # Updated: More fixtures
│       ├── unit/                   # NEW
│       │   ├── test_auth.py
│       │   ├── test_cognito.py
│       │   ├── test_boeing_client.py
│       │   ├── test_shopify_client.py
│       │   ├── test_rate_limiter.py
│       │   └── test_boeing_normalize.py
│       ├── integration/            # NEW
│       │   ├── test_boeing_client_integration.py
│       │   ├── test_shopify_client_integration.py
│       │   ├── test_routes_integration.py
│       │   └── test_supabase_integration.py
│       ├── database/               # NEW
│       │   └── test_batch_rpcs.py
│       ├── e2e/                    # NEW
│       │   ├── test_product_search_e2e.py
│       │   └── test_publish_e2e.py
│       └── factories/              # NEW
│           ├── product_factory.py
│           └── batch_factory.py
├── frontend/
│   ├── vitest.config.ts            # NEW
│   ├── src/
│   │   ├── test/                   # NEW
│   │   │   ├── setup.ts
│   │   │   └── mocks/
│   │   │       └── handlers.ts
│   │   ├── components/
│   │   │   └── dashboard/
│   │   │       └── *.test.tsx      # NEW
│   │   ├── services/
│   │   │   └── *.test.ts           # NEW
│   │   ├── hooks/
│   │   │   └── *.test.ts           # NEW
│   │   └── contexts/
│   │       └── *.test.tsx          # NEW
│   └── package.json                # Updated: Test deps
└── QUALITY_PLAN.md                 # This document
```

### 9.2 Required Dependencies Summary

**Backend (`requirements-dev.txt` additions):**
- pytest-xdist - Parallel test execution
- pytest-timeout - Test timeouts
- factory-boy - Test data factories
- httpx[http2] - HTTP/2 for async tests
- bandit - Security linting
- pre-commit - Pre-commit hooks
- commitizen - Commit message validation

**Frontend (`package.json` devDependencies additions):**
- @testing-library/react
- @testing-library/jest-dom
- @testing-library/user-event
- vitest
- @vitest/coverage-v8
- jsdom
- msw

### 9.3 Key Commands Reference

**Pre-commit hooks:**
- `pre-commit install` - Install hooks
- `pre-commit run --all-files` - Run all hooks
- `pre-commit run pytest --files <file>` - Run specific hook

**Backend testing:**
- `pytest` - Run all tests
- `pytest -m unit` - Run unit tests only
- `pytest -m integration` - Run integration tests
- `pytest --cov=app --cov-report=html` - Generate coverage report
- `pytest -x --tb=short` - Stop on first failure

**Frontend testing:**
- `npm run test` - Run all tests
- `npm run test:coverage` - Run with coverage
- `npm run test:watch` - Watch mode
- `npm run test:ui` - Vitest UI

**Change summary:**
- `python scripts/change_summary.py` - Default (vs main)
- `python scripts/change_summary.py --base develop` - Custom base
- `python scripts/change_summary.py --format json` - JSON output

---

**Document Control:**
- Created: February 2026
- Last Updated: February 2026
- Next Review: March 2026
- Approved By: [Pending]
