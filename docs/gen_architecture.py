"""Generate Architecture_Documentation.docx."""

from doc_helpers import (
    create_doc, add_table, add_code_block, add_diagram_placeholder,
    add_bullet, save_doc,
)


def generate():
    doc = create_doc("Architecture Documentation")

    # ── 1. System Overview ───────────────────────────────────────────
    doc.add_heading("1. System Overview", level=1)
    doc.add_paragraph(
        "Boeing Data Hub is a product data pipeline that connects Boeing's Part Number "
        "Availability (PNA) APIs to a Shopify retail storefront. It extracts aviation part "
        "data, normalizes it into a retail-friendly format, publishes products to Shopify, "
        "and continuously synchronizes prices and inventory."
    )
    doc.add_paragraph("Major components:")
    add_bullet(doc, "React SPA Frontend — Dashboard for search, publishing, and sync monitoring")
    add_bullet(doc, "FastAPI Backend — REST API layer with authentication and business logic")
    add_bullet(doc, "Celery Workers — 5 async workers processing extraction, publishing, and sync tasks")
    add_bullet(doc, "Celery Beat — Periodic task scheduler for hourly sync dispatch")
    add_bullet(doc, "Redis — Message broker for Celery and storage for rate limiter / distributed locks")
    add_bullet(doc, "Supabase PostgreSQL — Primary data store with real-time change subscriptions")
    add_bullet(doc, "Boeing PNA API — External data source (OAuth2-authenticated)")
    add_bullet(doc, "Shopify Admin API — Retail platform (REST + GraphQL)")
    add_bullet(doc, "Google Gemini — AI model for generating sync cycle reports")
    add_bullet(doc, "Resend — Email delivery for report distribution")

    # ── 2. Architecture Diagram ──────────────────────────────────────
    doc.add_heading("2. Architecture Diagram", level=1)
    doc.add_paragraph(
        "Below is a Mermaid diagram representing the system architecture. Render it using "
        "any Mermaid-compatible tool (GitHub, Notion, mermaid.live, etc.)."
    )
    add_code_block(doc, """graph TB
    subgraph "Frontend"
        A[React SPA<br/>Vite + TypeScript]
    end

    subgraph "Backend (EC2)"
        B[FastAPI<br/>Uvicorn]
        C[Celery Worker<br/>Extraction]
        D[Celery Worker<br/>Publishing]
        E[Celery Worker<br/>Sync]
        F[Celery Worker<br/>Default]
        G[Celery Beat<br/>Scheduler]
        H[Redis<br/>Broker + Cache]
        I[Nginx<br/>Reverse Proxy]
    end

    subgraph "External Services"
        J[Boeing PNA API]
        K[Shopify Admin API]
        L[AWS Cognito]
        M[Supabase<br/>PostgreSQL + Storage]
        N[Google Gemini]
        O[Resend Email]
    end

    A -->|HTTPS| I
    I -->|Proxy| B
    B -->|Enqueue Tasks| H
    H -->|Consume| C
    H -->|Consume| D
    H -->|Consume| E
    H -->|Consume| F
    G -->|Schedule| H
    C -->|OAuth2| J
    D -->|REST/GraphQL| K
    E -->|OAuth2| J
    E -->|REST| K
    B -->|JWT Verify| L
    B -->|CRUD| M
    C -->|Store| M
    D -->|Store| M
    E -->|Store| M
    A -.->|Realtime WS| M
    F -->|Generate| N
    F -->|Send| O""")

    add_diagram_placeholder(doc, "System Architecture — rendered from Mermaid above")

    # ── 3. Service Boundaries ────────────────────────────────────────
    doc.add_heading("3. Service Boundaries", level=1)

    doc.add_heading("3.1 API Layer (routes/)", level=2)
    doc.add_paragraph(
        "Route handlers accept HTTP requests, validate input via Pydantic schemas, enforce "
        "authentication via get_current_user dependency, and delegate to service classes. "
        "Routes never contain business logic or direct database calls."
    )

    doc.add_heading("3.2 Service Layer (services/)", level=2)
    doc.add_paragraph(
        "Services contain all business logic. They coordinate between multiple stores and "
        "clients, manage transactions, and implement patterns like the saga pattern for "
        "publishing. Services are stateless and injected via the DI container."
    )

    doc.add_heading("3.3 Data Store Layer (db/)", level=2)
    doc.add_paragraph(
        "Each database table has a corresponding store class that encapsulates all CRUD "
        "operations. Stores use the Supabase Python SDK and are responsible for query "
        "construction, result mapping, and error handling."
    )

    doc.add_heading("3.4 Client Layer (clients/)", level=2)
    doc.add_paragraph(
        "Clients wrap external API interactions. Each client handles its own authentication "
        "(OAuth2, API keys), request formatting, error handling, and rate limit awareness. "
        "Clients use httpx for async HTTP calls."
    )

    doc.add_heading("3.5 Task Layer (celery_app/tasks/)", level=2)
    doc.add_paragraph(
        "Celery tasks define the units of asynchronous work. They use the BaseTask class for "
        "DI container access. Tasks are organized by domain: extraction, normalization, "
        "publishing, batch management, sync dispatch, sync Boeing, and sync Shopify."
    )

    # ── 4. Data Flow ─────────────────────────────────────────────────
    doc.add_heading("4. Data Flow", level=1)

    doc.add_heading("4.1 Extraction Flow (User searches for parts)", level=2)
    doc.add_paragraph(
        "1. User enters part numbers in the React dashboard (SearchPanel.tsx)\n"
        "2. Frontend sends POST to /api/v1/extraction/bulk-search\n"
        "3. Route handler (routes/extraction.py) validates the request\n"
        "4. Batch record created in batches table (batch_store.py)\n"
        "5. process_bulk_search Celery task enqueued on 'extraction' queue\n"
        "6. Task splits part numbers into chunks of 10\n"
        "7. extract_chunk tasks call Boeing API (boeing_client.py) — rate limited to 2/min\n"
        "8. Raw responses stored in boeing_raw_data (raw_data_store.py)\n"
        "9. normalize_chunk tasks normalize data (boeing_normalize.py)\n"
        "10. Normalized products upserted in product_staging (staging_store.py)\n"
        "11. Batch progress updated; frontend receives real-time updates via Supabase subscription"
    )

    doc.add_heading("4.2 Publishing Flow (User publishes to Shopify)", level=2)
    doc.add_paragraph(
        "1. User clicks Publish on a staged product\n"
        "2. Frontend sends POST to /api/v1/publishing/publish\n"
        "3. Route handler enqueues publish_product task on 'publishing' queue\n"
        "4. Task reads staged product from product_staging\n"
        "5. Shopify orchestrator creates product via Shopify REST API\n"
        "6. Inventory levels set via Shopify inventory API\n"
        "7. Cost-per-item set on each variant\n"
        "8. Product record saved in product table (product_store.py)\n"
        "9. Sync schedule entry created (sync_store.py) for future auto-sync\n"
        "10. On failure: compensating transactions clean up partial Shopify data"
    )

    doc.add_heading("4.3 Sync Flow (Automated hourly cycle)", level=2)
    doc.add_paragraph(
        "1. Celery Beat triggers dispatch_hourly task at :45 of each hour\n"
        "2. Dispatch lock acquired (Redis) to prevent concurrent dispatch\n"
        "3. Current hour bucket (0-23) calculated\n"
        "4. Products assigned to this bucket fetched from product_sync_schedule\n"
        "5. Products grouped into batches of 10 SKUs\n"
        "6. process_boeing_batch tasks enqueued on 'sync_boeing' queue\n"
        "7. Each batch fetches latest data from Boeing API\n"
        "8. Hash-based change detection compares new vs. stored data\n"
        "9. Changed products get update_shopify_product tasks on 'sync_shopify' queue\n"
        "10. Shopify price, inventory, and cost updated\n"
        "11. Sync results recorded in sync_history\n"
        "12. product_sync_schedule updated with new sync timestamp and status\n"
        "13. Products with 5+ consecutive failures are automatically deactivated"
    )

    # ── 5. Database Schema ───────────────────────────────────────────
    doc.add_heading("5. Database Schema", level=1)
    doc.add_paragraph(
        "All data resides in Supabase (PostgreSQL). Below is a Mermaid ER diagram. "
        "Migrations are stored in the database/ directory."
    )
    add_code_block(doc, """erDiagram
    users {
        text id PK
        text username
        text email
        timestamp created_at
    }
    boeing_raw_data {
        uuid id PK
        text user_id FK
        text search_query
        jsonb raw_payload
        timestamp created_at
    }
    product_staging {
        text id PK
        text user_id FK
        text batch_id FK
        text sku UK
        text title
        numeric price
        integer inventory_quantity
        text status
        timestamp created_at
        timestamp updated_at
    }
    product {
        text id PK
        text user_id FK
        text sku UK
        text shopify_product_id
        text shopify_variant_id
        text title
        numeric price
        timestamp created_at
    }
    batches {
        varchar id PK
        text user_id FK
        text batch_type
        text status
        integer total_items
        integer extracted_count
        integer normalized_count
        integer published_count
        integer failed_count
        jsonb failed_items
        timestamp created_at
    }
    product_sync_schedule {
        uuid id PK
        text user_id FK
        text sku UK
        integer hour_bucket
        text sync_status
        integer consecutive_failures
        boolean is_active
        timestamp last_sync_at
    }
    sync_reports {
        uuid id PK
        text cycle_id
        text report_text
        jsonb summary_stats
        boolean email_sent
        timestamp created_at
    }

    users ||--o{ boeing_raw_data : owns
    users ||--o{ product_staging : owns
    users ||--o{ product : owns
    users ||--o{ batches : owns
    users ||--o{ product_sync_schedule : owns
    batches ||--o{ product_staging : contains""")

    add_diagram_placeholder(doc, "ER Diagram — rendered from Mermaid above")

    # ── 6. Infrastructure Topology ───────────────────────────────────
    doc.add_heading("6. Infrastructure Topology", level=1)
    add_table(doc,
        ["Component", "Platform", "Details"],
        [
            ["Compute", "AWS EC2 (Ubuntu 22.04)", "Single instance hosting backend, workers, Redis, Nginx"],
            ["Database", "Supabase (managed PostgreSQL)", "Hosted PostgreSQL with REST API and realtime"],
            ["File Storage", "Supabase Storage", "Product image storage (bucket: product-images)"],
            ["DNS/SSL", "Nginx + Let's Encrypt (Certbot)", "HTTPS termination, reverse proxy to Uvicorn:8000"],
            ["Message Broker", "Redis 7 (localhost)", "Celery broker, rate limiter tokens, dispatch locks"],
            ["Auth Provider", "AWS Cognito", "User pool with federated SSO via Aviation Gateway"],
            ["E-Commerce", "Shopify", "REST + GraphQL Admin API for product management"],
            ["Data Source", "Boeing PNA API", "OAuth2-authenticated part price and availability"],
            ["AI", "Google Gemini 2.0 Flash", "Sync report generation"],
            ["Email", "Resend", "Transactional email for report delivery"],
            ["CI/CD", "GitHub Actions", "Automated deployment on push to main"],
        ],
    )
    add_diagram_placeholder(doc, "Infrastructure Topology")

    # ── 7. Architecture Decision Records ─────────────────────────────
    doc.add_heading("7. Architecture Decision Records (ADRs)", level=1)

    # ADR 1
    doc.add_heading("ADR-1: FastAPI as the Backend Framework", level=2)
    doc.add_paragraph("Context: The system needs a high-performance Python API framework that supports async I/O for concurrent external API calls (Boeing, Shopify).")
    doc.add_paragraph("Decision: FastAPI was chosen over Flask and Django.")
    doc.add_paragraph("Reasoning: FastAPI provides native async support, automatic OpenAPI documentation, Pydantic validation, and dependency injection. Performance is significantly better than Flask for I/O-bound workloads. Django was too heavyweight for an API-only backend.")
    doc.add_paragraph("Consequences: The team must be comfortable with async/await patterns. Pydantic v2 models are required for request/response schemas. The ecosystem is smaller than Django's.")

    # ADR 2
    doc.add_heading("ADR-2: Celery + Redis for Async Task Processing", level=2)
    doc.add_paragraph("Context: Bulk operations (searching 50K parts, publishing thousands of products) cannot complete within an HTTP request timeout. External APIs have strict rate limits (Boeing: 2/min).")
    doc.add_paragraph("Decision: Celery with Redis as the message broker.")
    doc.add_paragraph("Reasoning: Celery provides reliable task queuing, automatic retries, rate limiting via task annotations, periodic scheduling (Beat), and proven scalability. Redis is lightweight and serves double duty as broker and cache for rate limiters and distributed locks.")
    doc.add_paragraph("Consequences: Additional infrastructure (Redis). Multiple worker processes to manage. Task debugging requires log inspection rather than request traces. Solo pool mode is needed on Windows for development.")

    # ADR 3
    doc.add_heading("ADR-3: Supabase as the Database Platform", level=2)
    doc.add_paragraph("Context: The application needs a relational database with real-time change subscriptions to push updates to the frontend without polling.")
    doc.add_paragraph("Decision: Supabase (managed PostgreSQL) over self-hosted PostgreSQL or Firebase.")
    doc.add_paragraph("Reasoning: Supabase provides PostgreSQL with built-in real-time subscriptions (via WebSocket), a file storage API, and a Python SDK. This eliminates the need for a separate WebSocket server. Firebase was rejected because the data is relational and requires SQL queries.")
    doc.add_paragraph("Consequences: Vendor dependency on Supabase. Service role key grants full database access (must be kept secret). Real-time subscriptions add complexity to the frontend.")

    # ADR 4
    doc.add_heading("ADR-4: Saga Pattern for Product Publishing", level=2)
    doc.add_paragraph("Context: Publishing a product involves multiple steps: create Shopify product, set inventory, set costs, record in database. A failure mid-way could leave orphaned products in Shopify.")
    doc.add_paragraph("Decision: Implement the saga pattern with compensating transactions.")
    doc.add_paragraph("Reasoning: The saga pattern ensures that if any step fails, previous steps are rolled back. For example, if inventory setup fails, the Shopify product is deleted. This maintains data consistency across Shopify and the local database without requiring distributed transactions.")
    doc.add_paragraph("Consequences: More complex publish logic. Each saga step must have a defined compensation action. Error handling is more verbose but more reliable.")

    # ADR 5
    doc.add_heading("ADR-5: Token Bucket Rate Limiting with Redis", level=2)
    doc.add_paragraph("Context: Boeing's API allows only 2 requests per minute. With multiple Celery workers processing tasks concurrently, a global rate limiter is needed.")
    doc.add_paragraph("Decision: Redis-backed token bucket algorithm with Lua scripts for atomicity.")
    doc.add_paragraph("Reasoning: A token bucket allows burst capacity (2 tokens) with steady refill (2/min). Redis ensures the limiter works across all worker processes. Lua scripts guarantee atomic check-and-consume operations, preventing race conditions.")
    doc.add_paragraph("Consequences: Dependency on Redis for rate limiting (not just task brokering). If Redis is down, rate limiting fails. The Lua script adds operational complexity.")

    # ADR 6
    doc.add_heading("ADR-6: Hour Bucket Sync Scheduling", level=2)
    doc.add_paragraph("Context: Thousands of products need periodic price/inventory syncs. Running all at once would overwhelm Boeing's rate-limited API.")
    doc.add_paragraph("Decision: Distribute products across 24 hourly buckets (0-23). Each hour, only the products in the current bucket are synced.")
    doc.add_paragraph("Reasoning: Even distribution ensures predictable API load. The slot manager algorithm fills buckets in groups of 10 (matching Boeing batch size). Products can be reassigned to different buckets for load balancing.")
    doc.add_paragraph("Consequences: Products sync once per day (or per week in weekly mode). Real-time price changes may take up to 24 hours to propagate. The testing mode (6 minute-buckets) allows rapid validation.")

    # ── 8. Scalability & Bottlenecks ─────────────────────────────────
    doc.add_heading("8. Scalability & Bottlenecks", level=1)

    doc.add_heading("8.1 Current Bottlenecks", level=2)
    add_table(doc,
        ["Bottleneck", "Current Limit", "Impact"],
        [
            ["Boeing API rate limit", "2 requests/minute", "Extraction and sync throughput capped at ~20 parts/minute"],
            ["Shopify API rate limit", "30 requests/minute", "Publishing throughput capped at ~30 products/minute"],
            ["Single EC2 instance", "1 server", "All services share compute; single point of failure"],
            ["Redis (localhost)", "Single instance", "If Redis crashes, all task processing and rate limiting stops"],
        ],
    )

    doc.add_heading("8.2 Scaling to 10x", level=2)
    doc.add_paragraph("To handle 10x current load:")
    add_bullet(doc, "Horizontal worker scaling: Run Celery workers on multiple EC2 instances. Redis and the rate limiter already support this (shared state via Redis).")
    add_bullet(doc, "Managed Redis: Move from localhost Redis to Amazon ElastiCache for high availability and persistence.")
    add_bullet(doc, "Multiple Boeing API accounts: Rate limits are per-credential. Multiple accounts would allow parallel extraction streams.")
    add_bullet(doc, "Database connection pooling: Add PgBouncer or Supabase's built-in pooler for handling more concurrent connections.")
    add_bullet(doc, "Containerization: Dockerize services for easier horizontal scaling with ECS, EKS, or Docker Swarm.")
    add_bullet(doc, "Load balancer: Add an ALB in front of multiple FastAPI instances for API-level horizontal scaling.")

    doc.add_heading("8.3 Single Points of Failure", level=2)
    add_table(doc,
        ["Component", "Risk", "Mitigation"],
        [
            ["EC2 Instance", "Instance failure takes down everything", "Multi-AZ deployment or container orchestration"],
            ["Redis", "Broker failure stops all async processing", "ElastiCache with replication"],
            ["Supabase", "Database outage blocks all operations", "Supabase handles HA; consider read replicas for heavy loads"],
            ["Boeing API", "API outage blocks extraction and sync", "Graceful degradation: retry logic, cached data, alert on failures"],
        ],
    )

    return save_doc(doc, "Architecture_Documentation.docx")
