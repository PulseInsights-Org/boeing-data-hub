"""Generate Onboarding_Guide.docx."""

from doc_helpers import (
    create_doc, add_table, add_code_block, add_bullet,
    add_bold_paragraph, add_numbered_steps, add_tip, save_doc,
)


def generate():
    doc = create_doc("Onboarding Guide")

    # ── 1. Welcome ───────────────────────────────────────────────────
    doc.add_heading("1. Welcome & Context", level=1)
    doc.add_paragraph(
        "Welcome to the Boeing Data Hub team! This project powers the product data pipeline "
        "that keeps our Shopify store stocked with Boeing aviation parts at the right prices "
        "and inventory levels. It is used daily by operations staff to search for parts, "
        "publish them to Shopify, and monitor automated price/inventory synchronization."
    )
    doc.add_paragraph(
        "Your work on this project directly impacts our ability to serve customers with "
        "accurate, up-to-date product information. This guide will get you from zero to "
        "contributing within your first week."
    )

    # ── 2. What You'll Need ──────────────────────────────────────────
    doc.add_heading("2. What You'll Need", level=1)
    doc.add_paragraph("Request access to the following before your first day:")

    doc.add_heading("2.1 Accounts & Access", level=2)
    add_table(doc,
        ["Item", "Where to Request", "Why You Need It"],
        [
            ["GitHub repo access", "Team lead or GitHub org admin", "Clone the repo, push branches, create PRs"],
            ["AWS Console access", "DevOps or team lead", "View EC2, Cognito, and deployment logs"],
            ["Supabase dashboard", "Team lead", "View database tables, run queries, check logs"],
            ["Shopify partner/staff access", "Shopify admin", "View published products and test publish flow"],
            ["Aviation Gateway SSO account", "IT / SSO admin", "Log into the application for testing"],
            ["Slack workspace", "Team lead", "[TODO: Add Slack workspace invite link]"],
        ],
    )

    doc.add_heading("2.2 Development Tools", level=2)
    add_table(doc,
        ["Tool", "Version", "Notes"],
        [
            ["Python", "3.11+", "Backend runtime"],
            ["Node.js", "18+", "Frontend runtime (npm included)"],
            ["Redis", "7+", "Install locally or use Docker"],
            ["Git", "2.30+", "Version control"],
            ["VS Code (recommended)", "Latest", "Extensions: Python, ESLint, Tailwind CSS IntelliSense"],
            ["Docker (optional)", "24+", "For running Redis easily"],
        ],
    )

    # ── 3. First-Time Setup ──────────────────────────────────────────
    doc.add_heading("3. First-Time Setup", level=1)
    doc.add_paragraph(
        "Take your time with this section. It is normal for first-time setup to take "
        "30-60 minutes. Ask for help if you get stuck at any step."
    )

    doc.add_heading("Step 1: Clone the repository", level=2)
    add_code_block(doc, """git clone https://github.com/PulseInsights-Org/boeing-data-hub.git
cd boeing-data-hub""")
    doc.add_paragraph(
        "This downloads the full project. You should see two main folders: backend/ and frontend/."
    )

    doc.add_heading("Step 2: Set up the backend", level=2)
    add_code_block(doc, """cd backend
python -m venv venv""")
    doc.add_paragraph(
        "This creates a Python virtual environment. A virtual environment keeps this project's "
        "dependencies separate from other Python projects on your machine."
    )
    add_code_block(doc, """# Activate the virtual environment:
source venv/bin/activate          # Linux/Mac
# venv\\Scripts\\activate          # Windows

# Install dependencies:
pip install -r requirements.txt
pip install -r requirements-dev.txt""")
    doc.add_paragraph(
        "If pip install fails, check that you have Python 3.11+ installed: python --version"
    )

    doc.add_heading("Step 3: Configure the backend environment", level=2)
    doc.add_paragraph(
        "The backend needs a .env file with connection details for Supabase, Boeing, "
        "Shopify, Cognito, and Redis. Ask your team lead for a copy of the development "
        ".env file."
    )
    add_code_block(doc, """# Copy the example and fill in values:
cp .env.example .env
# Ask a teammate for the actual values""")
    add_tip(doc, "Never commit .env files to Git. They contain secrets.")

    doc.add_heading("Step 4: Start Redis", level=2)
    doc.add_paragraph(
        "Redis is our message broker for background tasks. The easiest way to run it is Docker:"
    )
    add_code_block(doc, "docker run -d --name redis -p 6379:6379 redis:7")
    doc.add_paragraph("If you do not have Docker, install Redis directly on your system.")

    doc.add_heading("Step 5: Start the backend", level=2)
    add_code_block(doc, """# In one terminal (API server):
uvicorn app.main:app --reload --port 8000

# In another terminal (Celery workers — all queues):
celery -A app.celery_app worker --pool=solo \\
  -Q extraction,normalization,publishing,default,sync_boeing,sync_shopify -l info

# In a third terminal (scheduler):
celery -A app.celery_app beat -l info""")
    doc.add_paragraph("Check the backend is running:")
    add_code_block(doc, """curl http://localhost:8000/health
# Should return: {"status": "healthy"}""")

    doc.add_heading("Step 6: Set up the frontend", level=2)
    add_code_block(doc, """cd ../frontend
npm install""")
    doc.add_paragraph("Create the frontend environment file:")
    add_code_block(doc, """# Create frontend/.env.local with:
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SUPABASE_URL=<ask your team lead>
VITE_SUPABASE_ANON_KEY=<ask your team lead>
VITE_AVIATION_GATEWAY_URL=http://localhost:8080""")
    add_code_block(doc, "npm run dev")
    doc.add_paragraph(
        "Open http://localhost:8080 in your browser. You should see the login page "
        "(or be redirected to Aviation Gateway)."
    )

    doc.add_heading("Step 7: Run the tests", level=2)
    add_code_block(doc, """cd ../backend
pytest tests/unit/ -v""")
    doc.add_paragraph(
        "All unit tests should pass. If any fail, check your Python version and installed packages."
    )

    # ── 4. Repository Structure Tour ─────────────────────────────────
    doc.add_heading("4. Repository Structure Tour", level=1)
    doc.add_paragraph(
        "Here is a guided walk through the project. Understanding this structure will help "
        "you find code quickly."
    )

    add_bold_paragraph(doc, "backend/app/routes/ — API Endpoints")
    doc.add_paragraph(
        "This is where HTTP request handlers live. If you need to add or modify an API "
        "endpoint, start here. Each file maps to a group of related endpoints "
        "(e.g., extraction.py handles /extraction/*, sync.py handles /sync/*)."
    )

    add_bold_paragraph(doc, "backend/app/services/ — Business Logic")
    doc.add_paragraph(
        "Services contain the actual logic — how products get extracted, normalized, "
        "published, and synced. Routes call services; services call stores and clients. "
        "This is where you will spend most of your time for feature work."
    )

    add_bold_paragraph(doc, "backend/app/db/ — Database Operations")
    doc.add_paragraph(
        "Each database table has a 'store' class here (e.g., batch_store.py for the batches "
        "table). Stores handle all SQL queries through the Supabase SDK. If you need to "
        "change how data is read or written, look here."
    )

    add_bold_paragraph(doc, "backend/app/clients/ — External API Wrappers")
    doc.add_paragraph(
        "These modules wrap external APIs (Boeing, Shopify, Supabase, Gemini, Resend). "
        "Each client handles its own authentication and error handling. You would touch "
        "these when API contracts change or new integrations are added."
    )

    add_bold_paragraph(doc, "backend/app/celery_app/tasks/ — Background Tasks")
    doc.add_paragraph(
        "Celery tasks for async processing. Each task file corresponds to a domain: "
        "extraction, normalization, publishing, batch management, and sync. Tasks are the "
        "units of work that Celery workers consume from Redis queues."
    )

    add_bold_paragraph(doc, "backend/app/utils/ — Utilities")
    doc.add_paragraph(
        "Shared helpers: Boeing data normalization, Shopify payload building, rate limiting, "
        "hashing, slot management. Small, focused modules that services and tasks reuse."
    )

    add_bold_paragraph(doc, "frontend/src/components/dashboard/ — UI Panels")
    doc.add_paragraph(
        "The main dashboard components: SearchPanel (extraction + publishing), "
        "PublishedProductsPanel, and AutoSyncPanel. These are the primary UI pieces."
    )

    add_bold_paragraph(doc, "frontend/src/services/ — API Calls")
    doc.add_paragraph(
        "Frontend service modules that call the backend API. If you need to connect the "
        "frontend to a new endpoint, add or modify a service file here."
    )

    add_bold_paragraph(doc, "backend/tests/ — Test Suite")
    doc.add_paragraph(
        "Unit tests (tests/unit/), integration tests (tests/integration/), and end-to-end "
        "tests (tests/e2e/). Always run tests before pushing."
    )

    # ── 5. Coding Conventions ────────────────────────────────────────
    doc.add_heading("5. Coding Conventions", level=1)

    doc.add_heading("5.1 Python (Backend)", level=2)
    add_table(doc,
        ["Convention", "Details"],
        [
            ["Formatter", "Black (line length: 88)"],
            ["Linter", "Ruff"],
            ["Type Checker", "mypy"],
            ["Naming", "snake_case for functions/variables, PascalCase for classes"],
            ["Imports", "Group: stdlib, third-party, local. Sorted alphabetically"],
            ["Async", "Use async/await for I/O operations. Never block the event loop"],
            ["Docstrings", "Not required for every function, but add them for complex logic"],
        ],
    )

    doc.add_heading("5.2 TypeScript (Frontend)", level=2)
    add_table(doc,
        ["Convention", "Details"],
        [
            ["Linter", "ESLint with TypeScript plugin"],
            ["Naming", "camelCase for variables/functions, PascalCase for components/types"],
            ["Components", "Functional components with hooks (no class components)"],
            ["Styling", "Tailwind CSS utility classes (no CSS modules or styled-components)"],
            ["State", "React Query for server state; useState/useReducer for local state"],
            ["Types", "Define all interfaces in src/types/product.ts"],
        ],
    )

    doc.add_heading("5.3 General", level=2)
    add_bullet(doc, "Commit messages: Use imperative mood (e.g., 'Add sync dashboard endpoint')")
    add_bullet(doc, "Branch naming: feature/<description>, fix/<description>, refactor/<description>")
    add_bullet(doc, "Keep PRs focused on one change. Avoid mixing features and refactors.")

    # ── 6. How to Make Your First PR ─────────────────────────────────
    doc.add_heading("6. How to Make Your First PR", level=1)
    doc.add_paragraph(
        "This section walks you through making your first pull request. A good first PR might "
        "be a small bug fix, adding a test, or improving a log message."
    )

    add_numbered_steps(doc, [
        "Create a new branch from main: git checkout -b feature/my-first-change",
        "Make your changes in the relevant files.",
        "Run the linter and formatter (backend): cd backend && ruff check . && black .",
        "Run the tests: pytest tests/unit/ -v — make sure all pass.",
        "Stage your changes: git add <files>",
        "Commit with a clear message: git commit -m \"feat: add helpful description here\"",
        "Push to remote: git push -u origin feature/my-first-change",
        "Open a pull request on GitHub. In the description, explain what you changed and why.",
        "Request a review from your team lead or a senior developer.",
        "Address any review comments, push updates, and wait for approval.",
        "Once approved, your reviewer (or you, if you have merge permissions) will merge the PR.",
    ])

    add_tip(doc, "Your first PR does not need to be big. Even fixing a typo in a log message counts!")

    doc.add_heading("Commit Message Conventions", level=2)
    add_table(doc,
        ["Prefix", "Use When"],
        [
            ["feat:", "Adding new functionality"],
            ["fix:", "Fixing a bug"],
            ["refactor:", "Code restructuring without behavior change"],
            ["test:", "Adding or updating tests"],
            ["docs:", "Documentation changes"],
            ["chore:", "Build, CI, dependency updates"],
        ],
    )

    # ── 7. Key Abstractions ──────────────────────────────────────────
    doc.add_heading("7. Key Abstractions to Understand", level=1)

    doc.add_heading("7.1 Dependency Injection Container (container.py)", level=2)
    doc.add_paragraph(
        "The container provides singleton instances of all clients, stores, and services "
        "via lru_cache. When you need a service or store in a route or task, import the "
        "getter function (e.g., get_extraction_service()). This keeps dependencies loosely "
        "coupled and easy to mock in tests."
    )

    doc.add_heading("7.2 Store Pattern (db/*.py)", level=2)
    doc.add_paragraph(
        "Each database table has a corresponding store class (e.g., BatchStore, ProductStore). "
        "Stores encapsulate all database queries and provide methods like get_by_id, upsert, "
        "update_status. Routes and services never write raw SQL — they call store methods. "
        "This makes it easy to swap the database or add caching later."
    )

    doc.add_heading("7.3 Service Layer (services/*.py)", level=2)
    doc.add_paragraph(
        "Services orchestrate business logic across multiple stores and clients. For example, "
        "PublishingService reads from staging_store, calls shopify_orchestrator, writes to "
        "product_store, and creates a sync_schedule entry. This keeps route handlers thin and "
        "business logic testable."
    )

    doc.add_heading("7.4 Celery Tasks (celery_app/tasks/*.py)", level=2)
    doc.add_paragraph(
        "Tasks are the async work units. They inherit from BaseTask, which provides access "
        "to the DI container. Tasks are enqueued onto specific queues (extraction, publishing, "
        "sync_boeing, etc.) and consumed by dedicated workers. Understanding the task chain "
        "(process_bulk_search -> extract_chunk -> normalize_chunk -> check_batch_completion) "
        "is essential for debugging bulk operations."
    )

    doc.add_heading("7.5 Rate Limiter (utils/rate_limiter.py)", level=2)
    doc.add_paragraph(
        "A Redis-backed token bucket that controls how fast we call external APIs. Boeing's "
        "limit is 2 requests/minute. The limiter is shared across all Celery workers via "
        "Redis, ensuring global rate compliance. Before any Boeing API call, the code calls "
        "rate_limiter.acquire() which blocks until a token is available."
    )

    # ── 8. Who to Ask ────────────────────────────────────────────────
    doc.add_heading("8. Who to Ask for Help", level=1)
    add_table(doc,
        ["Topic", "Contact"],
        [
            ["General questions", "[TODO: Team lead name and Slack handle]"],
            ["Backend architecture", "[TODO: Senior backend dev]"],
            ["Frontend/UI", "[TODO: Frontend dev]"],
            ["DevOps/deployment", "[TODO: DevOps engineer]"],
            ["Boeing API issues", "[TODO: Boeing integration contact]"],
            ["Shopify issues", "[TODO: Shopify admin contact]"],
        ],
    )

    doc.add_paragraph("Helpful resources:")
    add_bullet(doc, "Project README: backend/readme.md")
    add_bullet(doc, "Slack channel: [TODO: Add channel name]")
    add_bullet(doc, "Issue tracker: [TODO: Add URL]")
    add_bullet(doc, "This documentation: docs/ folder")

    doc.add_paragraph(
        "Do not hesitate to ask questions. Everyone on the team was new once, and we would "
        "rather answer a question than debug a problem caused by guessing."
    )

    return save_doc(doc, "Onboarding_Guide.docx")
