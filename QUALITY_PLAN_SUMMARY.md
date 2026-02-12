# Boeing Data Hub - Quality Plan Summary

A simple guide to understanding our quality strategy.

---

## Phase 1: Foundation (P0 - Critical)

### What should be implemented?

Pre-commit hooks and a CI/CD test pipeline.

### What is the current situation?

- No pre-commit hooks exist. Developers can push code without any automated checks.
- The CI/CD pipeline only deploys code. It does not run tests before deploying.
- Tests exist but are not enforced. The backend has some tests, but nothing stops broken code from being merged.

### What changes are needed?

1. Install pre-commit hooks that automatically run before every commit. These hooks will check for formatting issues, prevent direct commits to main, detect secrets, format and lint code, run type checking, and run tests related to changed files.

2. Add a test stage to CI/CD that runs before deployment. This stage will run all tests and block deployment if any test fails or if coverage drops below 85%.

### How will this help?

- Catches formatting issues and obvious bugs before code is committed
- Prevents broken code from reaching the main branch
- Reduces time spent on code review catching basic issues
- Creates a safety net that builds confidence when making changes

---

## Phase 2: Test Coverage (P1 - High Priority)

### What should be implemented?

Comprehensive test suites for backend and frontend with 85% minimum coverage.

### What is the current situation?

The backend has 6 test files covering authentication and sync functionality. Most code is untested, including the Shopify client, bulk operations, publishing tasks, and database operations. Estimated current coverage is around 15%.

The frontend has no test framework installed, no tests exist, and coverage is 0%.

### What changes are needed?

For backend testing, add unit tests for all utility functions, authentication logic, and integration tests for Boeing and Shopify API clients with mocked HTTP responses. Also add integration tests for API routes and expand existing sync dispatcher tests.

For frontend testing, install Vitest testing framework, React Testing Library, and MSW for mocking API calls. Then add tests for AuthContext, dashboard components, API service functions, and custom React hooks.

### How will this help?

- Catches bugs early before they affect users
- Makes refactoring safe because tests verify nothing broke
- Documents expected behavior through test cases
- Reduces manual testing time
- Provides confidence when onboarding new developers

---

## Phase 3: Quality Automation (P2 - Medium Priority)

### What should be implemented?

Automated change summaries for pull requests and end-to-end tests.

### What is the current situation?

Pull request descriptions are written manually and often lack detail. Reviewers have to dig through diffs to understand what changed. No automated risk assessment exists and no end-to-end tests verify complete user workflows.

### What changes are needed?

Create a change summary automation script that analyzes git diffs, detects which components are affected, classifies changes by type, assesses risk level, generates testing recommendations, and auto-updates pull request descriptions.

Add end-to-end tests for the complete product search flow, publishing flow, hourly sync cycle, and full authentication flow.

### How will this help?

- Speeds up code review by providing context upfront
- Ensures reviewers know which areas need extra attention
- Catches integration issues that unit tests miss
- Verifies the system works as a whole, not just individual pieces

---

## Coverage Enforcement

### What should be implemented?

Automated enforcement of 85% minimum code coverage.

### What is the current situation?

Coverage tools are installed but not enforced. Developers can merge code with zero tests. No visibility into coverage trends over time.

### What changes are needed?

Configure pytest to fail if backend coverage drops below 85%. Configure Vitest to fail if frontend coverage drops below 85%. Add coverage checks to the CI/CD pipeline. Make coverage reports visible on pull requests.

### How will this help?

- Ensures new code is tested before merging
- Prevents coverage from slowly degrading over time
- Creates accountability for maintaining test quality
- Makes coverage visible to the whole team

---

## Pre-Commit Hook Stages

The pre-commit hooks run in six stages:

**Stage 1** runs quick checks in under 5 seconds. It trims trailing whitespace, fixes end-of-file issues, validates YAML and JSON syntax, checks for large files, checks for merge conflict markers, detects private keys or secrets, and blocks direct commits to main branch.

**Stage 2** handles Python formatting in under 10 seconds. It formats code with Black and lints with Ruff.

**Stage 3** handles frontend checks. It runs ESLint on TypeScript files and runs the TypeScript compiler to check types.

**Stage 4** runs Python type checking in under 30 seconds using MyPy.

**Stage 5** runs targeted tests. It identifies which source files changed, runs only the tests that cover those files, and skips if no relevant tests exist.

**Stage 6** runs security checks using Bandit to detect security issues in Python code.

When a check fails, the commit is blocked and the developer sees which check failed, what the problem is, clear instructions on how to fix it, and commands to run to resolve the issue.

---

## Testing Strategy

### Test Organization

Backend tests are organized into three folders. The unit folder contains fast tests for individual functions with no external dependencies. The integration folder contains tests that use mocked external services. The e2e folder contains full workflow tests.

Frontend tests live next to the code they test. Component tests are named ComponentName.test.tsx, service tests are named serviceName.test.ts, and hook tests are named useHookName.test.ts.

### What Makes a Good Test

A good test tests one thing at a time, has a clear name describing what it checks, follows the arrange-act-assert pattern, does not depend on other tests, runs quickly, and does not require external services.

### What to Test

Always test business logic and calculations, data transformations, error handling, edge cases like empty inputs and null values, and authentication and authorization.

Skip testing third-party library internals, simple getters and setters, and configuration constants.

---

## Implementation Timeline

### Phase 1: Foundation (Weeks 1-2)

Days 1-2 focus on setting up pre-commit hooks and verifying they work. Days 3-4 add the test stage to the CI/CD pipeline. Days 5-7 write backend authentication tests to 100% coverage. Days 8-10 set up the frontend test framework. Days 11-14 write frontend AuthContext tests.

Success means pre-commit hooks run on every commit, CI/CD blocks deployment if tests fail, authentication code has full test coverage, and frontend test infrastructure is ready.

### Phase 2: Test Coverage (Weeks 3-6)

Week 3 covers backend client tests for Boeing and Shopify. Week 4 covers backend services and utilities tests. Week 5 covers frontend component tests. Week 6 covers integration tests and coverage verification.

Success means backend coverage reaches 85%, frontend coverage reaches 85%, and all critical paths have test coverage.

### Phase 3: Quality Automation (Weeks 7-10)

Weeks 7-8 build the end-to-end test suite. Week 9 creates the change summary script and PR automation. Week 10 handles documentation and team training.

Success means E2E tests cover main user workflows, PRs automatically get change summaries, and the team is trained on new processes.

---

## What Gets Tested Where

The Rate Limiter gets unit tests only.

The Boeing Client and Shopify Client get both unit tests and integration tests.

Sync Helpers get unit tests only.

API Routes get integration tests only.

Celery Tasks get both unit tests and integration tests.

The Auth Flow gets unit tests, integration tests, and E2E tests.

Product Search gets integration tests and E2E tests.

Publishing gets integration tests and E2E tests.

Dashboard Components get unit tests only.

API Services get both unit tests and integration tests.

---

## Measuring Success

The backend test coverage is currently around 15% and the target is 85%.

The frontend test coverage is currently 0% and the target is 85%.

The pre-commit hook pass rate has no baseline and the target is 100% first-time pass.

Production bugs from untested code is currently unknown and the target is zero.

Progress should be tracked by running coverage reports weekly, tracking bugs caught by tests versus found in production, monitoring pre-commit hook failure reasons, and reviewing PR cycle times before and after automation.

---

## Getting Started

### For Developers

First install pre-commit by running pip install pre-commit. Then set up hooks by running pre-commit install. Run all hooks once with pre-commit run --all-files. Run backend tests with cd backend && pytest. Run frontend tests with cd frontend && npm test.

### For Reviewers

Check that the PR has tests for new code. Verify coverage did not decrease. Review the auto-generated change summary. Focus review on high-risk components.

---

**Document Owner:** Engineering Team
