"""Generate API_Reference.docx."""

from doc_helpers import (
    create_doc, add_table, add_code_block, add_bullet, save_doc,
)


def generate():
    doc = create_doc("API Reference")

    # ── 1. Overview ──────────────────────────────────────────────────
    doc.add_heading("1. Overview", level=1)
    doc.add_paragraph(
        "The Boeing Data Hub API is a RESTful JSON API built with FastAPI. All endpoints "
        "are versioned under /api/v1/. Legacy endpoints under /api/ redirect to v1."
    )
    add_table(doc,
        ["Property", "Value"],
        [
            ["Base URL", "https://api.boeing-data-hub.skynetparts.com/api/v1"],
            ["Content-Type", "application/json"],
            ["API Version", "v1"],
            ["Auth Method", "Bearer token (JWT via AWS Cognito)"],
        ],
    )

    # ── 2. Authentication ────────────────────────────────────────────
    doc.add_heading("2. Authentication", level=1)
    doc.add_paragraph(
        "Most endpoints require a valid JWT access token from Aviation Gateway (AWS Cognito). "
        "Include the token in the Authorization header:"
    )
    add_code_block(doc, "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...")
    doc.add_paragraph(
        "Tokens are obtained by logging in through the Aviation Gateway SSO flow. The backend "
        "validates the token against Cognito's JWKS endpoint, checking signature, expiry, "
        "issuer, and token_use claims."
    )
    doc.add_paragraph("Unauthenticated requests to protected endpoints return 401 Unauthorized.")

    # ── 3. Endpoints ─────────────────────────────────────────────────
    doc.add_heading("3. Endpoints", level=1)

    # ── Health ───────────────────────────────────────────────────────
    doc.add_heading("3.1 Health Check", level=2)

    doc.add_heading("GET /health", level=3)
    doc.add_paragraph("Returns the health status of the API. No authentication required.")
    add_code_block(doc, 'curl https://api.boeing-data-hub.skynetparts.com/health')
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, '{\n  "status": "healthy"\n}')

    # ── Auth ─────────────────────────────────────────────────────────
    doc.add_heading("3.2 Authentication", level=2)

    doc.add_heading("GET /api/v1/auth/me", level=3)
    doc.add_paragraph("Returns the currently authenticated user's information.")
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/auth/me""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, """{
  "user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "username": "john.doe",
  "email": "john.doe@skynetparts.com",
  "groups": ["admin", "operations"]
}""")

    doc.add_heading("POST /api/v1/auth/logout", level=3)
    doc.add_paragraph("Signs the user out of all Cognito sessions (global sign-out).")
    add_code_block(doc, """curl -X POST -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/auth/logout""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, '{\n  "success": true,\n  "message": "Successfully logged out"\n}')

    # ── Extraction ───────────────────────────────────────────────────
    doc.add_heading("3.3 Extraction", level=2)

    doc.add_heading("GET /api/v1/extraction/search", level=3)
    doc.add_paragraph("Search Boeing's PNA system for a single part number.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["query", "string (query)", "Yes", "Part number to search for"],
        ],
    )
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  "https://api.boeing-data-hub.skynetparts.com/api/v1/extraction/search?query=WF338109" """)
    doc.add_paragraph("Response (200 OK): Returns normalized product data and stages it in the database.")

    doc.add_heading("POST /api/v1/extraction/bulk-search", level=3)
    doc.add_paragraph("Start a bulk search operation for multiple part numbers. Returns immediately with a batch ID; processing happens asynchronously.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["part_numbers", "string[] (body)", "Yes", "Array of part numbers to search"],
            ["idempotency_key", "string (body)", "No", "Unique key to prevent duplicate batches"],
        ],
    )
    add_code_block(doc, """curl -X POST -H "Authorization: Bearer <token>" \\
  -H "Content-Type: application/json" \\
  -d '{"part_numbers": ["WF338109", "BA725011", "NAS1149"], "idempotency_key": "search-20260223-001"}' \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/extraction/bulk-search""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, """{
  "batch_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "pending",
  "total_items": 3,
  "message": "Bulk search started"
}""")

    # ── Batches ──────────────────────────────────────────────────────
    doc.add_heading("3.4 Batches", level=2)

    doc.add_heading("GET /api/v1/batches", level=3)
    doc.add_paragraph("List all batch operations with pagination and optional status filtering.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["limit", "integer (query)", "No", "Items per page (default: 50)"],
            ["offset", "integer (query)", "No", "Pagination offset (default: 0)"],
            ["status", "string (query)", "No", "Filter by status: pending, processing, completed, failed, cancelled"],
        ],
    )
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  "https://api.boeing-data-hub.skynetparts.com/api/v1/batches?limit=10&status=processing" """)

    doc.add_heading("GET /api/v1/batches/{batch_id}", level=3)
    doc.add_paragraph("Get detailed status and progress for a specific batch.")
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/batches/f47ac10b-58cc-4372-a567-0e02b2c3d479""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, """{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "batch_type": "extract",
  "status": "processing",
  "total_items": 3,
  "extracted_count": 2,
  "normalized_count": 1,
  "published_count": 0,
  "failed_count": 0,
  "progress_percent": 50.0,
  "failed_items": [],
  "created_at": "2026-02-23T10:30:00Z",
  "updated_at": "2026-02-23T10:31:15Z"
}""")

    doc.add_heading("DELETE /api/v1/batches/{batch_id}", level=3)
    doc.add_paragraph("Cancel a running batch. Items already processed are not rolled back.")
    add_code_block(doc, """curl -X DELETE -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/batches/f47ac10b-58cc-4372-a567-0e02b2c3d479""")

    # ── Publishing ───────────────────────────────────────────────────
    doc.add_heading("3.5 Publishing", level=2)

    doc.add_heading("POST /api/v1/publishing/publish", level=3)
    doc.add_paragraph("Publish a single staged product to Shopify.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["part_number", "string (body)", "Yes", "SKU of the staged product to publish"],
            ["batch_id", "string (body)", "No", "Batch ID if part of a bulk operation"],
        ],
    )
    add_code_block(doc, """curl -X POST -H "Authorization: Bearer <token>" \\
  -H "Content-Type: application/json" \\
  -d '{"part_number": "WF338109"}' \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/publishing/publish""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, """{
  "success": true,
  "shopifyProductId": "8234567890123",
  "batch_id": null
}""")

    doc.add_heading("POST /api/v1/publishing/bulk-publish", level=3)
    doc.add_paragraph("Start a bulk publish operation for multiple products.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["part_numbers", "string[] (body)", "Yes", "Array of SKUs to publish"],
            ["batch_id", "string (body)", "No", "Optional batch ID for tracking"],
        ],
    )
    add_code_block(doc, """curl -X POST -H "Authorization: Bearer <token>" \\
  -H "Content-Type: application/json" \\
  -d '{"part_numbers": ["WF338109", "BA725011"]}' \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/publishing/bulk-publish""")

    doc.add_heading("PUT /api/v1/publishing/products/{shopify_product_id}", level=3)
    doc.add_paragraph("Update an existing Shopify product.")
    add_code_block(doc, """curl -X PUT -H "Authorization: Bearer <token>" \\
  -H "Content-Type: application/json" \\
  -d '{"title": "Updated Part Title", "price": "129.99"}' \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/publishing/products/8234567890123""")

    doc.add_heading("GET /api/v1/publishing/check", level=3)
    doc.add_paragraph("Check if a SKU already exists in Shopify.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [["sku", "string (query)", "Yes", "SKU to check"]],
    )
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  "https://api.boeing-data-hub.skynetparts.com/api/v1/publishing/check?sku=WF338109" """)

    # ── Products ─────────────────────────────────────────────────────
    doc.add_heading("3.6 Products", level=2)

    doc.add_heading("GET /api/v1/products/published", level=3)
    doc.add_paragraph("List published products with pagination and search.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["limit", "integer (query)", "No", "Items per page (default: 50)"],
            ["offset", "integer (query)", "No", "Pagination offset (default: 0)"],
            ["search", "string (query)", "No", "Filter by SKU or title (partial match)"],
        ],
    )
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  "https://api.boeing-data-hub.skynetparts.com/api/v1/products/published?limit=20&search=WF338" """)

    doc.add_heading("GET /api/v1/products/published/{product_id}", level=3)
    doc.add_paragraph("Get a single published product by its database ID.")
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/products/published/abc123""")

    doc.add_heading("GET /api/v1/products/staging", level=3)
    doc.add_paragraph("List staged (normalized) products.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["limit", "integer (query)", "No", "Items per page (default: 100)"],
            ["batch_id", "string (query)", "No", "Filter by batch ID"],
        ],
    )

    doc.add_heading("GET /api/v1/products/raw-data/{part_number}", level=3)
    doc.add_paragraph("Get the raw Boeing API response for a specific part number.")
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/products/raw-data/WF338109""")

    # ── Search ───────────────────────────────────────────────────────
    doc.add_heading("3.7 Search", level=2)

    doc.add_heading("POST /api/v1/search/multi-part", level=3)
    doc.add_paragraph("Search for multiple SKUs across Shopify. This endpoint does not require authentication.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [["part_numbers", "string[] (body)", "Yes", "Array of part numbers to search"]],
    )
    add_code_block(doc, """curl -X POST -H "Content-Type: application/json" \\
  -d '{"part_numbers": ["WF338109", "BA725011", "NOTEXIST"]}' \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/search/multi-part""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, """{
  "found": [
    {"sku": "WF338109", "shopify_product_id": "8234567890123", "price": "119.99", "in_stock": true},
    {"sku": "BA725011", "shopify_product_id": "8234567890456", "price": "45.50", "in_stock": false}
  ],
  "not_found": ["NOTEXIST"]
}""")

    # ── Sync ─────────────────────────────────────────────────────────
    doc.add_heading("3.8 Sync", level=2)

    doc.add_heading("GET /api/v1/sync/dashboard", level=3)
    doc.add_paragraph("Get a full overview of the sync system status.")
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/sync/dashboard""")
    doc.add_paragraph("Response (200 OK):")
    add_code_block(doc, """{
  "total_products": 1250,
  "active_products": 1180,
  "inactive_products": 70,
  "success_rate": 94.5,
  "high_failure_count": 12,
  "status_counts": {"pending": 45, "syncing": 3, "success": 1132, "failed": 70},
  "slot_distribution": {"active": 18, "filling": 4, "dormant": 2},
  "efficiency_percent": 94.5,
  "last_updated": "2026-02-23T10:45:00Z"
}""")

    doc.add_heading("GET /api/v1/sync/products", level=3)
    doc.add_paragraph("List products in the sync schedule.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [
            ["limit", "integer (query)", "No", "Items per page (default: 50)"],
            ["status", "string (query)", "No", "Filter: pending, syncing, success, failed"],
        ],
    )

    doc.add_heading("GET /api/v1/sync/history", level=3)
    doc.add_paragraph("Get recent sync operation history.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [["hours_back", "integer (query)", "No", "Lookback window in hours (default: 24)"]],
    )

    doc.add_heading("GET /api/v1/sync/failures", level=3)
    doc.add_paragraph("List products with sync failures.")
    add_table(doc,
        ["Parameter", "Type", "Required", "Description"],
        [["limit", "integer (query)", "No", "Max results (default: 50)"]],
    )

    doc.add_heading("GET /api/v1/sync/hourly-stats", level=3)
    doc.add_paragraph("Get detailed per-hour sync statistics.")

    doc.add_heading("GET /api/v1/sync/product/{sku}", level=3)
    doc.add_paragraph("Get sync status for a specific product by SKU.")
    add_code_block(doc, """curl -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/sync/product/WF338109""")

    doc.add_heading("POST /api/v1/sync/product/{sku}/reactivate", level=3)
    doc.add_paragraph("Reactivate a product that was deactivated due to consecutive sync failures.")
    add_code_block(doc, """curl -X POST -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/sync/product/WF338109/reactivate""")

    doc.add_heading("POST /api/v1/sync/trigger/{sku}", level=3)
    doc.add_paragraph("Trigger an immediate sync for a specific product (bypasses the hourly schedule).")
    add_code_block(doc, """curl -X POST -H "Authorization: Bearer <token>" \\
  https://api.boeing-data-hub.skynetparts.com/api/v1/sync/trigger/WF338109""")

    # ── 4. Rate Limits ───────────────────────────────────────────────
    doc.add_heading("4. Rate Limits", level=1)
    doc.add_paragraph(
        "The API itself does not enforce per-client rate limits. However, the underlying "
        "external API calls are rate-limited to protect against quota exhaustion:"
    )
    add_table(doc,
        ["External API", "Limit", "Enforcement"],
        [
            ["Boeing PNA API", "2 requests/minute", "Redis token bucket (shared across all workers)"],
            ["Shopify Admin API", "30 requests/minute", "Celery task annotation rate limiting"],
        ],
    )
    doc.add_paragraph(
        "If a rate limit is hit internally, the task is automatically retried after a delay. "
        "Clients will see batch processing slow down but should not receive rate limit errors."
    )

    # ── 5. Error Code Reference ──────────────────────────────────────
    doc.add_heading("5. Error Code Reference", level=1)
    add_table(doc,
        ["Status Code", "Meaning", "When It Happens"],
        [
            ["200", "OK", "Request succeeded"],
            ["400", "Bad Request", "Invalid request body or parameters"],
            ["401", "Unauthorized", "Missing or invalid JWT token"],
            ["403", "Forbidden", "User lacks required group membership"],
            ["404", "Not Found", "Resource (batch, product, etc.) does not exist"],
            ["409", "Conflict", "Duplicate operation (idempotency key already used)"],
            ["422", "Unprocessable Entity", "Request body fails Pydantic validation"],
            ["429", "Too Many Requests", "External API rate limit exceeded (rare)"],
            ["500", "Internal Server Error", "Unexpected server-side error"],
        ],
    )
    doc.add_paragraph("Error responses follow this format:")
    add_code_block(doc, '{\n  "detail": "Batch not found: f47ac10b-58cc-4372-a567-0e02b2c3d479"\n}')

    # ── 6. Webhooks ──────────────────────────────────────────────────
    doc.add_heading("6. Webhooks", level=1)
    doc.add_paragraph(
        "The Boeing Data Hub does not expose outgoing webhooks. Real-time updates are delivered "
        "to the frontend via Supabase PostgreSQL change subscriptions (not HTTP webhooks)."
    )
    doc.add_paragraph(
        "Supabase channels used: batches (INSERT, UPDATE, DELETE) and product_staging "
        "(INSERT, UPDATE). These are WebSocket-based and handled by the Supabase JS client."
    )

    return save_doc(doc, "API_Reference.docx")
