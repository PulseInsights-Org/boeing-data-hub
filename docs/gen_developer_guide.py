"""Generate Developer_Guide.docx."""

from doc_helpers import (
    create_doc, add_table, add_code_block, add_bullet, save_doc,
)


def generate():
    doc = create_doc("Developer Guide")

    # ── 1. Prerequisites ─────────────────────────────────────────────
    doc.add_heading("1. Prerequisites", level=1)
    add_table(doc,
        ["Tool", "Version", "Install Link"],
        [
            ["Python", "3.11+", "https://www.python.org/downloads/"],
            ["Node.js", "18+", "https://nodejs.org/"],
            ["npm", "9+", "Bundled with Node.js"],
            ["Redis", "7+", "https://redis.io/download/ or Docker"],
            ["Git", "2.30+", "https://git-scm.com/downloads"],
            ["Docker (optional)", "24+", "https://docs.docker.com/get-docker/"],
        ],
    )

    # ── 2. Local Development Setup ───────────────────────────────────
    doc.add_heading("2. Local Development Setup", level=1)

    doc.add_heading("2.1 First-Time Setup", level=2)

    doc.add_heading("Clone the repository", level=3)
    add_code_block(doc, """git clone https://github.com/PulseInsights-Org/boeing-data-hub.git
cd boeing-data-hub""")

    doc.add_heading("Backend setup", level=3)
    add_code_block(doc, """cd backend
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\\Scripts\\activate          # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt""")

    doc.add_heading("Frontend setup", level=3)
    add_code_block(doc, """cd frontend
npm install""")

    doc.add_heading("Create backend .env", level=3)
    add_code_block(doc, """cp .env.example .env
# Fill in values — see Section 3 for details on each variable""")

    doc.add_heading("Create frontend .env.local", level=3)
    add_code_block(doc, """# frontend/.env.local
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SUPABASE_URL=<placeholder>
VITE_SUPABASE_ANON_KEY=<placeholder>
VITE_AVIATION_GATEWAY_URL=http://localhost:8080""")

    doc.add_heading("Start Redis", level=3)
    add_code_block(doc, """# Via Docker (recommended):
docker run -d --name redis -p 6379:6379 redis:7

# Or install locally:
# Ubuntu: sudo apt install redis-server
# Mac:    brew install redis""")

    doc.add_heading("2.2 Daily Development Workflow", level=2)
    doc.add_paragraph("If you already completed first-time setup, use this shorter workflow:")
    add_code_block(doc, """# Activate backend venv
cd backend && source venv/bin/activate

# Terminal 1: Start API server
uvicorn app.main:app --reload --port 8000

# Terminal 2: Start all Celery workers (simplified single-worker mode)
celery -A app.celery_app worker --pool=solo \\
  -Q extraction,normalization,publishing,default,sync_boeing,sync_shopify -l info

# Terminal 3: Start Celery Beat scheduler
celery -A app.celery_app beat -l info

# Terminal 4: Start frontend dev server
cd frontend && npm run dev""")

    doc.add_paragraph(
        "The frontend is accessible at http://localhost:8080 and proxies API calls "
        "to the backend at http://127.0.0.1:8000."
    )

    # ── 3. Environment Configuration ─────────────────────────────────
    doc.add_heading("3. Environment Configuration", level=1)

    doc.add_heading("3.1 Application", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["AUTO_START_CELERY", "No", "false", "If true, FastAPI lifespan auto-starts Celery worker and beat subprocesses"],
        ],
    )

    doc.add_heading("3.2 Authentication (AWS Cognito)", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["COGNITO_REGION", "Yes", "us-east-1", "AWS region of the Cognito user pool"],
            ["COGNITO_USER_POOL_ID", "Yes", "—", "Cognito user pool identifier"],
            ["COGNITO_APP_CLIENT_ID", "Yes", "—", "Cognito app client ID for token validation"],
        ],
    )

    doc.add_heading("3.3 Database (Supabase)", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["SUPABASE_URL", "Yes", "—", "Supabase project URL"],
            ["SUPABASE_SERVICE_ROLE_KEY", "Yes", "—", "Service role key for admin-level database access"],
            ["SUPABASE_STORAGE_BUCKET", "No", "product-images", "Storage bucket name for product images"],
        ],
    )

    doc.add_heading("3.4 Shopify", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["SHOPIFY_STORE_DOMAIN", "Yes", "—", "Shopify store domain (e.g., store.myshopify.com)"],
            ["SHOPIFY_ADMIN_API_TOKEN", "Yes", "—", "Shopify Admin API access token"],
            ["SHOPIFY_API_VERSION", "No", "2025-01", "Shopify API version string"],
            ["SHOPIFY_LOCATION_MAP", "No", "{}", "JSON map of location names to IDs"],
            ["SHOPIFY_INVENTORY_LOCATION_CODES", "No", "{}", "JSON map of locations to inventory codes"],
        ],
    )

    doc.add_heading("3.5 Boeing API", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["BOEING_CLIENT_ID", "Yes", "—", "Boeing OAuth2 client identifier"],
            ["BOEING_CLIENT_SECRET", "Yes", "—", "Boeing OAuth2 client secret"],
            ["BOEING_USERNAME", "Yes", "—", "Boeing PNA API username"],
            ["BOEING_PASSWORD", "Yes", "—", "Boeing PNA API password"],
            ["BOEING_OAUTH_TOKEN_URL", "Yes", "—", "Boeing OAuth2 token endpoint"],
            ["BOEING_PNA_OAUTH_URL", "Yes", "—", "Boeing PNA secondary auth endpoint"],
            ["BOEING_PNA_PRICE_URL", "Yes", "—", "Boeing PNA price/availability endpoint"],
        ],
    )

    doc.add_heading("3.6 Redis & Task Queue", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["REDIS_URL", "Yes", "redis://localhost:6379/0", "Redis connection URL for Celery broker and rate limiter"],
        ],
    )

    doc.add_heading("3.7 Rate Limiting", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["BOEING_RATE_LIMIT_CAPACITY", "No", "2", "Token bucket capacity for Boeing API calls"],
            ["SHOPIFY_API_RATE_LIMIT", "No", "30", "Max Shopify API calls per minute"],
        ],
    )

    doc.add_heading("3.8 Sync Scheduler", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["SYNC_MODE", "No", "testing", "'testing' (minute buckets) or 'production' (hourly buckets)"],
            ["SYNC_ENABLED", "No", "true", "Master switch to enable/disable sync system"],
            ["SYNC_FREQUENCY", "No", "weekly", "'daily' or 'weekly'"],
            ["SYNC_TEST_BUCKET_COUNT", "No", "6", "Number of buckets in testing mode"],
            ["SYNC_DISPATCH_MINUTE", "No", "*/10", "Cron minute expression for dispatch"],
            ["SYNC_RETRY_HOURS", "No", "4", "Retry failed syncs every N hours"],
            ["SYNC_MAX_FAILURES", "No", "5", "Deactivate product after N consecutive failures"],
            ["SYNC_BATCH_SIZE", "No", "10", "Max SKUs per Boeing API batch"],
        ],
    )

    doc.add_heading("3.9 AI Reports & Email", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description"],
        [
            ["GEMINI_API_KEY", "No", "—", "Google Gemini API key for AI report generation"],
            ["RESEND_API_KEY", "No", "—", "Resend API key for email delivery"],
            ["RESEND_FROM_ADDRESS", "No", "—", "Sender email address for reports"],
            ["REPORT_RECIPIENTS", "No", "[]", "JSON array of report recipient emails"],
        ],
    )

    doc.add_paragraph(
        "Missing required variables: The application will fail to start with a Pydantic "
        "validation error listing which required settings are absent."
    )

    # ── 4. Build Process ─────────────────────────────────────────────
    doc.add_heading("4. Build Process", level=1)

    doc.add_heading("4.1 Backend (Python)", level=2)
    doc.add_paragraph(
        "The backend does not require a build step. FastAPI runs directly from source using "
        "Uvicorn. In development, use --reload for hot reloading:"
    )
    add_code_block(doc, "uvicorn app.main:app --reload --port 8000")
    doc.add_paragraph("In production, Uvicorn runs as a systemd service without --reload.")

    doc.add_heading("4.2 Frontend (Vite + React)", level=2)
    doc.add_paragraph("Development (hot reload):")
    add_code_block(doc, "cd frontend && npm run dev")
    doc.add_paragraph("Production build:")
    add_code_block(doc, """cd frontend && npm run build
# Output: frontend/dist/
# Contains: index.html + assets/ (bundled JS, CSS)""")
    doc.add_paragraph(
        "Vite uses SWC (via @vitejs/plugin-react-swc) for fast transpilation. "
        "TypeScript is compiled with strict mode enabled."
    )

    # ── 5. Running Tests ─────────────────────────────────────────────
    doc.add_heading("5. Running Tests", level=1)

    doc.add_heading("5.1 Test Framework", level=2)
    add_table(doc,
        ["Framework", "Purpose"],
        [
            ["pytest", "Test runner and assertions"],
            ["pytest-asyncio", "Async test support for FastAPI"],
            ["pytest-cov", "Code coverage reporting"],
            ["pytest-mock", "Mock objects and patching"],
            ["respx", "HTTP request mocking (for httpx)"],
            ["fakeredis", "In-memory Redis mock for Celery/rate limiter tests"],
        ],
    )

    doc.add_heading("5.2 Running Tests", level=2)
    add_code_block(doc, """# All tests
cd backend && pytest tests/ -v

# Unit tests only (fast, no external dependencies)
pytest tests/unit/ -v

# Integration tests (route handlers, task chains)
pytest tests/integration/ -v

# End-to-end tests (full pipeline)
pytest tests/e2e/ -v

# With coverage
pytest tests/ --cov=app --cov-report=html

# Run a specific test file
pytest tests/unit/test_boeing_normalize.py -v""")

    doc.add_heading("5.3 Test Structure", level=2)
    doc.add_paragraph(
        "Unit tests (26 files): Test individual components in isolation with mocked dependencies. "
        "These are fast (<1s each) and do not make real API or database calls."
    )
    doc.add_paragraph(
        "Integration tests (10 files): Test route handlers with mocked services and Celery task "
        "chains. Verify request/response contracts."
    )
    doc.add_paragraph(
        "End-to-end tests (3 files): Test complete pipelines — extraction, publishing, and sync. "
        "Use comprehensive mocks for all external services."
    )

    # ── 6. Debugging ─────────────────────────────────────────────────
    doc.add_heading("6. Debugging", level=1)

    doc.add_heading("6.1 Backend Debugging", level=2)
    doc.add_paragraph("VS Code launch configuration for FastAPI:")
    add_code_block(doc, """{
  "name": "FastAPI Debug",
  "type": "debugpy",
  "request": "launch",
  "module": "uvicorn",
  "args": ["app.main:app", "--reload", "--port", "8000"],
  "cwd": "${workspaceFolder}/backend"
}""")
    doc.add_paragraph("Celery worker debugging (attach debugger to worker process):")
    add_code_block(doc, "celery -A app.celery_app worker --pool=solo -Q default -l debug")

    doc.add_heading("6.2 Frontend Debugging", level=2)
    doc.add_paragraph(
        "Use browser DevTools (F12). The React Developer Tools extension is recommended. "
        "React Query Devtools are available in development mode for inspecting cache state."
    )

    doc.add_heading("6.3 Common Errors and Fixes", level=2)
    add_table(doc,
        ["Error", "Cause", "Fix"],
        [
            ["ConnectionRefusedError: Redis", "Redis not running", "Start Redis: docker run -d -p 6379:6379 redis:7"],
            ["401 Unauthorized on API calls", "JWT token expired or invalid", "Log out and log back in via Aviation Gateway"],
            ["Pydantic ValidationError on startup", "Missing required env vars", "Check .env file against Section 3 above"],
            ["Boeing API 403 Forbidden", "Invalid Boeing credentials", "Verify BOEING_CLIENT_ID, BOEING_CLIENT_SECRET"],
            ["Shopify 429 Too Many Requests", "Rate limit exceeded", "Rate limiter should prevent this; check SHOPIFY_API_RATE_LIMIT"],
            ["Celery worker not consuming tasks", "Queue mismatch", "Ensure worker subscribes to correct queues (-Q flag)"],
            ["CORS error in browser", "Backend URL mismatch", "Check VITE_API_BASE_URL matches backend port"],
            ["Supabase permission denied", "Invalid service role key", "Verify SUPABASE_SERVICE_ROLE_KEY"],
        ],
    )

    # ── 7. CI/CD Pipeline ────────────────────────────────────────────
    doc.add_heading("7. CI/CD Pipeline", level=1)
    doc.add_paragraph(
        "The project uses GitHub Actions for continuous deployment. The pipeline configuration "
        "lives at .github/workflows/deploy-backend.yml."
    )

    doc.add_heading("7.1 Trigger", level=2)
    doc.add_paragraph(
        "The pipeline triggers on push to the main branch when files in backend/ are changed, "
        "or via manual workflow_dispatch."
    )

    doc.add_heading("7.2 Pipeline Stages", level=2)
    add_table(doc,
        ["Stage", "Action", "Details"],
        [
            ["Checkout", "actions/checkout@v4", "Clones the repository"],
            ["SSH Setup", "Install EC2 SSH key", "Key from GitHub secret: EC2_SSH_KEY"],
            ["Deploy", "rsync to EC2", "Syncs backend/ to /home/ubuntu/boeing-data-hub/backend/ (excludes .env, venv, tests, __pycache__)"],
            ["Install", "pip install on EC2", "Installs/updates dependencies inside virtualenv"],
            ["Restart", "redeploy.sh", "Stops all services, reloads systemd, restarts in sequence with health check"],
            ["Verify", "Service status check", "Confirms all 6 services (backend + 5 Celery) are running"],
            ["Cleanup", "Remove SSH key", "Removes temporary SSH key file"],
        ],
    )

    doc.add_heading("7.3 GitHub Secrets Required", level=2)
    add_table(doc,
        ["Secret", "Purpose"],
        [
            ["EC2_SSH_KEY", "Private SSH key for connecting to the EC2 deployment server"],
        ],
    )

    # ── 8. Deployment Workflow ───────────────────────────────────────
    doc.add_heading("8. Deployment Workflow", level=1)

    doc.add_heading("8.1 Backend (Automatic)", level=2)
    doc.add_paragraph(
        "Push to main branch with backend/ changes triggers automatic deployment (see Section 7). "
        "No manual intervention required."
    )

    doc.add_heading("8.2 Frontend (Manual)", level=2)
    add_code_block(doc, """cd frontend
npm run build
# Deploy dist/ to the web server:
scp -r dist/* ubuntu@<ec2-ip>:/var/www/boeing-frontend/""")

    doc.add_heading("8.3 Verifying a Deployment", level=2)
    add_code_block(doc, """# Backend health check
curl https://api.boeing-data-hub.skynetparts.com/health
# Expected: {"status": "healthy"}

# Check all services on EC2
ssh ubuntu@<ec2-ip> 'systemctl status boeing-backend boeing-celery-extract boeing-celery-publish boeing-celery-sync boeing-celery-default boeing-celery-beat'""")

    # ── 9. Troubleshooting ───────────────────────────────────────────
    doc.add_heading("9. Common Issues & Troubleshooting", level=1)
    add_table(doc,
        ["Problem", "Symptoms", "Solution"],
        [
            ["Backend won't start", "ModuleNotFoundError", "Activate venv: source venv/bin/activate, then pip install -r requirements.txt"],
            ["Frontend shows blank page", "Console errors about VITE_API_BASE_URL", "Ensure frontend/.env.local has correct VITE_API_BASE_URL"],
            ["Celery tasks stuck in queue", "Tasks pending but not executing", "Verify Redis is running and worker is connected to correct queues"],
            ["Database connection timeout", "Supabase error on startup", "Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env"],
            ["Sync not running", "No sync history entries", "Check SYNC_ENABLED=true and Celery Beat is running"],
            ["Deployment fails", "GitHub Actions red", "Check EC2_SSH_KEY secret, EC2 instance status, and disk space"],
            ["Rate limiter errors", "Redis errors in logs", "Verify REDIS_URL; check Redis memory with redis-cli INFO memory"],
            ["Tests fail with import errors", "ModuleNotFoundError in tests", "Install dev deps: pip install -r requirements-dev.txt"],
        ],
    )

    return save_doc(doc, "Developer_Guide.docx")
