# Boeing Data Hub: Celery + Redis Implementation Guide

> **For Developers** | Version 1.1 | Last Updated: 2025-01-26

---

## Quick Links

- [Getting Started](#getting-started)
- [Implementation Checklist](#4-implementation-checklist-single-phase)
- [File Specifications](#5-detailed-file-specifications)
- [Database Setup](#6-database-schema-changes)
- [Running Workers](#8-worker-specifications)
- [API Reference](#9-api-endpoint-changes)
- [Troubleshooting](#appendix-b-troubleshooting)

---

## Executive Summary

This guide provides step-by-step instructions for integrating **Celery** and **Redis** into the Boeing Data Hub system. The goal is to enable production-ready bulk processing of up to **20,000+ part numbers** with:

- Async task handling
- Automatic retry mechanisms
- Progress tracking
- Rate limiting for external APIs

**Estimated Implementation Time:** Follow the checklist in Section 4 sequentially.

---

## Table of Contents

1. [Current System Analysis](#1-current-system-analysis)
2. [Target Architecture](#2-target-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Implementation Checklist (Single Phase)](#4-implementation-checklist-single-phase)
5. [Detailed File Specifications](#5-detailed-file-specifications)
6. [Database Schema Changes](#6-database-schema-changes)
7. [Configuration Management](#7-configuration-management)
8. [Worker Specifications](#8-worker-specifications)
9. [API Endpoint Changes](#9-api-endpoint-changes)
10. [Error Handling Strategy](#10-error-handling-strategy)
11. [Testing Strategy](#11-testing-strategy)
12. [Deployment Checklist](#12-deployment-checklist)
13. [Monitoring & Observability](#13-monitoring--observability)

---

## 1. Current System Analysis

### 1.1 Existing Architecture

```
backend/
├── app/
│   ├── main.py                 # FastAPI entry point
│   ├── clients/                # External API clients
│   │   ├── boeing_client.py    # Boeing API integration
│   │   ├── shopify_client.py   # Shopify API integration
│   │   └── supabase_client.py  # Supabase client wrapper
│   ├── core/
│   │   └── config.py           # Environment configuration
│   ├── db/
│   │   └── supabase_store.py   # Database operations
│   ├── routes/                 # API endpoints
│   │   ├── boeing.py
│   │   ├── shopify.py
│   │   └── zap.py
│   ├── schemas/                # Pydantic models
│   │   ├── boeing.py
│   │   ├── products.py
│   │   ├── shopify.py
│   │   └── zap.py
│   ├── services/               # Business logic
│   │   ├── boeing_service.py
│   │   ├── shopify_service.py
│   │   └── zap_service.py
│   └── utils/
│       └── boeing_normalize.py # Data transformation
├── .env
└── requirements.txt
```

### 1.2 Current Limitations

| Limitation | Impact | Solution |
|------------|--------|----------|
| Single part number per API request | Can't process bulk orders | Celery batch tasks |
| Synchronous API calls | Blocking, slow for bulk operations | Async workers |
| No retry mechanism | Failures require manual retry | Celery auto-retry |
| No progress tracking | User can't see bulk operation status | Batch tracking table |
| No rate limiting | Risk of hitting API limits | Celery rate limits |
| No background processing | Long requests timeout | Worker queues |

### 1.3 Current Data Flow

```
User Request → FastAPI → Service → Client → External API → Response → Database → User
                         (synchronous, blocking)
```

### 1.4 Files That Will Be Modified

| File | Changes |
|------|---------|
| `requirements.txt` | Add celery, redis dependencies |
| `app/main.py` | Add bulk router, health endpoints |
| `app/core/config.py` | Add Redis configuration |
| `app/clients/boeing_client.py` | Add batch fetch method |
| `app/db/supabase_store.py` | Minor adjustments for batch operations |

### 1.5 Files That Will NOT Be Modified

| File | Reason |
|------|--------|
| `app/clients/shopify_client.py` | Already supports required operations |
| `app/clients/supabase_client.py` | Already configured correctly |
| `app/utils/boeing_normalize.py` | Normalization logic is complete |
| `app/schemas/*.py` | Existing schemas are sufficient |
| `app/services/zap_service.py` | Zap integration remains unchanged |
| `app/routes/zap.py` | Zap endpoints remain unchanged |

---

## 2. Target Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BOEING DATA HUB                                    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         APPLICATION LAYER                            │   │
│  │                                                                      │   │
│  │   ┌──────────────┐         ┌──────────────┐                        │   │
│  │   │   FastAPI    │         │    Redis     │                        │   │
│  │   │   Server     │────────▶│   Broker     │                        │   │
│  │   │  Port 8000   │         │  Port 6379   │                        │   │
│  │   └──────────────┘         └──────────────┘                        │   │
│  │         │                         │                                 │   │
│  │         │ API Requests            │ Task Messages                   │   │
│  │         ▼                         ▼                                 │   │
│  │   ┌─────────────────────────────────────────────────────────────┐  │   │
│  │   │                    CELERY WORKERS                            │  │   │
│  │   │                                                              │  │   │
│  │   │  ┌────────────┐   ┌────────────┐   ┌────────────┐          │  │   │
│  │   │  │ Extraction │   │Normalizat. │   │  Shopify   │          │  │   │
│  │   │  │  Workers   │   │  Workers   │   │  Workers   │          │  │   │
│  │   │  │ (Queue: E) │   │ (Queue: N) │   │ (Queue: S) │          │  │   │
│  │   │  │            │   │            │   │            │          │  │   │
│  │   │  │ Concur: 2  │   │ Concur: 4  │   │ Concur: 1  │          │  │   │
│  │   │  └────────────┘   └────────────┘   └────────────┘          │  │   │
│  │   │        │                │                │                   │  │   │
│  │   └────────┼────────────────┼────────────────┼───────────────────┘  │   │
│  │            │                │                │                      │   │
│  └────────────┼────────────────┼────────────────┼──────────────────────┘   │
│               │                │                │                          │
│               ▼                ▼                ▼                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                       EXTERNAL SERVICES                              │   │
│  │                                                                      │   │
│  │   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐           │   │
│  │   │  Boeing API  │   │   Supabase   │   │ Shopify API  │           │   │
│  │   │              │   │  (Database   │   │              │           │   │
│  │   │  OAuth +     │   │   + Storage) │   │  REST +      │           │   │
│  │   │  Part API    │   │              │   │  GraphQL     │           │   │
│  │   └──────────────┘   └──────────────┘   └──────────────┘           │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow with Celery

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           BULK SEARCH DATA FLOW                               │
│                                                                               │
│   1. API Request                                                              │
│   ──────────────                                                              │
│   POST /api/bulk-search                                                       │
│   { "part_numbers": ["PN-001", "PN-002", ... "PN-20000"] }                   │
│         │                                                                     │
│         ▼                                                                     │
│   2. FastAPI Handler                                                          │
│   ──────────────────                                                          │
│   • Validate request                                                          │
│   • Check idempotency_key for duplicate prevention                           │
│   • Create batch record in Supabase (1 row)                                  │
│   • Enqueue Celery task: process_bulk_search.delay(batch_id, part_numbers)   │
│   • Return immediately: { batch_id, status: "processing" }                   │
│         │                                                                     │
│         │ (Instant response to user)                                         │
│         ▼                                                                     │
│   3. Redis Queue                                                              │
│   ──────────────                                                              │
│   Queue "extraction" receives task message                                    │
│   Task waits for available worker                                            │
│         │                                                                     │
│         │ (Worker picks up immediately - push model)                         │
│         ▼                                                                     │
│   4. Extraction Worker                                                        │
│   ────────────────────                                                        │
│   FOR each chunk of 10 part numbers (configurable via BOEING_BATCH_SIZE):   │
│     • Call Boeing API with batch of 10                                       │
│     • Store raw response in boeing_raw_data table                            │
│     • Chain to normalization: normalize_chunk.delay(batch_id, chunk, data)   │
│     • Update batch.extracted_count += 10                                     │
│         │                                                                     │
│         │ (Automatic chaining)                                               │
│         ▼                                                                     │
│   5. Normalization Worker                                                     │
│   ───────────────────────                                                     │
│   FOR each item in chunk:                                                    │
│     • Apply normalize_boeing_payload()                                       │
│     • Upsert to product_staging table                                        │
│     • Update batch.normalized_count += 1                                     │
│         │                                                                     │
│         │ (Batch complete when all normalized)                               │
│         ▼                                                                     │
│   6. Completion                                                               │
│   ──────────────                                                              │
│   • Batch status updated to "completed"                                      │
│   • Products available in product_staging                                    │
│   • User can poll GET /api/batches/{batch_id} for status                    │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Publish Data Flow

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          BULK PUBLISH DATA FLOW                               │
│                                                                               │
│   User triggers publish (after search complete)                              │
│         │                                                                     │
│         ▼                                                                     │
│   POST /api/bulk-publish                                                      │
│   { "part_numbers": ["PN-001", "PN-002", ...] }                             │
│         │                                                                     │
│         ▼                                                                     │
│   Create publish batch → Enqueue publish_batch task                          │
│         │                                                                     │
│         ▼                                                                     │
│   Shopify Worker (Rate Limited: 30/minute)                                   │
│   FOR each part_number:                                                      │
│     • Get product from product_staging                                       │
│     • Upload image to Supabase Storage                                       │
│     • Call Shopify API to create product                                     │
│     • Set category, inventory, cost                                          │
│     • Save to products table with Shopify ID                                 │
│     • If DB save fails → DELETE from Shopify (compensation)                  │
│     • Update batch.published_count += 1                                      │
│         │                                                                     │
│         ▼                                                                     │
│   Batch status → "completed"                                                 │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Directory Structure

### 3.1 Current Structure (UNCHANGED)

Your existing project structure remains exactly as-is:

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                          # MINOR MODIFICATION: Add bulk router
│   ├── clients/                         # UNCHANGED
│   │   ├── __init__.py
│   │   ├── boeing_client.py             # MINOR MODIFICATION: Add batch method
│   │   ├── shopify_client.py
│   │   └── supabase_client.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py                    # MINOR MODIFICATION: Add Redis config
│   ├── db/
│   │   ├── __init__.py
│   │   └── supabase_store.py            # UNCHANGED
│   ├── routes/                          # UNCHANGED
│   │   ├── __init__.py
│   │   ├── boeing.py
│   │   ├── shopify.py
│   │   └── zap.py
│   ├── schemas/                         # UNCHANGED
│   │   ├── __init__.py
│   │   ├── boeing.py
│   │   ├── products.py
│   │   ├── shopify.py
│   │   └── zap.py
│   ├── services/                        # UNCHANGED
│   │   ├── __init__.py
│   │   ├── boeing_service.py
│   │   ├── shopify_service.py
│   │   └── zap_service.py
│   └── utils/
│       ├── __init__.py
│       └── boeing_normalize.py          # UNCHANGED
│
├── .env
└── requirements.txt
```

### 3.2 New Additions (Celery Only)

Only these new files/folders will be added:

```
backend/
├── app/
│   ├── ...                              # (existing - unchanged)
│   ├── core/
│   │   └── exceptions.py                # NEW: Error hierarchy
│   ├── db/
│   │   └── batch_store.py               # NEW: Batch progress tracking
│   ├── routes/
│   │   └── bulk.py                      # NEW: Bulk operation endpoints
│   └── schemas/
│       └── bulk.py                      # NEW: Bulk operation schemas
│
├── celery_app/                          # NEW: Celery application
│   ├── __init__.py                      # Exports celery_app instance
│   ├── celery_config.py                 # Celery broker/queue configuration
│   └── tasks/
│       ├── __init__.py                  # Task exports
│       ├── base.py                      # Base task class with retry logic
│       ├── extraction.py                # Boeing API extraction tasks
│       ├── normalization.py             # Data transformation tasks
│       ├── publishing.py                # Shopify publishing tasks
│       └── batch.py                     # Batch orchestration tasks
│
├── .env                                 # MODIFIED: Add REDIS_URL
└── requirements.txt                     # MODIFIED: Add celery, redis
```

### 3.3 Summary of Changes

| Location | Change Type | Files |
|----------|-------------|-------|
| `app/` | Minor edits | `main.py`, `core/config.py`, `clients/boeing_client.py` |
| `app/core/` | New file | `exceptions.py` |
| `app/db/` | New file | `batch_store.py` |
| `app/routes/` | New file | `bulk.py` |
| `app/schemas/` | New file | `bulk.py` |
| `celery_app/` | New folder | Entire Celery application |
| Root | Modified | `requirements.txt`, `.env` |

### 3.4 Celery Folder Structure Explained

```
celery_app/
├── __init__.py              # Entry point - exports celery_app
├── celery_config.py         # All Celery settings (broker, queues, rates)
└── tasks/
    ├── __init__.py          # Exports all tasks for easy imports
    ├── base.py              # BaseTask class with retry/error handling
    ├── extraction.py        # extract_bulk, extract_chunk
    ├── normalization.py     # normalize_chunk, normalize_single
    ├── publishing.py        # publish_batch, publish_product
    └── batch.py             # check_completion, cleanup_stale, cancel_batch
```

**Why this structure:**
- Flat task files (no nested `shared/`, `helpers/` folders) - simpler for your codebase size
- Clear naming: `extraction.py` instead of `extraction_tasks.py` (shorter)
- Single `base.py` for shared task logic instead of separate `shared/` folder
- All configuration in one `celery_config.py` file

---

## 4. Implementation Checklist (Single Phase)

> **IMPORTANT:** Complete each step in order. Each step depends on previous steps being complete.

### Step 1: Dependencies & Configuration

| # | Action | File | Description | Done |
|---|--------|------|-------------|------|
| 1 | MODIFY | `requirements.txt` | Add `celery[redis]==5.3.6`, `redis==5.0.1` | ☐ |
| 2 | MODIFY | `.env` | Add `REDIS_URL`, `BOEING_BATCH_SIZE`, `MAX_BULK_SEARCH_SIZE`, `MAX_BULK_PUBLISH_SIZE` | ☐ |
| 3 | MODIFY | `app/core/config.py` | Add Redis, Celery, and batch processing settings | ☐ |
| 4 | CREATE | `app/core/exceptions.py` | Add `RetryableError`, `NonRetryableError` hierarchy | ☐ |

### Step 2: Database Schema

| # | Action | Location | Description | Done |
|---|--------|----------|-------------|------|
| 5 | CREATE | Supabase SQL Editor | Create `batches` table with `idempotency_key` column | ☐ |
| 6 | CREATE | Supabase SQL Editor | Create helper functions (`increment_batch_counter`, `record_batch_failure`, `update_batch_status`) | ☐ |

### Step 3: Schemas & Data Layer

| # | Action | File | Description | Done |
|---|--------|------|-------------|------|
| 7 | CREATE | `app/schemas/bulk.py` | Request/response models with idempotency support | ☐ |
| 8 | CREATE | `app/db/batch_store.py` | Batch progress tracking operations | ☐ |

### Step 4: Celery Infrastructure

| # | Action | File | Description | Done |
|---|--------|------|-------------|------|
| 9 | CREATE | `celery_app/__init__.py` | Export `celery_app` instance | ☐ |
| 10 | CREATE | `celery_app/celery_config.py` | Broker, queues, rate limits, routing | ☐ |
| 11 | CREATE | `celery_app/tasks/__init__.py` | Export all tasks | ☐ |
| 12 | CREATE | `celery_app/tasks/base.py` | `BaseTask` class + dependency helpers | ☐ |

### Step 5: Worker Tasks

| # | Action | File | Description | Done |
|---|--------|------|-------------|------|
| 13 | CREATE | `celery_app/tasks/extraction.py` | `process_bulk_search`, `extract_chunk` | ☐ |
| 14 | CREATE | `celery_app/tasks/normalization.py` | `normalize_chunk` | ☐ |
| 15 | CREATE | `celery_app/tasks/publishing.py` | `publish_batch`, `publish_product` (with compensation) | ☐ |
| 16 | CREATE | `celery_app/tasks/batch.py` | `check_batch_completion`, `cancel_batch`, `cleanup_stale_batches` | ☐ |

### Step 6: API & Integration

| # | Action | File | Description | Done |
|---|--------|------|-------------|------|
| 17 | CREATE | `app/routes/bulk.py` | Bulk operation endpoints with idempotency check | ☐ |
| 18 | MODIFY | `app/main.py` | Add bulk router | ☐ |
| 19 | MODIFY | `app/clients/boeing_client.py` | Add `fetch_price_availability_batch` method | ☐ |
| 20 | MODIFY | `app/clients/shopify_client.py` | Add `delete_product` method (for compensation) | ☐ |

### Validation Checklist

After completing all steps, verify:

```
☐ Redis connection works: redis-cli ping
☐ Celery app starts: celery -A celery_app worker --loglevel=INFO
☐ Batches table exists in Supabase
☐ POST /api/bulk-search returns batch_id
☐ GET /api/batches/{id} returns status
☐ Workers process tasks from queues
☐ Retry logic triggers on simulated failure
☐ Rate limiting respects Boeing/Shopify limits
☐ Idempotency key prevents duplicate batches
☐ Cancel batch works via DELETE endpoint
```

---

## 5. Detailed File Specifications

### 5.1 Celery Configuration

#### `celery_app/__init__.py`

```python
"""
Celery application package.
Exports the main Celery app instance.
"""
from celery_app.celery_config import celery_app

__all__ = ["celery_app"]
```

#### `celery_app/celery_config.py`

```python
"""
Celery application configuration.
Configures Redis broker, task queues, rate limiting, and retry policies.
"""
from celery import Celery
from kombu import Queue
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "boeing_data_hub",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "celery_app.tasks.extraction",
        "celery_app.tasks.normalization",
        "celery_app.tasks.publishing",
        "celery_app.tasks.batch",
    ]
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    # Task routing - 4 queues for pipeline stages
    task_queues=(
        Queue("extraction"),
        Queue("normalization"),
        Queue("publishing"),
        Queue("default"),
    ),
    task_routes={
        "celery_app.tasks.extraction.*": {"queue": "extraction"},
        "celery_app.tasks.normalization.*": {"queue": "normalization"},
        "celery_app.tasks.publishing.*": {"queue": "publishing"},
        "celery_app.tasks.batch.*": {"queue": "default"},
    },

    # Rate limiting
    task_annotations={
        "celery_app.tasks.extraction.extract_chunk": {
            "rate_limit": "20/m",  # Boeing API limit
        },
        "celery_app.tasks.publishing.publish_product": {
            "rate_limit": "30/m",  # Shopify API limit
        },
    },

    # Result expiration
    result_expires=3600,  # 1 hour

    # Retry settings
    task_default_retry_delay=30,
    task_max_retries=3,
)
```

### 5.2 Task Files

#### `celery_app/tasks/__init__.py`

```python
"""
Celery tasks package.
Exports all tasks for convenient imports.
"""
from celery_app.tasks.extraction import process_bulk_search, extract_chunk
from celery_app.tasks.normalization import normalize_chunk
from celery_app.tasks.publishing import publish_batch, publish_product
from celery_app.tasks.batch import check_batch_completion, cancel_batch, cleanup_stale_batches

__all__ = [
    "process_bulk_search",
    "extract_chunk",
    "normalize_chunk",
    "publish_batch",
    "publish_product",
    "check_batch_completion",
    "cancel_batch",
    "cleanup_stale_batches",
]
```

#### `celery_app/tasks/base.py`

```python
"""
Base task class with common functionality.

Provides:
- Automatic dependency injection
- Standardized error handling
- Logging configuration
- Retry logic
"""
import asyncio
import logging
from celery import Task
from typing import Any, Dict

logger = logging.getLogger(__name__)


class BaseTask(Task):
    """Base task with common functionality for all workers."""

    # Don't create abstract tasks
    abstract = True

    # Default retry settings
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 300  # 5 minutes max backoff
    retry_jitter = True
    max_retries = 3

    # Track task state
    track_started = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails after all retries exhausted."""
        logger.error(f"Task {self.name}[{task_id}] failed: {exc}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is being retried."""
        logger.warning(f"Task {self.name}[{task_id}] retrying (attempt {self.request.retries}): {exc}")

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds."""
        logger.info(f"Task {self.name}[{task_id}] succeeded")


# ============================================
# Async Helper
# ============================================
def run_async(coro):
    """
    Run async function in sync context.

    Use this to call async methods from Celery tasks.
    Each call creates a new event loop to avoid conflicts.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================
# Dependency helpers (lazy loading)
# ============================================
_dependencies = None


def get_dependencies():
    """
    Lazy load dependencies.

    Called after fork so each worker gets own instances.
    This prevents connection sharing issues between workers.
    """
    global _dependencies
    if _dependencies is None:
        from app.core.config import settings
        from app.clients.boeing_client import BoeingClient
        from app.clients.shopify_client import ShopifyClient
        from app.db.supabase_store import SupabaseStore
        from app.db.batch_store import BatchStore

        _dependencies = {
            "settings": settings,
            "boeing_client": BoeingClient(settings),
            "shopify_client": ShopifyClient(settings),
            "supabase_store": SupabaseStore(settings),
            "batch_store": BatchStore(settings),
        }
    return _dependencies


def get_boeing_client():
    """Get Boeing API client instance."""
    return get_dependencies()["boeing_client"]


def get_shopify_client():
    """Get Shopify API client instance."""
    return get_dependencies()["shopify_client"]


def get_supabase_store():
    """Get Supabase store instance."""
    return get_dependencies()["supabase_store"]


def get_batch_store():
    """Get batch store instance."""
    return get_dependencies()["batch_store"]
```

#### `celery_app/tasks/extraction.py`

```python
"""
Extraction tasks for fetching data from Boeing API.

Tasks:
- process_bulk_search: Orchestrates bulk search by splitting into chunks
- extract_chunk: Extracts a single chunk of part numbers from Boeing API
"""
import logging
import os
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_boeing_client,
    get_supabase_store,
    get_batch_store
)
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)

# Configurable chunk size - start with 10 for safety, tune via env var
BOEING_BATCH_SIZE = int(os.getenv("BOEING_BATCH_SIZE", "10"))


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.extraction.process_bulk_search",
    max_retries=0  # Orchestrator doesn't retry - child tasks do
)
def process_bulk_search(self, batch_id: str, part_numbers: List[str]):
    """
    Process a bulk search request.

    Splits part numbers into chunks and queues extraction tasks.
    This is the main orchestrator task for bulk searches.

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers to search

    Returns:
        dict: Summary of queued tasks
    """
    logger.info(f"Starting bulk search for batch {batch_id} with {len(part_numbers)} parts (chunk size: {BOEING_BATCH_SIZE})")

    batch_store = get_batch_store()

    try:
        # Update status to processing
        batch_store.update_status(batch_id, "processing")

        # Split into chunks using configurable size
        chunks = [
            part_numbers[i:i + BOEING_BATCH_SIZE]
            for i in range(0, len(part_numbers), BOEING_BATCH_SIZE)
        ]

        logger.info(f"Split into {len(chunks)} chunks of up to {BOEING_BATCH_SIZE} parts each")

        # Queue extraction for each chunk
        for i, chunk in enumerate(chunks):
            extract_chunk.delay(batch_id, chunk, chunk_index=i, total_chunks=len(chunks))

        logger.info(f"Queued {len(chunks)} extraction tasks for batch {batch_id}")

        return {
            "batch_id": batch_id,
            "total_parts": len(part_numbers),
            "chunks_queued": len(chunks),
            "chunk_size": BOEING_BATCH_SIZE
        }

    except Exception as e:
        logger.error(f"Bulk search failed for batch {batch_id}: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.extraction.extract_chunk",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
)
def extract_chunk(
    self,
    batch_id: str,
    part_numbers: List[str],
    chunk_index: int = 0,
    total_chunks: int = 1
):
    """
    Extract a chunk of part numbers from Boeing API.

    Args:
        batch_id: Batch identifier
        part_numbers: Part numbers to extract (max BOEING_BATCH_SIZE)
        chunk_index: Current chunk index (for logging)
        total_chunks: Total number of chunks (for logging)

    Returns:
        dict: Summary of extraction results
    """
    logger.info(
        f"Extracting chunk {chunk_index + 1}/{total_chunks} "
        f"({len(part_numbers)} parts) for batch {batch_id}"
    )

    boeing_client = get_boeing_client()
    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    try:
        # Call Boeing API with batch of part numbers
        raw_response = run_async(
            boeing_client.fetch_price_availability_batch(part_numbers)
        )

        # Store raw data for audit trail
        run_async(
            supabase_store.insert_boeing_raw_data(
                search_query=",".join(part_numbers),
                raw_payload=raw_response
            )
        )

        # Update extraction progress
        batch_store.increment_extracted(batch_id, len(part_numbers))

        # Chain to normalization
        from celery_app.tasks.normalization import normalize_chunk
        normalize_chunk.delay(batch_id, part_numbers, raw_response)

        logger.info(f"Extraction complete for chunk {chunk_index + 1}/{total_chunks}")

        return {
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "parts_extracted": len(part_numbers)
        }

    except Exception as e:
        logger.error(f"Extraction failed for chunk {chunk_index + 1}: {e}")

        # If max retries exceeded, record failures for each part number
        if self.request.retries >= self.max_retries:
            for pn in part_numbers:
                batch_store.record_failure(batch_id, pn, f"Extraction failed: {e}")

            # Check if batch should be marked complete
            from celery_app.tasks.batch import check_batch_completion
            check_batch_completion.delay(batch_id)

        raise
```

#### `celery_app/tasks/normalization.py`

```python
"""
Normalization tasks for transforming Boeing data to Shopify-friendly format.

Tasks:
- normalize_chunk: Normalizes a chunk of products from raw Boeing data
"""
import logging
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_supabase_store,
    get_batch_store
)
from app.utils.boeing_normalize import normalize_boeing_payload
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.normalization.normalize_chunk",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3
)
def normalize_chunk(self, batch_id: str, part_numbers: List[str], raw_response: Dict[str, Any]):
    """
    Normalize a chunk of products from raw Boeing data.

    Transforms Boeing API response format to Shopify-friendly format
    and stores in product_staging table.

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers in this chunk
        raw_response: Raw response from Boeing API

    Returns:
        dict: Summary of normalization results
    """
    logger.info(f"Normalizing {len(part_numbers)} parts for batch {batch_id}")

    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    line_items = raw_response.get("lineItems", [])
    currency = raw_response.get("currency")

    # Create lookup by part number for O(1) access
    item_lookup = {
        item.get("aviallPartNumber"): item
        for item in line_items
        if item.get("aviallPartNumber")
    }

    normalized_count = 0
    failed_count = 0

    for pn in part_numbers:
        try:
            item = item_lookup.get(pn)
            if not item:
                batch_store.record_failure(batch_id, pn, "Not found in Boeing response")
                failed_count += 1
                continue

            # Use existing normalize function
            normalized_list = normalize_boeing_payload(
                pn,
                {"lineItems": [item], "currency": currency}
            )

            if normalized_list:
                run_async(supabase_store.upsert_product_staging(normalized_list))
                normalized_count += 1
            else:
                batch_store.record_failure(batch_id, pn, "Normalization produced no results")
                failed_count += 1

        except Exception as e:
            logger.error(f"Failed to normalize {pn}: {e}")
            batch_store.record_failure(batch_id, pn, f"Normalization error: {e}")
            failed_count += 1

    # Update batch progress
    if normalized_count > 0:
        batch_store.increment_normalized(batch_id, normalized_count)

    # Check if batch is complete
    from celery_app.tasks.batch import check_batch_completion
    check_batch_completion.delay(batch_id)

    logger.info(f"Normalized {normalized_count}/{len(part_numbers)} parts ({failed_count} failed)")

    return {
        "batch_id": batch_id,
        "normalized": normalized_count,
        "failed": failed_count,
        "total": len(part_numbers)
    }
```

#### `celery_app/tasks/publishing.py`

```python
"""
Shopify publishing tasks with transaction compensation.

Tasks:
- publish_batch: Orchestrates publishing a batch of products
- publish_product: Publishes a single product with rollback on failure

IMPORTANT: This implements the Saga pattern for transaction safety.
If Shopify publish succeeds but DB save fails, we rollback by deleting
the orphaned Shopify product.
"""
import logging
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_shopify_client,
    get_supabase_store,
    get_batch_store
)
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)

# Fallback image when Boeing doesn't provide one
FALLBACK_IMAGE = "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto"


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.publishing.publish_batch",
    max_retries=0  # Orchestrator doesn't retry
)
def publish_batch(self, batch_id: str, part_numbers: List[str]):
    """
    Orchestrate publishing a batch of products to Shopify.

    Queues individual publish_product tasks for each part number.
    Rate limiting is handled at the task level (30/min for Shopify).

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers to publish

    Returns:
        dict: Summary of queued tasks
    """
    logger.info(f"Starting publish batch {batch_id} with {len(part_numbers)} products")
    batch_store = get_batch_store()

    try:
        # Update status to processing
        batch_store.update_status(batch_id, "processing")

        # Queue individual publish tasks
        for pn in part_numbers:
            publish_product.delay(batch_id, pn)

        logger.info(f"Queued {len(part_numbers)} publish tasks for batch {batch_id}")

        return {
            "batch_id": batch_id,
            "products_queued": len(part_numbers)
        }

    except Exception as e:
        logger.error(f"Publish batch failed: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.publishing.publish_product",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="30/m"  # Shopify rate limit
)
def publish_product(self, batch_id: str, part_number: str):
    """
    Publish a single product to Shopify with transaction compensation.

    TRANSACTION SAFETY:
    If DB save fails after Shopify publish succeeds, we attempt to delete
    the orphaned Shopify product to maintain consistency.

    Args:
        batch_id: Batch identifier for tracking
        part_number: Part number to publish

    Returns:
        dict: Result including Shopify product ID
    """
    logger.info(f"Publishing {part_number} to Shopify")

    shopify_client = get_shopify_client()
    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    # Track Shopify product ID for potential rollback
    shopify_product_id = None

    try:
        # 1. Get product from staging
        record = run_async(
            supabase_store.get_product_staging_by_part_number(part_number)
        )
        if not record:
            raise NonRetryableError(f"Product {part_number} not found in staging")

        # 2. Handle image upload
        boeing_image_url = record.get("boeing_image_url") or record.get("boeing_thumbnail_url")
        if boeing_image_url:
            try:
                image_url, image_path = run_async(
                    supabase_store.upload_image_from_url(boeing_image_url, part_number)
                )
                record["image_url"] = image_url
                record["image_path"] = image_path
                run_async(
                    supabase_store.update_product_staging_image(part_number, image_url, image_path)
                )
            except Exception as img_err:
                logger.warning(f"Image upload failed, using placeholder: {img_err}")
                record["image_url"] = FALLBACK_IMAGE
        else:
            record["image_url"] = FALLBACK_IMAGE

        # 3. Prepare Shopify payload
        record = _prepare_shopify_record(record)

        # 4. Publish to Shopify
        result = run_async(shopify_client.publish_product(record))

        shopify_product_id = result.get("product", {}).get("id")
        if not shopify_product_id:
            raise ValueError("Shopify did not return product ID")

        # 5. CRITICAL: Save to database with compensation on failure
        try:
            run_async(
                supabase_store.upsert_product(record, shopify_product_id=str(shopify_product_id))
            )
        except Exception as db_err:
            # COMPENSATION: Delete orphaned Shopify product
            logger.error(
                f"DB save failed after Shopify publish. "
                f"Compensating by deleting Shopify product {shopify_product_id}"
            )
            try:
                run_async(shopify_client.delete_product(shopify_product_id))
                logger.info(f"Compensation successful: deleted orphaned Shopify product {shopify_product_id}")
            except Exception as rollback_err:
                # CRITICAL: Manual reconciliation needed
                logger.critical(
                    f"ORPHANED PRODUCT: Shopify ID {shopify_product_id} for part {part_number}. "
                    f"DB error: {db_err}, Rollback error: {rollback_err}"
                )
                batch_store.record_failure(
                    batch_id,
                    part_number,
                    f"ORPHANED: Shopify {shopify_product_id} - needs manual cleanup"
                )
            raise db_err

        # 6. Update batch progress
        batch_store.increment_published(batch_id)

        # 7. Check if batch is complete
        from celery_app.tasks.batch import check_batch_completion
        check_batch_completion.delay(batch_id)

        logger.info(f"Published {part_number} -> Shopify ID: {shopify_product_id}")

        return {
            "success": True,
            "part_number": part_number,
            "shopify_product_id": str(shopify_product_id)
        }

    except NonRetryableError:
        # Don't retry validation errors
        batch_store.record_failure(batch_id, part_number, str(e))
        from celery_app.tasks.batch import check_batch_completion
        check_batch_completion.delay(batch_id)
        raise

    except Exception as e:
        logger.error(f"Failed to publish {part_number}: {e}")

        # Record failure if max retries exceeded
        if self.request.retries >= self.max_retries:
            batch_store.record_failure(batch_id, part_number, str(e))
            from celery_app.tasks.batch import check_batch_completion
            check_batch_completion.delay(batch_id)
        raise


def _prepare_shopify_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare record with Shopify-specific fields.

    Transforms normalized Boeing data to Shopify product format.
    Includes pricing calculation (10% markup on list price).

    Args:
        record: Normalized product record from staging

    Returns:
        dict: Record with shopify field populated
    """
    record.setdefault("shopify", {})

    # Calculate pricing with 10% markup
    list_price = record.get("list_price")
    net_price = record.get("net_price")
    base_cost = list_price if list_price is not None else net_price
    shop_price = (base_cost * 1.1) if base_cost is not None else record.get("price")

    record["shopify"].update({
        "title": record.get("title"),
        "sku": record.get("sku"),
        "description": record.get("boeing_name") or record.get("title"),
        "body_html": record.get("body_html") or "",
        "vendor": record.get("vendor"),
        "manufacturer": record.get("supplier_name") or record.get("vendor"),
        "price": shop_price,
        "cost_per_item": base_cost,
        "currency": record.get("currency"),
        "unit_of_measure": record.get("base_uom"),
        "country_of_origin": record.get("country_of_origin"),
        "length": record.get("dim_length"),
        "width": record.get("dim_width"),
        "height": record.get("dim_height"),
        "dim_uom": record.get("dim_uom"),
        "weight": record.get("weight"),
        "weight_uom": record.get("weight_unit"),
        "inventory_quantity": record.get("inventory_quantity"),
        "location_summary": record.get("location_summary"),
        "product_image": record.get("image_url"),
        "thumbnail_image": record.get("boeing_thumbnail_url"),
        "cert": "FAA 8130-3",
        "condition": record.get("condition") or "NE",
        "pma": record.get("pma"),
        "estimated_lead_time_days": record.get("estimated_lead_time_days") or 3,
        "trace": record.get("trace"),
        "expiration_date": record.get("expiration_date"),
        "notes": record.get("notes"),
    })

    record["name"] = record.get("boeing_name") or record.get("title")
    record["description"] = record.get("boeing_description") or ""

    return record
```

#### `celery_app/tasks/batch.py`

```python
"""
Batch orchestration tasks.

Tasks:
- check_batch_completion: Check if a batch has completed
- cancel_batch: Cancel a batch and revoke tasks
- cleanup_stale_batches: Mark stuck batches as failed
"""
import logging
from datetime import datetime, timedelta

from celery_app.celery_config import celery_app
from celery_app.tasks.base import BaseTask, get_batch_store

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.batch.check_batch_completion"
)
def check_batch_completion(self, batch_id: str):
    """
    Check if a batch has completed and update status.

    A batch is complete when:
    - Search batch: (normalized_count + failed_count) >= total_items
    - Publish batch: (published_count + failed_count) >= total_items

    Args:
        batch_id: Batch identifier to check

    Returns:
        dict: Completion status
    """
    logger.info(f"Checking completion for batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found")
        return {"batch_id": batch_id, "error": "not_found"}

    # Skip if already finalized
    if batch["status"] in ("completed", "failed", "cancelled"):
        return {
            "batch_id": batch_id,
            "status": batch["status"],
            "already_finalized": True
        }

    total = batch["total_items"]
    normalized = batch["normalized_count"]
    published = batch["published_count"]
    failed = batch["failed_count"]

    is_complete = False

    if batch["batch_type"] == "search":
        is_complete = (normalized + failed) >= total
    elif batch["batch_type"] == "publish":
        is_complete = (published + failed) >= total

    if is_complete:
        batch_store.update_status(batch_id, "completed")
        logger.info(f"Batch {batch_id} marked as completed")
        return {
            "batch_id": batch_id,
            "status": "completed",
            "total": total,
            "succeeded": normalized if batch["batch_type"] == "search" else published,
            "failed": failed
        }

    return {
        "batch_id": batch_id,
        "status": "processing",
        "progress": {
            "total": total,
            "normalized": normalized,
            "published": published,
            "failed": failed
        }
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.batch.cancel_batch"
)
def cancel_batch(self, batch_id: str):
    """
    Cancel a batch and revoke all in-flight Celery tasks.

    This attempts to terminate any tasks currently running for this batch.
    Note: Tasks already completed cannot be undone.

    Args:
        batch_id: Batch identifier to cancel

    Returns:
        dict: Cancellation result
    """
    logger.info(f"Cancelling batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found for cancellation")
        return {"success": False, "error": "Batch not found"}

    if batch["status"] in ("completed", "failed", "cancelled"):
        logger.info(f"Batch {batch_id} already finalized: {batch['status']}")
        return {"success": False, "error": f"Batch already {batch['status']}"}

    # Revoke the main orchestrator task if it exists
    celery_task_id = batch.get("celery_task_id")
    if celery_task_id:
        try:
            # Revoke with terminate=True to kill running tasks
            celery_app.control.revoke(celery_task_id, terminate=True, signal='SIGTERM')
            logger.info(f"Revoked main task {celery_task_id} for batch {batch_id}")
        except Exception as e:
            logger.warning(f"Failed to revoke task {celery_task_id}: {e}")

    # Note: Child tasks (extract_chunk, normalize_chunk, etc.) will continue
    # but their results will be ignored since batch is cancelled.
    # For full cancellation, you'd need to track all child task IDs.

    batch_store.update_status(batch_id, "cancelled", "Cancelled by user request")
    logger.info(f"Batch {batch_id} marked as cancelled")

    return {
        "success": True,
        "batch_id": batch_id,
        "status": "cancelled"
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.batch.cleanup_stale_batches"
)
def cleanup_stale_batches(self, max_age_hours: int = 24):
    """
    Mark stuck batches as failed after timeout.

    Run this periodically (e.g., via Celery Beat) to clean up
    batches that got stuck due to worker crashes or other issues.

    Args:
        max_age_hours: Maximum age in hours before marking as failed

    Returns:
        dict: Cleanup results
    """
    logger.info(f"Cleaning up stale batches older than {max_age_hours} hours")

    batch_store = get_batch_store()
    active_batches = batch_store.get_active_batches()
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    cleaned_count = 0

    for batch in active_batches:
        created_at = datetime.fromisoformat(batch["created_at"].replace("Z", "+00:00"))
        if created_at.replace(tzinfo=None) < cutoff:
            batch_store.update_status(
                batch["id"],
                "failed",
                f"Timed out after {max_age_hours} hours"
            )
            logger.warning(f"Marked batch {batch['id']} as failed (timed out)")
            cleaned_count += 1

    return {
        "batches_checked": len(active_batches),
        "batches_cleaned": cleaned_count
    }
```

### 5.3 Batch Store

#### `app/db/batch_store.py`

```python
"""
Batch store for tracking bulk operation progress.

This module provides CRUD operations for the batches table.
All methods are synchronous to simplify Celery task code.
"""
import uuid
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class BatchStore:
    """
    Store for managing batch operations.

    Provides methods for:
    - Creating new batches
    - Updating progress counters
    - Recording failures
    - Querying batch status
    """

    def __init__(self, settings):
        """
        Initialize BatchStore with Supabase client.

        Args:
            settings: Application settings containing Supabase credentials
        """
        from supabase import create_client
        self.client = create_client(settings.supabase_url, settings.supabase_key)
        self.table = "batches"

    def create_batch(
        self,
        batch_type: str,
        total_items: int,
        idempotency_key: Optional[str] = None,
        celery_task_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new batch record.

        Args:
            batch_type: "search" or "publish"
            total_items: Total number of items to process
            idempotency_key: Optional client-provided key for duplicate prevention
            celery_task_id: Optional Celery task ID for the orchestrator

        Returns:
            dict: Created batch record
        """
        batch_id = str(uuid.uuid4())

        data = {
            "id": batch_id,
            "batch_type": batch_type,
            "status": "pending",
            "total_items": total_items,
            "extracted_count": 0,
            "normalized_count": 0,
            "published_count": 0,
            "failed_count": 0,
            "failed_items": [],
            "celery_task_id": celery_task_id,
            "idempotency_key": idempotency_key,
        }

        result = self.client.table(self.table).insert(data).execute()
        logger.info(f"Created batch {batch_id} (type: {batch_type}, items: {total_items})")

        return result.data[0] if result.data else data

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a batch by ID.

        Args:
            batch_id: Batch identifier

        Returns:
            dict or None: Batch record if found
        """
        result = self.client.table(self.table)\
            .select("*")\
            .eq("id", batch_id)\
            .execute()

        return result.data[0] if result.data else None

    def get_batch_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Look up batch by idempotency key.

        Used to prevent duplicate batch creation on client retries.

        Args:
            idempotency_key: Client-provided idempotency key

        Returns:
            dict or None: Existing batch if found
        """
        result = self.client.table(self.table)\
            .select("*")\
            .eq("idempotency_key", idempotency_key)\
            .execute()

        return result.data[0] if result.data else None

    def get_active_batches(self) -> List[Dict[str, Any]]:
        """
        Get all active (pending/processing) batches.

        Returns:
            list: List of active batch records
        """
        result = self.client.table(self.table)\
            .select("*")\
            .in_("status", ["pending", "processing"])\
            .order("created_at", desc=True)\
            .execute()

        return result.data or []

    def update_status(
        self,
        batch_id: str,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """
        Update batch status.

        Uses the update_batch_status SQL function for atomic updates.

        Args:
            batch_id: Batch identifier
            status: New status (pending, processing, completed, failed, cancelled)
            error_message: Optional error message for failed status
        """
        # Call the SQL function for atomic update
        self.client.rpc(
            "update_batch_status",
            {
                "p_batch_id": batch_id,
                "p_status": status,
                "p_error": error_message
            }
        ).execute()

        logger.info(f"Updated batch {batch_id} status to {status}")

    def increment_extracted(self, batch_id: str, count: int = 1) -> None:
        """
        Increment the extracted_count counter.

        Args:
            batch_id: Batch identifier
            count: Number to increment by (default: 1)
        """
        self.client.rpc(
            "increment_batch_counter",
            {
                "p_batch_id": batch_id,
                "p_column": "extracted_count",
                "p_amount": count
            }
        ).execute()

    def increment_normalized(self, batch_id: str, count: int = 1) -> None:
        """
        Increment the normalized_count counter.

        Args:
            batch_id: Batch identifier
            count: Number to increment by (default: 1)
        """
        self.client.rpc(
            "increment_batch_counter",
            {
                "p_batch_id": batch_id,
                "p_column": "normalized_count",
                "p_amount": count
            }
        ).execute()

    def increment_published(self, batch_id: str, count: int = 1) -> None:
        """
        Increment the published_count counter.

        Args:
            batch_id: Batch identifier
            count: Number to increment by (default: 1)
        """
        self.client.rpc(
            "increment_batch_counter",
            {
                "p_batch_id": batch_id,
                "p_column": "published_count",
                "p_amount": count
            }
        ).execute()

    def record_failure(
        self,
        batch_id: str,
        part_number: str,
        error: str
    ) -> None:
        """
        Record a failed item.

        Uses the record_batch_failure SQL function to atomically:
        - Append to failed_items JSONB array
        - Increment failed_count

        Args:
            batch_id: Batch identifier
            part_number: Part number that failed
            error: Error message describing the failure
        """
        self.client.rpc(
            "record_batch_failure",
            {
                "p_batch_id": batch_id,
                "p_part_number": part_number,
                "p_error": error
            }
        ).execute()

        logger.warning(f"Recorded failure for {part_number} in batch {batch_id}: {error}")

    def list_batches(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        List batches with pagination.

        Args:
            limit: Maximum number of batches to return
            offset: Number of batches to skip
            status: Optional status filter

        Returns:
            tuple: (list of batches, total count)
        """
        query = self.client.table(self.table).select("*", count="exact")

        if status:
            query = query.eq("status", status)

        result = query\
            .order("created_at", desc=True)\
            .range(offset, offset + limit - 1)\
            .execute()

        return result.data or [], result.count or 0
```

---

## 6. Database Schema Changes

### 6.1 New Table: batches

> **Execute this SQL in Supabase SQL Editor**

```sql
-- ============================================
-- Create batches table for tracking bulk operations
-- ============================================
CREATE TABLE IF NOT EXISTS batches (
    id VARCHAR(36) PRIMARY KEY,
    batch_type VARCHAR(20) NOT NULL CHECK (batch_type IN ('search', 'publish')),
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')),

    -- Progress tracking
    total_items INT NOT NULL DEFAULT 0,
    extracted_count INT DEFAULT 0,
    normalized_count INT DEFAULT 0,
    published_count INT DEFAULT 0,
    failed_count INT DEFAULT 0,

    -- Error tracking (stores list of failed part numbers with errors)
    error_message TEXT,
    failed_items JSONB DEFAULT '[]'::JSONB,

    -- Celery integration
    celery_task_id VARCHAR(100),

    -- Idempotency support (prevents duplicate batches on client retry)
    idempotency_key VARCHAR(100) UNIQUE,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Index for querying by status
CREATE INDEX IF NOT EXISTS idx_batches_status
    ON batches(status, created_at DESC);

-- Index for idempotency lookups
CREATE INDEX IF NOT EXISTS idx_batches_idempotency
    ON batches(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Index for active batches
CREATE INDEX IF NOT EXISTS idx_batches_active
    ON batches(created_at DESC)
    WHERE status IN ('pending', 'processing');
```

### 6.2 Helper Functions

> **Execute this SQL in Supabase SQL Editor after creating the table**

```sql
-- ============================================
-- Function: Atomically increment a batch counter
-- ============================================
CREATE OR REPLACE FUNCTION increment_batch_counter(
    p_batch_id TEXT,
    p_column TEXT,
    p_amount INT DEFAULT 1
) RETURNS VOID AS $$
BEGIN
    EXECUTE format(
        'UPDATE batches SET %I = %I + $2, updated_at = NOW() WHERE id = $1',
        p_column, p_column
    ) USING p_batch_id, p_amount;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Function: Record a failed item
-- ============================================
CREATE OR REPLACE FUNCTION record_batch_failure(
    p_batch_id TEXT,
    p_part_number TEXT,
    p_error TEXT
) RETURNS VOID AS $$
BEGIN
    UPDATE batches
    SET
        failed_items = failed_items || jsonb_build_object(
            'part_number', p_part_number,
            'error', p_error,
            'timestamp', NOW()
        ),
        failed_count = failed_count + 1,
        updated_at = NOW()
    WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Function: Update batch status
-- ============================================
CREATE OR REPLACE FUNCTION update_batch_status(
    p_batch_id TEXT,
    p_status TEXT,
    p_error TEXT DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    UPDATE batches
    SET
        status = p_status,
        error_message = COALESCE(p_error, error_message),
        updated_at = NOW(),
        completed_at = CASE
            WHEN p_status IN ('completed', 'failed', 'cancelled') THEN NOW()
            ELSE completed_at
        END
    WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;
```

---

## 7. Configuration Management

### 7.1 Updated Environment Variables

> **Add these to your `.env` file**

```bash
# ============================================
# REDIS CONFIGURATION
# ============================================
REDIS_URL=redis://localhost:6379/0

# For production with authentication:
# REDIS_URL=redis://:password@hostname:6379/0

# ============================================
# CELERY CONFIGURATION
# ============================================
CELERY_BROKER_URL=${REDIS_URL}
CELERY_RESULT_BACKEND=${REDIS_URL}

# Worker concurrency settings
CELERY_EXTRACTION_CONCURRENCY=2
CELERY_NORMALIZATION_CONCURRENCY=4
CELERY_SHOPIFY_CONCURRENCY=1

# ============================================
# BATCH PROCESSING SETTINGS
# ============================================
# Chunk size for Boeing API calls (start with 10, can increase to 20 if stable)
BOEING_BATCH_SIZE=10

# Maximum allowed items per request
MAX_BULK_SEARCH_SIZE=50000
MAX_BULK_PUBLISH_SIZE=10000

# ============================================
# RATE LIMITING
# ============================================
BOEING_API_RATE_LIMIT=20/m
SHOPIFY_API_RATE_LIMIT=30/m
```

### 7.2 Updated Config File

> **Add these settings to `app/core/config.py`**

```python
# app/core/config.py - additions to existing Settings class

class Settings(BaseModel):
    # ... existing settings ...

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Celery
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Worker concurrency
    celery_extraction_concurrency: int = int(os.getenv("CELERY_EXTRACTION_CONCURRENCY", "2"))
    celery_normalization_concurrency: int = int(os.getenv("CELERY_NORMALIZATION_CONCURRENCY", "4"))
    celery_shopify_concurrency: int = int(os.getenv("CELERY_SHOPIFY_CONCURRENCY", "1"))

    # Batch processing settings
    boeing_batch_size: int = int(os.getenv("BOEING_BATCH_SIZE", "10"))
    max_bulk_search_size: int = int(os.getenv("MAX_BULK_SEARCH_SIZE", "50000"))
    max_bulk_publish_size: int = int(os.getenv("MAX_BULK_PUBLISH_SIZE", "10000"))

    # Rate limits
    boeing_api_rate_limit: str = os.getenv("BOEING_API_RATE_LIMIT", "20/m")
    shopify_api_rate_limit: str = os.getenv("SHOPIFY_API_RATE_LIMIT", "30/m")
```

### 7.3 Requirements Update

> **Add these to `requirements.txt`**

```txt
# Async task queue
celery[redis]==5.3.6
redis==5.0.1

# Optional: Monitoring (add when needed)
# flower==2.0.1
```

---

## 8. Worker Specifications

### 8.1 Worker Types and Configuration

| Worker | Queue | Concurrency | Rate Limit | Purpose |
|--------|-------|-------------|------------|---------|
| Extraction | extraction | 2 | 20/min | Fetch from Boeing API |
| Normalization | normalization | 4 | None | Transform data (CPU-bound) |
| Publishing | publishing | 1 | 30/min | Publish to Shopify |
| Default | default | 2 | None | Batch orchestration tasks |

### 8.2 Worker Start Commands

```bash
# ============================================
# DEVELOPMENT: Run each in a separate terminal
# ============================================

# Terminal 1: FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Extraction workers (2 concurrent)
celery -A celery_app worker \
    --queues=extraction \
    --concurrency=2 \
    --loglevel=INFO \
    -n extraction@%h

# Terminal 3: Normalization workers (4 concurrent)
celery -A celery_app worker \
    --queues=normalization \
    --concurrency=4 \
    --loglevel=INFO \
    -n normalization@%h

# Terminal 4: Publishing worker (1 concurrent - rate limited)
celery -A celery_app worker \
    --queues=publishing \
    --concurrency=1 \
    --loglevel=INFO \
    -n publishing@%h

# Terminal 5: Default workers (batch tasks)
celery -A celery_app worker \
    --queues=default \
    --concurrency=2 \
    --loglevel=INFO \
    -n default@%h
```

### 8.3 Single Worker Command (All Queues)

For simpler development, you can run all queues in one worker:

```bash
# All queues in one worker (development only)
celery -A celery_app worker \
    --queues=extraction,normalization,publishing,default \
    --concurrency=4 \
    --loglevel=INFO
```

### 8.4 Production: systemd Service Files

> **Create these files on your EC2 instance**

#### `/etc/systemd/system/celery-extraction.service`

```ini
[Unit]
Description=Celery Extraction Worker
After=network.target redis.service

[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/boeing-data-hub/backend
Environment="PATH=/home/ubuntu/boeing-data-hub/venv/bin"
ExecStart=/home/ubuntu/boeing-data-hub/venv/bin/celery -A celery_app worker \
    --queues=extraction \
    --concurrency=2 \
    --loglevel=INFO \
    -n extraction@%%h \
    --detach \
    --pidfile=/tmp/celery-extraction.pid \
    --logfile=/var/log/boeing/celery-extraction.log

ExecStop=/bin/kill -TERM $MAINPID
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### `/etc/systemd/system/celery-normalization.service`

```ini
[Unit]
Description=Celery Normalization Worker
After=network.target redis.service

[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/boeing-data-hub/backend
Environment="PATH=/home/ubuntu/boeing-data-hub/venv/bin"
ExecStart=/home/ubuntu/boeing-data-hub/venv/bin/celery -A celery_app worker \
    --queues=normalization \
    --concurrency=4 \
    --loglevel=INFO \
    -n normalization@%%h \
    --detach \
    --pidfile=/tmp/celery-normalization.pid \
    --logfile=/var/log/boeing/celery-normalization.log

ExecStop=/bin/kill -TERM $MAINPID
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### `/etc/systemd/system/celery-publishing.service`

```ini
[Unit]
Description=Celery Publishing Worker
After=network.target redis.service

[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/boeing-data-hub/backend
Environment="PATH=/home/ubuntu/boeing-data-hub/venv/bin"
ExecStart=/home/ubuntu/boeing-data-hub/venv/bin/celery -A celery_app worker \
    --queues=publishing \
    --concurrency=1 \
    --loglevel=INFO \
    -n publishing@%%h \
    --detach \
    --pidfile=/tmp/celery-publishing.pid \
    --logfile=/var/log/boeing/celery-publishing.log

ExecStop=/bin/kill -TERM $MAINPID
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Enable and Start Services

```bash
# Create log directory
sudo mkdir -p /var/log/boeing
sudo chown ubuntu:ubuntu /var/log/boeing

# Reload systemd
sudo systemctl daemon-reload

# Enable services (start on boot)
sudo systemctl enable celery-extraction
sudo systemctl enable celery-normalization
sudo systemctl enable celery-publishing

# Start services
sudo systemctl start celery-extraction
sudo systemctl start celery-normalization
sudo systemctl start celery-publishing

# Check status
sudo systemctl status celery-extraction
sudo systemctl status celery-normalization
sudo systemctl status celery-publishing
```

---

## 9. API Endpoint Changes

### 9.1 New Bulk Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/bulk-search` | Start bulk search operation |
| POST | `/api/bulk-publish` | Start bulk publish operation |
| GET | `/api/batches` | List all batches |
| GET | `/api/batches/{batch_id}` | Get batch status |
| DELETE | `/api/batches/{batch_id}` | Cancel batch |

### 9.2 Bulk API Schemas

#### `app/schemas/bulk.py`

```python
"""
Pydantic schemas for bulk operations.

Provides request/response models for:
- Bulk search requests
- Bulk publish requests
- Batch status responses
"""
import re
import os
from pydantic import BaseModel, Field, validator, root_validator
from typing import List, Optional
from datetime import datetime

# Configurable limits via environment variables
MAX_BULK_SEARCH_SIZE = int(os.getenv("MAX_BULK_SEARCH_SIZE", "50000"))
MAX_BULK_PUBLISH_SIZE = int(os.getenv("MAX_BULK_PUBLISH_SIZE", "10000"))


class BulkSearchRequest(BaseModel):
    """
    Request to start a bulk search operation.

    Supports multiple input methods:
    - part_numbers: Direct list of part numbers
    - part_numbers_text: Newline or comma-separated text
    - idempotency_key: Client-generated UUID to prevent duplicate batches

    Examples:
        # Direct list
        {"part_numbers": ["PN-001", "PN-002", "PN-003"]}

        # Text input (comma separated)
        {"part_numbers_text": "PN-001, PN-002, PN-003"}

        # Text input (newline separated)
        {"part_numbers_text": "PN-001\\nPN-002\\nPN-003"}

        # With idempotency key
        {"part_numbers": ["PN-001"], "idempotency_key": "uuid-here"}
    """
    part_numbers: Optional[List[str]] = Field(
        None,
        max_items=MAX_BULK_SEARCH_SIZE,
        description="List of part numbers to search"
    )
    part_numbers_text: Optional[str] = Field(
        None,
        description="Newline or comma-separated part numbers (alternative to list)"
    )
    idempotency_key: Optional[str] = Field(
        None,
        description="Client-generated UUID to prevent duplicate batch creation on retries"
    )

    @root_validator(pre=True)
    def parse_part_numbers(cls, values):
        """Parse part_numbers from text if provided."""
        part_numbers = values.get('part_numbers')
        part_numbers_text = values.get('part_numbers_text')

        if part_numbers and part_numbers_text:
            raise ValueError("Provide either 'part_numbers' or 'part_numbers_text', not both")

        if part_numbers_text:
            # Parse newline, comma, or semicolon separated text
            parsed = [pn.strip() for pn in re.split(r'[,;\n\r]+', part_numbers_text) if pn.strip()]
            if not parsed:
                raise ValueError("No valid part numbers found in text")
            if len(parsed) > MAX_BULK_SEARCH_SIZE:
                raise ValueError(f"Maximum {MAX_BULK_SEARCH_SIZE} part numbers allowed")
            values['part_numbers'] = parsed

        if not values.get('part_numbers'):
            raise ValueError("Either 'part_numbers' or 'part_numbers_text' is required")

        return values

    @validator('part_numbers', each_item=True)
    def validate_part_number_format(cls, v):
        """Basic validation for part number format."""
        if not v or len(v) > 50:
            raise ValueError(f"Invalid part number: {v}")
        return v.strip().upper()


class BulkPublishRequest(BaseModel):
    """
    Request to start a bulk publish operation.

    Same input options as BulkSearchRequest.
    """
    part_numbers: Optional[List[str]] = Field(
        None,
        max_items=MAX_BULK_PUBLISH_SIZE,
        description="List of part numbers to publish"
    )
    part_numbers_text: Optional[str] = Field(
        None,
        description="Newline or comma-separated part numbers"
    )
    idempotency_key: Optional[str] = Field(
        None,
        description="Client-generated UUID to prevent duplicate batch creation"
    )

    @root_validator(pre=True)
    def parse_part_numbers(cls, values):
        """Parse part_numbers from text if provided."""
        part_numbers = values.get('part_numbers')
        part_numbers_text = values.get('part_numbers_text')

        if part_numbers_text and not part_numbers:
            parsed = [pn.strip() for pn in re.split(r'[,;\n\r]+', part_numbers_text) if pn.strip()]
            if parsed:
                if len(parsed) > MAX_BULK_PUBLISH_SIZE:
                    raise ValueError(f"Maximum {MAX_BULK_PUBLISH_SIZE} part numbers allowed")
                values['part_numbers'] = parsed

        if not values.get('part_numbers'):
            raise ValueError("Either 'part_numbers' or 'part_numbers_text' is required")

        return values


class BulkOperationResponse(BaseModel):
    """Response for bulk operation initiation."""
    batch_id: str
    total_items: int
    status: str
    message: str
    idempotency_key: Optional[str] = None


class FailedItem(BaseModel):
    """Details of a failed item."""
    part_number: str
    error: str
    timestamp: Optional[datetime] = None


class BatchStatusResponse(BaseModel):
    """
    Detailed batch status response.

    Includes progress tracking and failed items list.
    """
    id: str
    batch_type: str
    status: str
    total_items: int
    extracted_count: int
    normalized_count: int
    published_count: int
    failed_count: int
    progress_percent: float
    failed_items: Optional[List[FailedItem]] = None
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class BatchListResponse(BaseModel):
    """Response for listing batches."""
    batches: List[BatchStatusResponse]
    total: int
```

### 9.3 Bulk Routes

#### `app/routes/bulk.py`

```python
"""
Bulk operation API endpoints.

Provides endpoints for:
- Starting bulk search operations
- Starting bulk publish operations
- Checking batch status
- Cancelling batches
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.schemas.bulk import (
    BulkSearchRequest,
    BulkPublishRequest,
    BulkOperationResponse,
    BatchStatusResponse,
    BatchListResponse,
    FailedItem,
)
from app.db.batch_store import BatchStore
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["bulk"])

# Initialize batch store
batch_store = BatchStore(settings)


@router.post("/bulk-search", response_model=BulkOperationResponse)
async def bulk_search(request: BulkSearchRequest):
    """
    Start a bulk search operation.

    Accepts a list of part numbers and queues them for extraction from Boeing API.
    Returns immediately with a batch_id for progress tracking.

    Supports idempotency: if the same idempotency_key is provided twice,
    returns the existing batch instead of creating a duplicate.
    """
    # Check for existing batch with same idempotency key
    if request.idempotency_key:
        existing = batch_store.get_batch_by_idempotency_key(request.idempotency_key)
        if existing:
            logger.info(f"Returning existing batch {existing['id']} for idempotency key")
            return BulkOperationResponse(
                batch_id=existing["id"],
                total_items=existing["total_items"],
                status=existing["status"],
                message="Returning existing batch (idempotent request)",
                idempotency_key=request.idempotency_key
            )

    # Import here to avoid circular imports
    from celery_app.tasks.extraction import process_bulk_search

    # Create batch record
    batch = batch_store.create_batch(
        batch_type="search",
        total_items=len(request.part_numbers),
        idempotency_key=request.idempotency_key
    )

    # Queue the bulk search task
    task = process_bulk_search.delay(batch["id"], request.part_numbers)

    # Update batch with celery task ID
    batch_store.client.table("batches").update({
        "celery_task_id": task.id
    }).eq("id", batch["id"]).execute()

    logger.info(f"Started bulk search batch {batch['id']} with {len(request.part_numbers)} parts")

    return BulkOperationResponse(
        batch_id=batch["id"],
        total_items=len(request.part_numbers),
        status="processing",
        message=f"Bulk search started. Processing {len(request.part_numbers)} part numbers.",
        idempotency_key=request.idempotency_key
    )


@router.post("/bulk-publish", response_model=BulkOperationResponse)
async def bulk_publish(request: BulkPublishRequest):
    """
    Start a bulk publish operation.

    Accepts a list of part numbers (must exist in product_staging) and
    queues them for publishing to Shopify.
    """
    # Check for existing batch with same idempotency key
    if request.idempotency_key:
        existing = batch_store.get_batch_by_idempotency_key(request.idempotency_key)
        if existing:
            logger.info(f"Returning existing batch {existing['id']} for idempotency key")
            return BulkOperationResponse(
                batch_id=existing["id"],
                total_items=existing["total_items"],
                status=existing["status"],
                message="Returning existing batch (idempotent request)",
                idempotency_key=request.idempotency_key
            )

    from celery_app.tasks.publishing import publish_batch

    # Create batch record
    batch = batch_store.create_batch(
        batch_type="publish",
        total_items=len(request.part_numbers),
        idempotency_key=request.idempotency_key
    )

    # Queue the publish task
    task = publish_batch.delay(batch["id"], request.part_numbers)

    # Update batch with celery task ID
    batch_store.client.table("batches").update({
        "celery_task_id": task.id
    }).eq("id", batch["id"]).execute()

    logger.info(f"Started bulk publish batch {batch['id']} with {len(request.part_numbers)} products")

    return BulkOperationResponse(
        batch_id=batch["id"],
        total_items=len(request.part_numbers),
        status="processing",
        message=f"Bulk publish started. Publishing {len(request.part_numbers)} products to Shopify.",
        idempotency_key=request.idempotency_key
    )


@router.get("/batches", response_model=BatchListResponse)
async def list_batches(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status")
):
    """
    List all batches with pagination.

    Query parameters:
    - limit: Max batches to return (1-100, default 50)
    - offset: Number to skip for pagination
    - status: Optional filter (pending, processing, completed, failed, cancelled)
    """
    batches, total = batch_store.list_batches(limit=limit, offset=offset, status=status)

    # Convert to response models
    batch_responses = []
    for b in batches:
        progress = _calculate_progress(b)
        batch_responses.append(BatchStatusResponse(
            id=b["id"],
            batch_type=b["batch_type"],
            status=b["status"],
            total_items=b["total_items"],
            extracted_count=b["extracted_count"],
            normalized_count=b["normalized_count"],
            published_count=b["published_count"],
            failed_count=b["failed_count"],
            progress_percent=progress,
            error_message=b.get("error_message"),
            idempotency_key=b.get("idempotency_key"),
            created_at=b["created_at"],
            updated_at=b["updated_at"],
            completed_at=b.get("completed_at"),
        ))

    return BatchListResponse(batches=batch_responses, total=total)


@router.get("/batches/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(batch_id: str):
    """
    Get detailed status of a specific batch.

    Includes progress percentages and list of failed items.
    """
    batch = batch_store.get_batch(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    progress = _calculate_progress(batch)

    # Parse failed items
    failed_items = None
    if batch.get("failed_items"):
        failed_items = [
            FailedItem(
                part_number=item.get("part_number", "unknown"),
                error=item.get("error", "Unknown error"),
                timestamp=item.get("timestamp")
            )
            for item in batch["failed_items"]
        ]

    return BatchStatusResponse(
        id=batch["id"],
        batch_type=batch["batch_type"],
        status=batch["status"],
        total_items=batch["total_items"],
        extracted_count=batch["extracted_count"],
        normalized_count=batch["normalized_count"],
        published_count=batch["published_count"],
        failed_count=batch["failed_count"],
        progress_percent=progress,
        failed_items=failed_items,
        error_message=batch.get("error_message"),
        idempotency_key=batch.get("idempotency_key"),
        created_at=batch["created_at"],
        updated_at=batch["updated_at"],
        completed_at=batch.get("completed_at"),
    )


@router.delete("/batches/{batch_id}")
async def cancel_batch(batch_id: str):
    """
    Cancel a batch operation.

    Marks the batch as cancelled and attempts to revoke in-flight Celery tasks.
    Note: Already completed items cannot be rolled back.
    """
    batch = batch_store.get_batch(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel batch with status: {batch['status']}"
        )

    # Queue cancellation task
    from celery_app.tasks.batch import cancel_batch as cancel_batch_task
    cancel_batch_task.delay(batch_id)

    return {
        "message": "Batch cancellation initiated",
        "batch_id": batch_id
    }


def _calculate_progress(batch: dict) -> float:
    """Calculate progress percentage based on batch type."""
    total = batch["total_items"]
    if total == 0:
        return 0.0

    if batch["batch_type"] == "search":
        completed = batch["normalized_count"] + batch["failed_count"]
    else:  # publish
        completed = batch["published_count"] + batch["failed_count"]

    return round((completed / total) * 100, 2)
```

### 9.4 Update main.py

> **Add this to `app/main.py`**

```python
# Add import at top
from app.routes.bulk import router as bulk_router

# Add router registration (after other routers)
app.include_router(bulk_router)

# Add health endpoint
@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}
```

---

## 10. Error Handling Strategy

### 10.1 Error Categories

| Category | HTTP Code | Retry | Example |
|----------|-----------|-------|---------|
| Validation | 400 | No | Invalid part number format |
| Not Found | 404 | No | Product not in staging |
| Rate Limited | 429 | Yes | Boeing/Shopify API rate limit |
| External Error | 502 | Yes | Boeing API timeout |
| Internal Error | 500 | Yes | Unexpected exception |

### 10.2 Custom Exceptions

#### `app/core/exceptions.py`

```python
"""
Custom exception hierarchy for Boeing Data Hub.

Exceptions are categorized as:
- RetryableError: Transient errors that should trigger Celery retry
- NonRetryableError: Permanent errors that should fail immediately

This categorization allows Celery tasks to use:
- autoretry_for=(RetryableError,)
- dont_autoretry_for=(NonRetryableError,)
"""


class BoeingDataHubException(Exception):
    """Base exception for Boeing Data Hub."""
    pass


# ============================================
# RETRYABLE ERRORS - Will trigger Celery retry
# ============================================
class RetryableError(BoeingDataHubException):
    """
    Base class for errors that should trigger retry.

    Use this for transient errors where retrying might succeed:
    - Network timeouts
    - Rate limits (with backoff)
    - Temporary service unavailability
    """
    pass


class ExternalAPIError(RetryableError):
    """
    Error from external API (Boeing, Shopify).

    Typically transient - the external service might recover.
    """
    def __init__(self, service: str, message: str, status_code: int = None):
        self.service = service
        self.status_code = status_code
        super().__init__(f"{service} API error: {message}")


class RateLimitError(RetryableError):
    """
    Rate limit exceeded.

    Should retry after the specified delay.
    """
    def __init__(self, service: str, retry_after: int = 60):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"{service} rate limited. Retry after {retry_after}s")


class ConnectionTimeoutError(RetryableError):
    """Connection or timeout error - typically transient."""
    pass


class DatabaseTransientError(RetryableError):
    """
    Transient database error.

    Examples: connection pool exhausted, deadlock, temporary unavailability
    """
    pass


# ============================================
# NON-RETRYABLE ERRORS - No automatic retry
# ============================================
class NonRetryableError(BoeingDataHubException):
    """
    Base class for errors that should NOT trigger retry.

    Use this for permanent errors where retrying won't help:
    - Validation failures
    - Missing data
    - Authentication errors (need config fix)
    """
    pass


class ValidationError(NonRetryableError):
    """Invalid input data - retrying won't help."""
    pass


class BatchNotFoundError(NonRetryableError):
    """Batch not found - permanent failure."""
    pass


class ProductNotFoundError(NonRetryableError):
    """Product not found in staging - permanent failure."""
    pass


class InvalidPartNumberError(NonRetryableError):
    """Part number format is invalid."""
    pass


class AuthenticationError(NonRetryableError):
    """
    API authentication failed.

    Needs configuration fix, not retry.
    """
    pass
```

### 10.3 Using Error Categories in Tasks

```python
# Example usage in a Celery task

from app.core.exceptions import RetryableError, NonRetryableError

@celery_app.task(
    bind=True,
    base=BaseTask,
    # Only retry for RetryableError types
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    # Never retry for these
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
)
def my_task(self, ...):
    try:
        # ... task logic ...
        pass
    except NonRetryableError as e:
        # Log and fail immediately - no retry
        logger.error(f"Non-retryable error: {e}")
        # Don't re-raise - task completes as failed
    except RetryableError as e:
        # Will be auto-retried by Celery
        raise
```

---

## 11. Testing Strategy

### 11.1 Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/
│   ├── test_extraction_tasks.py
│   ├── test_normalization_tasks.py
│   ├── test_publishing_tasks.py
│   └── test_batch_store.py
├── integration/
│   └── test_bulk_flow.py
└── e2e/
    └── test_api_endpoints.py
```

### 11.2 Test Fixtures

#### `tests/conftest.py`

```python
"""
Shared test fixtures for Boeing Data Hub tests.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def mock_boeing_client():
    """Mock Boeing API client."""
    client = MagicMock()
    client.fetch_price_availability_batch = AsyncMock(return_value={
        "currency": "USD",
        "lineItems": [
            {
                "aviallPartNumber": "TEST-001",
                "name": "Test Part",
                "listPrice": 100.0,
                "quantity": 10,
            }
        ]
    })
    return client


@pytest.fixture
def mock_shopify_client():
    """Mock Shopify API client."""
    client = MagicMock()
    client.publish_product = AsyncMock(return_value={
        "product": {"id": "shopify-123"}
    })
    client.delete_product = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_batch_store():
    """Mock batch store."""
    store = MagicMock()
    store.get_batch = MagicMock(return_value={
        "id": "test-batch",
        "batch_type": "search",
        "status": "processing",
        "total_items": 10,
        "extracted_count": 0,
        "normalized_count": 0,
        "published_count": 0,
        "failed_count": 0,
    })
    store.increment_extracted = MagicMock()
    store.increment_normalized = MagicMock()
    store.increment_published = MagicMock()
    store.record_failure = MagicMock()
    store.update_status = MagicMock()
    return store


@pytest.fixture
def mock_supabase_store():
    """Mock Supabase store."""
    store = MagicMock()
    store.insert_boeing_raw_data = AsyncMock()
    store.upsert_product_staging = AsyncMock()
    store.get_product_staging_by_part_number = AsyncMock(return_value={
        "sku": "TEST-001",
        "title": "Test Part",
        "list_price": 100.0,
    })
    store.upsert_product = AsyncMock()
    return store
```

### 11.3 Example Unit Test

#### `tests/unit/test_extraction_tasks.py`

```python
"""
Unit tests for extraction tasks.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestExtractChunk:
    """Tests for extract_chunk task."""

    @patch("celery_app.tasks.extraction.get_boeing_client")
    @patch("celery_app.tasks.extraction.get_supabase_store")
    @patch("celery_app.tasks.extraction.get_batch_store")
    @patch("celery_app.tasks.normalization.normalize_chunk.delay")
    def test_extract_chunk_success(
        self,
        mock_normalize,
        mock_get_batch,
        mock_get_supabase,
        mock_get_boeing,
        mock_boeing_client,
        mock_supabase_store,
        mock_batch_store
    ):
        """Test successful extraction chains to normalization."""
        from celery_app.tasks.extraction import extract_chunk

        # Setup mocks
        mock_get_boeing.return_value = mock_boeing_client
        mock_get_supabase.return_value = mock_supabase_store
        mock_get_batch.return_value = mock_batch_store

        # Execute
        result = extract_chunk("batch-123", ["PN-001", "PN-002"])

        # Verify Boeing API was called
        mock_boeing_client.fetch_price_availability_batch.assert_called_once()

        # Verify raw data was stored
        mock_supabase_store.insert_boeing_raw_data.assert_called_once()

        # Verify progress was updated
        mock_batch_store.increment_extracted.assert_called_once_with("batch-123", 2)

        # Verify normalization was chained
        mock_normalize.assert_called_once()


    @patch("celery_app.tasks.extraction.get_boeing_client")
    @patch("celery_app.tasks.extraction.get_batch_store")
    def test_extract_chunk_records_failure_on_max_retries(
        self,
        mock_get_batch,
        mock_get_boeing,
        mock_batch_store
    ):
        """Test that failures are recorded when max retries exceeded."""
        from celery_app.tasks.extraction import extract_chunk

        # Setup mocks
        mock_boeing = MagicMock()
        mock_boeing.fetch_price_availability_batch = AsyncMock(
            side_effect=Exception("API Error")
        )
        mock_get_boeing.return_value = mock_boeing
        mock_get_batch.return_value = mock_batch_store

        # Create task instance with max retries reached
        task = extract_chunk.s("batch-123", ["PN-001"])
        task.request.retries = 3  # Max retries

        # Execute and expect exception
        with pytest.raises(Exception):
            extract_chunk("batch-123", ["PN-001"])

        # Verify failure was recorded for each part number
        mock_batch_store.record_failure.assert_called()
```

---

## 12. Deployment Checklist

### 12.1 Pre-Deployment

```
☐ All environment variables configured in `.env`
☐ Redis server installed and running
☐ Supabase `batches` table created
☐ Helper functions deployed to Supabase
☐ All dependencies installed (`pip install -r requirements.txt`)
☐ Unit tests passing
☐ Integration tests passing
```

### 12.2 Deployment Steps

```bash
# ============================================
# 1. Install Redis (Ubuntu/EC2)
# ============================================
sudo apt update
sudo apt install redis-server -y
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verify Redis
redis-cli ping  # Should return PONG

# ============================================
# 2. Install Python dependencies
# ============================================
cd backend
pip install -r requirements.txt

# ============================================
# 3. Run database migrations (Supabase SQL)
# ============================================
# Execute SQL from section 6 in Supabase dashboard:
# - Create batches table
# - Create helper functions

# ============================================
# 4. Test Celery connection
# ============================================
celery -A celery_app inspect ping

# ============================================
# 5. Start services (development)
# ============================================
# Terminal 1: FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2-5: Workers (see section 8.2)
celery -A celery_app worker --queues=extraction --concurrency=2 -n extraction@%h
celery -A celery_app worker --queues=normalization --concurrency=4 -n normalization@%h
celery -A celery_app worker --queues=publishing --concurrency=1 -n publishing@%h
celery -A celery_app worker --queues=default --concurrency=2 -n default@%h

# ============================================
# 6. Or use systemd (production - see section 8.4)
# ============================================
sudo systemctl start celery-extraction
sudo systemctl start celery-normalization
sudo systemctl start celery-publishing
```

### 12.3 Post-Deployment Verification

```
☐ FastAPI health endpoint returns OK: curl http://localhost:8000/health
☐ All Celery workers connected: celery -A celery_app inspect ping
☐ Test bulk search with small batch (5 parts)
☐ Verify batch status endpoint works
☐ Check logs for any errors
☐ Monitor Redis memory usage: redis-cli info memory
☐ Test idempotency: same request twice returns same batch
☐ Test cancellation: DELETE /api/batches/{id}
```

---

## 13. Monitoring & Observability

### 13.1 Health Endpoints

```python
# Already added in main.py

@app.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy"}
```

### 13.2 Logging Configuration

```python
# Recommended logging setup for app/main.py

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/boeing/app.log")
    ]
)
```

### 13.3 Key Metrics to Monitor

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Queue depth | Redis | > 1000 pending |
| Task failure rate | Logs | > 5% |
| Worker CPU | System | > 80% |
| Redis memory | Redis | > 80% capacity |
| API response time | FastAPI | > 5s |
| Batch completion rate | Database | < 95% |

---

## Appendix A: Quick Reference Commands

```bash
# ============================================
# CELERY COMMANDS
# ============================================

# Start all workers (development - single terminal)
celery -A celery_app worker --queues=extraction,normalization,publishing,default --concurrency=4 --loglevel=INFO

# Check worker status
celery -A celery_app inspect ping
celery -A celery_app inspect active_queues
celery -A celery_app inspect active

# Purge a queue (careful!)
celery -A celery_app purge -Q extraction

# Cancel a specific task
celery -A celery_app control revoke <task_id> --terminate

# ============================================
# REDIS COMMANDS
# ============================================

# Check Redis status
redis-cli ping
redis-cli info memory
redis-cli info clients

# Monitor Redis in real-time
redis-cli monitor

# ============================================
# SYSTEMD COMMANDS (Production)
# ============================================

# Check service status
sudo systemctl status celery-extraction
sudo systemctl status celery-normalization
sudo systemctl status celery-publishing

# View logs
sudo journalctl -u celery-extraction -f
tail -f /var/log/boeing/celery-extraction.log

# Restart services
sudo systemctl restart celery-extraction
sudo systemctl restart celery-normalization
sudo systemctl restart celery-publishing
```

---

## Appendix B: Troubleshooting

| Issue | Possible Cause | Solution |
|-------|---------------|----------|
| Workers not starting | Redis not running | `sudo systemctl start redis-server` |
| Tasks stuck in queue | Workers crashed | Restart workers, check logs |
| High memory usage | Large task payloads | Reduce BOEING_BATCH_SIZE |
| Rate limit errors | API throttling | Reduce rate_limit setting in celery_config.py |
| Database timeouts | Connection pool exhausted | Increase Supabase pool size |
| "Module not found" errors | PYTHONPATH not set | Run from backend/ directory or set PYTHONPATH |
| Tasks not being picked up | Queue name mismatch | Verify queue names match in config and worker commands |

---

## Appendix C: Performance Tuning

### For 20,000 Part Numbers

| Setting | Value | Reason |
|---------|-------|--------|
| Boeing batch size | 10-20 | Start with 10, increase if stable |
| Extraction concurrency | 2 | API rate limit |
| Normalization concurrency | 4 | CPU-bound, fast |
| Shopify concurrency | 1 | Rate limit protection |
| Redis maxmemory | 256MB | Sufficient for queues |

### Estimated Processing Time

```
20,000 parts ÷ 10 per batch = 2,000 Boeing API calls
2,000 calls ÷ 20/min rate limit = 100 minutes extraction

With chunk size of 20:
20,000 parts ÷ 20 per batch = 1,000 Boeing API calls
1,000 calls ÷ 20/min rate limit = 50 minutes extraction

Normalization: ~5 minutes (parallel with extraction)
Total search time: ~55-105 minutes depending on chunk size

Publishing (when triggered):
20,000 products ÷ 30/min = ~667 minutes = ~11 hours
```

---

## Appendix D: Code Changes Summary

### Files to CREATE

| File | Purpose |
|------|---------|
| `app/core/exceptions.py` | Custom exception hierarchy |
| `app/schemas/bulk.py` | Request/response schemas |
| `app/db/batch_store.py` | Batch CRUD operations |
| `app/routes/bulk.py` | Bulk API endpoints |
| `celery_app/__init__.py` | Celery app export |
| `celery_app/celery_config.py` | Celery configuration |
| `celery_app/tasks/__init__.py` | Task exports |
| `celery_app/tasks/base.py` | Base task class |
| `celery_app/tasks/extraction.py` | Extraction tasks |
| `celery_app/tasks/normalization.py` | Normalization tasks |
| `celery_app/tasks/publishing.py` | Publishing tasks |
| `celery_app/tasks/batch.py` | Batch management tasks |

### Files to MODIFY

| File | Changes |
|------|---------|
| `requirements.txt` | Add celery, redis |
| `.env` | Add Redis URL, batch settings |
| `app/core/config.py` | Add new settings |
| `app/main.py` | Add bulk router, health endpoint |
| `app/clients/boeing_client.py` | Add `fetch_price_availability_batch` |
| `app/clients/shopify_client.py` | Add `delete_product` method |

### SQL to Execute

| Location | Script |
|----------|--------|
| Supabase SQL Editor | Create `batches` table |
| Supabase SQL Editor | Create helper functions |

---

## Appendix E: API Quick Reference

### Bulk Search

```bash
# Start bulk search
curl -X POST http://localhost:8000/api/bulk-search \
  -H "Content-Type: application/json" \
  -d '{"part_numbers": ["PN-001", "PN-002", "PN-003"]}'

# With idempotency key
curl -X POST http://localhost:8000/api/bulk-search \
  -H "Content-Type: application/json" \
  -d '{"part_numbers": ["PN-001"], "idempotency_key": "my-unique-id-123"}'

# With text input
curl -X POST http://localhost:8000/api/bulk-search \
  -H "Content-Type: application/json" \
  -d '{"part_numbers_text": "PN-001, PN-002, PN-003"}'
```

### Bulk Publish

```bash
curl -X POST http://localhost:8000/api/bulk-publish \
  -H "Content-Type: application/json" \
  -d '{"part_numbers": ["PN-001", "PN-002"]}'
```

### Check Status

```bash
# Get batch status
curl http://localhost:8000/api/batches/{batch_id}

# List all batches
curl http://localhost:8000/api/batches

# List with filters
curl "http://localhost:8000/api/batches?status=processing&limit=10"
```

### Cancel Batch

```bash
curl -X DELETE http://localhost:8000/api/batches/{batch_id}
```

---

*Document Version: 1.1*
*Last Updated: 2025-01-26*
*For: Development Team*
