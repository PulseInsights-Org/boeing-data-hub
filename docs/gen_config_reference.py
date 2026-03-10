"""Generate Configuration_Reference.docx."""

from doc_helpers import (
    create_doc, add_table, add_code_block, add_bullet,
    add_numbered_steps, save_doc,
)


def generate():
    doc = create_doc("Configuration & Environment Reference")

    # ── 1. Overview ──────────────────────────────────────────────────
    doc.add_heading("1. Overview", level=1)
    doc.add_paragraph(
        "Boeing Data Hub uses environment variables as its primary configuration mechanism. "
        "The backend loads variables from a .env file via python-dotenv, validated by a "
        "Pydantic Settings class (backend/app/core/config.py). The frontend uses Vite's "
        "VITE_* prefix convention (frontend/.env.local)."
    )
    doc.add_paragraph(
        "Configuration files (Vite config, Tailwind config, ESLint, TypeScript) control "
        "build tooling and code quality. Deployment is configured via GitHub Actions "
        "workflow files."
    )

    # ── 2. Environment Variables ─────────────────────────────────────
    doc.add_heading("2. Environment Variables", level=1)

    doc.add_heading("2.1 Application", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["AUTO_START_CELERY", "No", "false", "If true, FastAPI auto-starts Celery worker and beat subprocesses on boot. Set to false when running workers manually in development.", "true"],
        ],
    )

    doc.add_heading("2.2 Authentication (AWS Cognito)", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["COGNITO_REGION", "Yes", "us-east-1", "AWS region where the Cognito user pool is hosted.", "us-east-1"],
            ["COGNITO_USER_POOL_ID", "Yes", "—", "The user pool ID. Used to construct the JWKS URL for token verification.", "us-east-1_AbCdEfGhI"],
            ["COGNITO_APP_CLIENT_ID", "Yes", "—", "App client ID. The JWT aud claim must match this value.", "1a2b3c4d5e6f7g8h9i0j"],
        ],
    )
    doc.add_paragraph(
        "If any Cognito variable is missing, all authenticated API requests will fail with 401 Unauthorized."
    )

    doc.add_heading("2.3 Database (Supabase)", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["SUPABASE_URL", "Yes", "—", "Supabase project URL. Used for all database operations and storage.", "https://abcdefgh.supabase.co"],
            ["SUPABASE_SERVICE_ROLE_KEY", "Yes", "—", "Service role key with admin-level access. Bypasses Row Level Security. NEVER expose publicly.", "eyJhbGciOiJIUzI1NiIs..."],
            ["SUPABASE_STORAGE_BUCKET", "No", "product-images", "Name of the Supabase Storage bucket for product images.", "product-images"],
        ],
    )
    doc.add_paragraph(
        "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY causes the application to crash on startup."
    )

    doc.add_heading("2.4 Shopify", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["SHOPIFY_STORE_DOMAIN", "Yes", "—", "Your Shopify store domain.", "my-store.myshopify.com"],
            ["SHOPIFY_ADMIN_API_TOKEN", "Yes", "—", "Admin API access token. Required scopes: write_products, write_inventory.", "shpat_abc123def456..."],
            ["SHOPIFY_API_VERSION", "No", "2025-01", "Shopify API version string. Update when migrating to newer API versions.", "2025-01"],
            ["SHOPIFY_LOCATION_MAP", "No", "{}", "JSON mapping of human-readable location names to Shopify location IDs.", '{"Dallas Central": "123456"}'],
            ["SHOPIFY_INVENTORY_LOCATION_CODES", "No", "{}", "JSON mapping of locations to inventory tracking codes.", '{"DAL": "123456"}'],
        ],
    )

    doc.add_heading("2.5 Boeing API", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["BOEING_CLIENT_ID", "Yes", "—", "OAuth2 client ID for Boeing's developer API portal.", "client-id-value"],
            ["BOEING_CLIENT_SECRET", "Yes", "—", "OAuth2 client secret. Used in the token exchange flow.", "client-secret-value"],
            ["BOEING_USERNAME", "Yes", "—", "Username for Boeing's PNA (Part Number Availability) API.", "pna-username"],
            ["BOEING_PASSWORD", "Yes", "—", "Password for Boeing's PNA API.", "pna-password"],
            ["BOEING_SCOPE", "No", "api://helixapis.com/.default", "OAuth2 scope for Boeing API token requests.", "api://helixapis.com/.default"],
            ["BOEING_OAUTH_TOKEN_URL", "Yes", "—", "Primary OAuth2 token endpoint.", "https://api.developer.boeingservices.com/oauth2/v2.0/token"],
            ["BOEING_PNA_OAUTH_URL", "Yes", "—", "Secondary PNA-specific authentication endpoint.", "https://api.developer.boeingservices.com/.../token/v1/oauth"],
            ["BOEING_PNA_PRICE_URL", "Yes", "—", "PNA price and availability data endpoint.", "https://api.developer.boeingservices.com/.../price-availability/v1/wtoken"],
        ],
    )
    doc.add_paragraph(
        "Missing Boeing credentials cause extraction and sync operations to fail with ExternalAPIError. "
        "The application itself will still start, but all Boeing-related operations will error."
    )

    doc.add_heading("2.6 Redis & Task Queue", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["REDIS_URL", "Yes", "redis://localhost:6379/0", "Redis connection URL. Used as Celery broker, rate limiter backend, and distributed lock store.", "redis://localhost:6379/0"],
        ],
    )
    doc.add_paragraph(
        "If Redis is unreachable, Celery workers cannot start, rate limiting will not function, "
        "and all async operations (extraction, publishing, sync) will be unavailable."
    )

    doc.add_heading("2.7 Batch Processing Limits", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["BOEING_BATCH_SIZE", "No", "10", "Number of part numbers per Boeing API call. Boeing's API supports up to 10.", "10"],
            ["MAX_BULK_SEARCH_SIZE", "No", "50000", "Maximum part numbers in a single bulk search request.", "50000"],
            ["MAX_BULK_PUBLISH_SIZE", "No", "10000", "Maximum products in a single bulk publish request.", "10000"],
        ],
    )

    doc.add_heading("2.8 Rate Limiting", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["BOEING_RATE_LIMIT_CAPACITY", "No", "2", "Token bucket capacity. Allows burst of this many requests.", "2"],
            ["BOEING_RATE_LIMIT_REFILL", "No", "2", "Tokens refilled per minute.", "2"],
            ["SHOPIFY_API_RATE_LIMIT", "No", "30", "Maximum Shopify API requests per minute (Celery task annotation).", "30"],
        ],
    )

    doc.add_heading("2.9 Sync Scheduler", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["SYNC_MODE", "No", "testing", "'testing': uses minute-based buckets (fast cycles). 'production': uses hourly buckets (24h cycle).", "production"],
            ["SYNC_ENABLED", "No", "true", "Master switch. Set to false to completely disable sync.", "true"],
            ["SYNC_FREQUENCY", "No", "weekly", "'daily': sync every day. 'weekly': sync once per week.", "weekly"],
            ["SYNC_WEEKLY_DAY", "No", "Sunday", "Day of week for weekly sync (only used when SYNC_FREQUENCY=weekly).", "Sunday"],
            ["SYNC_TEST_BUCKET_COUNT", "No", "6", "Number of buckets in testing mode (maps to minute intervals).", "6"],
            ["SYNC_DISPATCH_MINUTE", "No", "*/10", "Cron minute expression for dispatch schedule.", "45"],
            ["SYNC_RETRY_HOURS", "No", "4", "How often (in hours) to retry failed sync products.", "4"],
            ["SYNC_CLEANUP_HOUR", "No", "0", "Hour (UTC) for daily end-of-day cleanup of stuck syncs.", "0"],
            ["SYNC_MAX_FAILURES", "No", "5", "Product is deactivated after this many consecutive sync failures.", "5"],
            ["SYNC_BATCH_SIZE", "No", "10", "Number of SKUs per Boeing API call during sync.", "10"],
        ],
    )

    doc.add_heading("2.10 AI Reports & Email", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["GEMINI_API_KEY", "No", "—", "Google Generative AI API key for Gemini model.", "AIza..."],
            ["GEMINI_MODEL", "No", "gemini-2.0-flash", "Gemini model ID for report generation.", "gemini-2.0-flash"],
            ["RESEND_API_KEY", "No", "—", "Resend API key for email delivery.", "re_..."],
            ["RESEND_FROM_ADDRESS", "No", "—", "Sender email address for reports.", "reports@skynetparts.com"],
            ["REPORT_RECIPIENTS", "No", "[]", "JSON array of email addresses to receive reports.", '["dev@skynetparts.com"]'],
        ],
    )
    doc.add_paragraph(
        "If Gemini or Resend keys are missing, report generation tasks will fail silently. "
        "The rest of the application remains unaffected."
    )

    doc.add_heading("2.11 Frontend Environment Variables", level=2)
    add_table(doc,
        ["Variable", "Required", "Default", "Description", "Example"],
        [
            ["VITE_API_BASE_URL", "Yes", "—", "Backend API base URL. All frontend API calls are made to this URL.", "http://127.0.0.1:8000"],
            ["VITE_SUPABASE_URL", "Yes", "—", "Supabase project URL for real-time subscriptions.", "https://abc.supabase.co"],
            ["VITE_SUPABASE_ANON_KEY", "Yes", "—", "Supabase anonymous key for client-side subscriptions.", "eyJhbGci..."],
            ["VITE_AVIATION_GATEWAY_URL", "No", "—", "Aviation Gateway URL for SSO login redirects.", "http://localhost:8080"],
        ],
    )

    # ── 3. Config Files ──────────────────────────────────────────────
    doc.add_heading("3. Configuration Files", level=1)
    add_table(doc,
        ["File", "Purpose"],
        [
            ["backend/app/core/config.py", "Pydantic Settings class — defines and validates all backend env vars"],
            ["backend/app/celery_app/celery_config.py", "Celery configuration: broker URL, queues, beat schedule, task annotations"],
            ["backend/app/core/middleware.py", "CORS middleware settings (allowed origins, methods, headers)"],
            [".github/workflows/deploy-backend.yml", "GitHub Actions CI/CD pipeline for backend deployment"],
            ["frontend/vite.config.ts", "Vite build configuration: port (8080), host, SWC plugin, path aliases"],
            ["frontend/tailwind.config.ts", "Tailwind CSS theme: colors, fonts, animations, plugins"],
            ["frontend/tsconfig.app.json", "TypeScript compiler options: strict mode, path mapping"],
            ["frontend/.eslintrc / eslint.config.js", "ESLint rules for TypeScript and React"],
            ["frontend/index.html", "HTML entry point: meta tags, root div, script import"],
        ],
    )

    # ── 4. Feature Flags ─────────────────────────────────────────────
    doc.add_heading("4. Feature Flags", level=1)
    doc.add_paragraph(
        "The project uses environment variables as feature flags rather than a dedicated "
        "feature flag service."
    )
    add_table(doc,
        ["Flag", "Type", "Default", "What It Controls"],
        [
            ["SYNC_ENABLED", "boolean", "true", "Master switch for the entire sync system. When false, no sync operations run."],
            ["SYNC_MODE", "string", "testing", "Switches between testing mode (fast minute-based cycles) and production mode (hourly cycles)."],
            ["AUTO_START_CELERY", "boolean", "false", "Whether FastAPI auto-starts Celery worker/beat subprocesses on application boot."],
            ["SYNC_FREQUENCY", "string", "weekly", "Controls whether sync runs daily or weekly."],
        ],
    )

    # ── 5. Secrets Management ────────────────────────────────────────
    doc.add_heading("5. Secrets Management", level=1)

    doc.add_heading("5.1 Local Development", level=2)
    doc.add_paragraph(
        "Secrets are stored in .env files (backend/.env, frontend/.env.local). These files "
        "are listed in .gitignore and must never be committed."
    )

    doc.add_heading("5.2 CI/CD (GitHub Actions)", level=2)
    doc.add_paragraph(
        "The deployment pipeline uses GitHub Actions secrets. Currently, only EC2_SSH_KEY is "
        "stored as a repository secret. Backend .env is pre-configured on the EC2 server."
    )

    doc.add_heading("5.3 Production (EC2)", level=2)
    doc.add_paragraph(
        "The production .env file lives at /home/ubuntu/boeing-data-hub/backend/.env on the "
        "EC2 instance. It is excluded from rsync deployments (--exclude '.env'). Changes to "
        "production env vars must be made directly on the server via SSH."
    )

    doc.add_heading("5.4 What Must NEVER Be Committed", level=2)
    add_bullet(doc, ".env files (backend/.env, frontend/.env.local)")
    add_bullet(doc, "SSH private keys (*.pem)")
    add_bullet(doc, "SUPABASE_SERVICE_ROLE_KEY (grants full DB admin access)")
    add_bullet(doc, "SHOPIFY_ADMIN_API_TOKEN")
    add_bullet(doc, "BOEING_CLIENT_SECRET, BOEING_PASSWORD")
    add_bullet(doc, "COGNITO_APP_CLIENT_ID (not a secret per se, but keep it private)")
    add_bullet(doc, "GEMINI_API_KEY, RESEND_API_KEY")

    # ── 6. Environment Differences ───────────────────────────────────
    doc.add_heading("6. Environment Differences", level=1)
    add_table(doc,
        ["Variable / Setting", "Local Development", "Production (EC2)"],
        [
            ["VITE_API_BASE_URL", "http://127.0.0.1:8000", "https://api.boeing-data-hub.skynetparts.com"],
            ["REDIS_URL", "redis://localhost:6379/0", "redis://localhost:6379/0"],
            ["AUTO_START_CELERY", "false (manual workers)", "false (systemd manages workers)"],
            ["SYNC_MODE", "testing", "production"],
            ["SYNC_FREQUENCY", "weekly", "weekly"],
            ["SYNC_DISPATCH_MINUTE", "*/10 (every 10 min)", "45 (at :45 of each hour)"],
            ["SSL/TLS", "None (HTTP)", "Let's Encrypt (HTTPS)"],
            ["Process Manager", "Manual terminals", "systemd (6 services)"],
            ["Uvicorn", "uvicorn --reload", "systemd service (no --reload)"],
            ["Frontend", "npm run dev (Vite)", "Pre-built dist/ served by Nginx"],
            ["Log Access", "Terminal stdout", "journalctl -u <service>"],
        ],
    )

    # ── 7. Adding a New Variable ─────────────────────────────────────
    doc.add_heading("7. Adding a New Environment Variable", level=1)
    doc.add_paragraph("When you need to add a new configuration value, follow these steps:")

    add_numbered_steps(doc, [
        "Add the variable to the Pydantic Settings class in **backend/app/core/config.py** with a type, default value (if optional), and a clear field description.",
        "Add the variable to your local **.env** file with the development value.",
        "Update **.env.example** (if it exists) with a placeholder value and a comment.",
        "If the variable is a secret, add it to **.gitignore** patterns if not already covered.",
        "SSH into the EC2 production server and add the variable to the production **.env** file.",
        "If it is needed in CI/CD, add it as a GitHub Actions **repository secret**.",
        "Update this Configuration Reference document with the new variable's details.",
        "Add the variable to the **Environment Differences** table (Section 6) if the value differs between environments.",
    ])

    add_code_block(doc, """# Example: Adding a new variable to config.py
class Settings(BaseModel):
    # ... existing settings ...
    my_new_variable: str = Field(
        default="default_value",
        description="Description of what this controls"
    )""")

    return save_doc(doc, "Configuration_Reference.docx")
