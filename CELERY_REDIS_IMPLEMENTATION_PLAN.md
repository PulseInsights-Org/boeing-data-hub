# Boeing Data Hub: Celery + Redis Implementation Plan

## Executive Summary

This document outlines the comprehensive implementation plan for integrating Celery and Redis into the Boeing Data Hub system. The goal is to enable production-ready bulk processing of up to 20,000+ part numbers with proper async task handling, retry mechanisms, and progress tracking.

---

## Table of Contents

1. [Current System Analysis](#1-current-system-analysis)
2. [Target Architecture](#2-target-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Implementation Phases](#4-implementation-phases)
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
| `requirements.txt` | Add celery, redis, flower dependencies |
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
│  │   ┌──────────────┐         ┌──────────────┐         ┌────────────┐ │   │
│  │   │   FastAPI    │         │    Redis     │         │   Flower   │ │   │
│  │   │   Server     │────────▶│   Broker     │         │  (Monitor) │ │   │
│  │   │  Port 8000   │         │  Port 6379   │         │  Port 5555 │ │   │
│  │   └──────────────┘         └──────────────┘         └────────────┘ │   │
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
│   FOR each chunk of 20 part numbers:                                         │
│     • Call Boeing API with batch of 20                                       │
│     • Store raw response in boeing_raw_data table                            │
│     • Chain to normalization: normalize_chunk.delay(batch_id, chunk, data)   │
│     • Update batch.extracted_count += 20                                     │
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
├── requirements.txt                     # MODIFIED: Add celery, redis, flower
└── supervisord.conf                     # NEW: Process management (optional)
```

### 3.3 Summary of Changes

| Location | Change Type | Files |
|----------|-------------|-------|
| `app/` | Minor edits | `main.py`, `core/config.py`, `clients/boeing_client.py` |
| `app/db/` | New file | `batch_store.py` |
| `app/routes/` | New file | `bulk.py` |
| `app/schemas/` | New file | `bulk.py` |
| `celery_app/` | New folder | Entire Celery application |
| Root | New/Modified | `requirements.txt`, `supervisord.conf`|

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
    └── batch.py             # check_completion, cleanup_stale
```

**Why this structure:**
- Flat task files (no nested `shared/`, `helpers/` folders) - simpler for your codebase size
- Clear naming: `extraction.py` instead of `extraction_tasks.py` (shorter)
- Single `base.py` for shared task logic instead of separate `shared/` folder
- All configuration in one `celery_config.py` file

---

## 4. Implementation Checklist (Single Phase)

All files should be created in this order. Each step depends on previous steps being complete.

### Step 1: Dependencies & Configuration

| # | Action | File | Description |
|---|--------|------|-------------|
| 1 | MODIFY | `requirements.txt` | Add `celery[redis]==5.3.6`, `redis==5.0.1`, `flower==2.0.1` |
| 2 | MODIFY | `.env` | Add `REDIS_URL`, `BOEING_BATCH_SIZE`, `MAX_BULK_SEARCH_SIZE`, `MAX_BULK_PUBLISH_SIZE` |
| 3 | MODIFY | `app/core/config.py` | Add Redis, Celery, and batch processing settings |
| 4 | CREATE | `app/core/exceptions.py` | Add `RetryableError`, `NonRetryableError` hierarchy |

### Step 2: Database Schema

| # | Action | Location | Description |
|---|--------|----------|-------------|
| 5 | CREATE | Supabase SQL Editor | Create `batches` table with `idempotency_key` column |
| 6 | CREATE | Supabase SQL Editor | Create helper functions (`increment_batch_counter`, `record_batch_failure`, `update_batch_status`) |

### Step 3: Schemas & Data Layer

| # | Action | File | Description |
|---|--------|------|-------------|
| 7 | CREATE | `app/schemas/bulk.py` | Request/response models with idempotency support |
| 8 | CREATE | `app/db/batch_store.py` | Batch progress tracking operations |

### Step 4: Celery Infrastructure

| # | Action | File | Description |
|---|--------|------|-------------|
| 9 | CREATE | `celery_app/__init__.py` | Export `celery_app` instance |
| 10 | CREATE | `celery_app/celery_config.py` | Broker, queues, rate limits, routing |
| 11 | CREATE | `celery_app/tasks/__init__.py` | Export all tasks |
| 12 | CREATE | `celery_app/tasks/base.py` | `BaseTask` class + dependency helpers |

### Step 5: Worker Tasks

| # | Action | File | Description |
|---|--------|------|-------------|
| 13 | CREATE | `celery_app/tasks/extraction.py` | `process_bulk_search`, `extract_chunk` |
| 14 | CREATE | `celery_app/tasks/normalization.py` | `normalize_chunk` |
| 15 | CREATE | `celery_app/tasks/publishing.py` | `publish_batch`, `publish_product` (with compensation) |
| 16 | CREATE | `celery_app/tasks/batch.py` | `check_batch_completion`, `cancel_batch`, `cleanup_stale_batches` |

### Step 6: API & Integration

| # | Action | File | Description |
|---|--------|------|-------------|
| 17 | CREATE | `app/routes/bulk.py` | Bulk operation endpoints |
| 18 | MODIFY | `app/main.py` | Add bulk router |
| 19 | MODIFY | `app/clients/boeing_client.py` | Add `fetch_price_availability_batch` method |

### Validation Checklist

After completing all steps, verify:

```
□ Redis connection works: redis-cli ping
□ Celery app starts: celery -A celery_app worker --loglevel=INFO
□ Batches table exists in Supabase
□ POST /api/bulk-search returns batch_id
□ GET /api/batches/{id} returns status
□ Workers process tasks from queues
□ Retry logic triggers on simulated failure
□ Rate limiting respects Boeing/Shopify limits
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

    # Task routing - 3 queues for pipeline stages
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
from celery_app.tasks.batch import check_batch_completion

__all__ = [
    "process_bulk_search",
    "extract_chunk",
    "normalize_chunk",
    "publish_batch",
    "publish_product",
    "check_batch_completion",
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
from celery import Task
from typing import Any, Dict
import logging

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

    _dependencies = None

    @property
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
# Dependency helpers (inline in base.py)
# ============================================
_dependencies = None

def get_dependencies():
    """Lazy load dependencies. Called after fork so each worker gets own instances."""
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
    return get_dependencies()["boeing_client"]

def get_shopify_client():
    return get_dependencies()["shopify_client"]

def get_supabase_store():
    return get_dependencies()["supabase_store"]

def get_batch_store():
    return get_dependencies()["batch_store"]
```

#### `celery_app/tasks/extraction.py`
```python
"""
Extraction tasks for fetching data from Boeing API.
"""
import logging
import asyncio
import os
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import BaseTask, get_boeing_client, get_supabase_store, get_batch_store

logger = logging.getLogger(__name__)

# Configurable chunk size - start with 10 for safety, tune via env var
BOEING_BATCH_SIZE = int(os.getenv("BOEING_BATCH_SIZE", "10"))


def run_async(coro):
    """Run async function in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, base=BaseTask, name="celery_app.tasks.extraction.process_bulk_search", max_retries=0)
def process_bulk_search(self, batch_id: str, part_numbers: List[str]):
    """
    Process a bulk search request.

    Splits part numbers into chunks and queues extraction tasks.

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers to search
    """
    logger.info(f"Starting bulk search for batch {batch_id} with {len(part_numbers)} parts (chunk size: {BOEING_BATCH_SIZE})")

    batch_store = get_batch_store()

    try:
        # Split into chunks using configurable size
        chunks = [
            part_numbers[i:i + BOEING_BATCH_SIZE]
            for i in range(0, len(part_numbers), BOEING_BATCH_SIZE)
        ]

        logger.info(f"Split into {len(chunks)} chunks of {BOEING_BATCH_SIZE} parts each")

        # Queue extraction for each chunk
        for i, chunk in enumerate(chunks):
            extract_chunk.delay(batch_id, chunk, chunk_index=i, total_chunks=len(chunks))

        logger.info(f"Queued {len(chunks)} extraction tasks for batch {batch_id}")

    except Exception as e:
        logger.error(f"Bulk search failed for batch {batch_id}: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.extraction.extract_chunk",
    autoretry_for=(Exception,),
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
        part_numbers: Part numbers to extract
        chunk_index: Current chunk index (for logging)
        total_chunks: Total number of chunks (for logging)
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

    except Exception as e:
        logger.error(f"Extraction failed for chunk: {e}")

        # If max retries exceeded, record failures
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
"""
import logging
import asyncio
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import BaseTask, get_supabase_store, get_batch_store
from app.utils.boeing_normalize import normalize_boeing_payload

logger = logging.getLogger(__name__)


def run_async(coro):
    """Run async function in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, base=BaseTask, name="celery_app.tasks.normalization.normalize_chunk", max_retries=3)
def normalize_chunk(self, batch_id: str, part_numbers: List[str], raw_response: Dict[str, Any]):
    """Normalize a chunk of products from raw Boeing data."""
    logger.info(f"Normalizing {len(part_numbers)} parts for batch {batch_id}")

    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    line_items = raw_response.get("lineItems", [])
    currency = raw_response.get("currency")

    # Create lookup by part number
    item_lookup = {item.get("aviallPartNumber"): item for item in line_items if item.get("aviallPartNumber")}
    normalized_count = 0

    for pn in part_numbers:
        try:
            item = item_lookup.get(pn)
            if not item:
                batch_store.record_failure(batch_id, pn, "Not found in Boeing response")
                continue

            normalized_list = normalize_boeing_payload(pn, {"lineItems": [item], "currency": currency})
            if normalized_list:
                run_async(supabase_store.upsert_product_staging(normalized_list))
                normalized_count += 1
            else:
                batch_store.record_failure(batch_id, pn, "Normalization produced no results")
        except Exception as e:
            logger.error(f"Failed to normalize {pn}: {e}")
            batch_store.record_failure(batch_id, pn, f"Normalization error: {e}")

    if normalized_count > 0:
        batch_store.increment_normalized(batch_id, normalized_count)

    # Check if batch is complete
    from celery_app.tasks.batch import check_batch_completion
    check_batch_completion.delay(batch_id)

    logger.info(f"Normalized {normalized_count}/{len(part_numbers)} parts")
```

#### `celery_app/tasks/publishing.py`
```python
"""
Shopify publishing tasks with transaction compensation.
"""
import logging
import asyncio
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import BaseTask, get_shopify_client, get_supabase_store, get_batch_store

logger = logging.getLogger(__name__)
FALLBACK_IMAGE = "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto"


def run_async(coro):
    """Run async function in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, base=BaseTask, name="celery_app.tasks.publishing.publish_batch", max_retries=0)
def publish_batch(self, batch_id: str, part_numbers: List[str]):
    """Orchestrate publishing a batch of products to Shopify."""
    logger.info(f"Starting publish batch {batch_id} with {len(part_numbers)} products")
    batch_store = get_batch_store()

    try:
        for pn in part_numbers:
            publish_product.delay(batch_id, pn)
        logger.info(f"Queued {len(part_numbers)} publish tasks")
    except Exception as e:
        logger.error(f"Publish batch failed: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True, base=BaseTask, name="celery_app.tasks.publishing.publish_product",
    autoretry_for=(Exception,), retry_backoff=True, max_retries=3, rate_limit="30/m"
)
def publish_product(self, batch_id: str, part_number: str):
    """
    Publish a single product to Shopify with transaction compensation.

    If DB save fails after Shopify publish succeeds, we attempt to delete
    the orphaned Shopify product to maintain consistency.
    """
    logger.info(f"Publishing {part_number} to Shopify")

    shopify_client = get_shopify_client()
    supabase_store = get_supabase_store()
    batch_store = get_batch_store()
    shopify_product_id = None  # Track for compensation

    try:
        record = run_async(supabase_store.get_product_staging_by_part_number(part_number))
        if not record:
            raise ValueError(f"Product {part_number} not found in staging")

        # Handle image upload
        boeing_image_url = record.get("boeing_image_url") or record.get("boeing_thumbnail_url")
        if boeing_image_url:
            try:
                image_url, image_path = run_async(supabase_store.upload_image_from_url(boeing_image_url, part_number))
                record["image_url"] = image_url
                record["image_path"] = image_path
                run_async(supabase_store.update_product_staging_image(part_number, image_url, image_path))
            except Exception as img_err:
                logger.warning(f"Image upload failed, using placeholder: {img_err}")
                record["image_url"] = FALLBACK_IMAGE
        else:
            record["image_url"] = FALLBACK_IMAGE

        # Prepare and publish to Shopify
        record = _prepare_shopify_record(record)
        result = run_async(shopify_client.publish_product(record))

        shopify_product_id = result.get("product", {}).get("id")
        if not shopify_product_id:
            raise ValueError("Shopify did not return product ID")

        # CRITICAL: Save to database - if this fails, compensate by deleting from Shopify
        try:
            run_async(supabase_store.upsert_product(record, shopify_product_id=str(shopify_product_id)))
        except Exception as db_err:
            logger.error(f"DB save failed after Shopify publish. Compensating by deleting Shopify product {shopify_product_id}")
            try:
                run_async(shopify_client.delete_product(shopify_product_id))
                logger.info(f"Compensation successful: deleted orphaned Shopify product {shopify_product_id}")
            except Exception as rollback_err:
                # CRITICAL: Manual reconciliation needed
                logger.critical(
                    f"ORPHANED PRODUCT: Shopify ID {shopify_product_id} for part {part_number}. "
                    f"DB error: {db_err}, Rollback error: {rollback_err}"
                )
                batch_store.record_failure(batch_id, part_number, f"ORPHANED: Shopify {shopify_product_id}")
            raise db_err

        batch_store.increment_published(batch_id)

        from celery_app.tasks.batch import check_batch_completion
        check_batch_completion.delay(batch_id)

        logger.info(f"Published {part_number} -> Shopify ID: {shopify_product_id}")
        return {"success": True, "shopify_product_id": str(shopify_product_id)}

    except Exception as e:
        logger.error(f"Failed to publish {part_number}: {e}")
        if self.request.retries >= self.max_retries:
            batch_store.record_failure(batch_id, part_number, str(e))
            from celery_app.tasks.batch import check_batch_completion
            check_batch_completion.delay(batch_id)
        raise


def _prepare_shopify_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare record with Shopify-specific fields."""
    record.setdefault("shopify", {})
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
Batch orchestration tasks - check completion, cleanup stale batches.
"""
import logging
from datetime import datetime, timedelta

from celery_app.celery_config import celery_app
from celery_app.tasks.base import BaseTask, get_batch_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, base=BaseTask, name="celery_app.tasks.batch.check_batch_completion")
def check_batch_completion(self, batch_id: str):
    """Check if a batch has completed and update status."""
    logger.info(f"Checking completion for batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found")
        return

    if batch["status"] in ("completed", "failed", "cancelled"):
        return

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


@celery_app.task(bind=True, base=BaseTask, name="celery_app.tasks.batch.cleanup_stale_batches")
def cleanup_stale_batches(self, max_age_hours: int = 24):
    """Mark stuck batches as failed after timeout."""
    logger.info(f"Cleaning up stale batches older than {max_age_hours} hours")

    batch_store = get_batch_store()
    active_batches = batch_store.get_active_batches()
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    for batch in active_batches:
        created_at = datetime.fromisoformat(batch["created_at"].replace("Z", "+00:00"))
        if created_at.replace(tzinfo=None) < cutoff:
            batch_store.update_status(batch["id"], "failed", f"Timed out after {max_age_hours} hours")
            logger.warning(f"Marked batch {batch['id']} as failed (timed out)")


@celery_app.task(bind=True, base=BaseTask, name="celery_app.tasks.batch.cancel_batch")
def cancel_batch(self, batch_id: str):
    """
    Cancel a batch and revoke all in-flight Celery tasks.

    This attempts to terminate any tasks currently running for this batch.
    Note: Tasks already completed cannot be undone.
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
            from celery_app.celery_config import celery_app
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

    return {"success": True, "batch_id": batch_id, "status": "cancelled"}
```

**Note:** Health check tasks are optional and can be added later if needed. The core functionality is covered by the four task files above.

---

## 6. Database Schema Changes

### 6.1 New Table: batches

```sql
-- Create batches table for tracking bulk operations
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

    -- Error tracking
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

```sql
-- Function: Atomically increment a batch counter
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


-- Function: Record a failed item
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


-- Function: Update batch status
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

```bash
# .env additions

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
# Chunk size for Boeing API calls (start with 10, can increase to 20)
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

```python
# app/core/config.py - additions

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
    boeing_batch_size: int = int(os.getenv("BOEING_BATCH_SIZE", "10"))  # Start with 10, tune to 20 if stable
    max_bulk_search_size: int = int(os.getenv("MAX_BULK_SEARCH_SIZE", "50000"))
    max_bulk_publish_size: int = int(os.getenv("MAX_BULK_PUBLISH_SIZE", "10000"))

    # Rate limits
    boeing_api_rate_limit: str = os.getenv("BOEING_API_RATE_LIMIT", "20/m")
    shopify_api_rate_limit: str = os.getenv("SHOPIFY_API_RATE_LIMIT", "30/m")
```

### 7.3 Requirements Update

```txt
# requirements.txt - additions

# Async task queue
celery[redis]==5.3.6
redis==5.0.1

# Monitoring
flower==2.0.1

# Process management (optional, for development)
honcho==1.1.0
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
# Extraction workers (2 concurrent)
celery -A celery_app worker \
    --queues=extraction \
    --concurrency=2 \
    --loglevel=INFO \
    -n extraction@%h

# Normalization workers (4 concurrent)
celery -A celery_app worker \
    --queues=normalization \
    --concurrency=4 \
    --loglevel=INFO \
    -n normalization@%h

# Publishing worker (1 concurrent - rate limited)
celery -A celery_app worker \
    --queues=publishing \
    --concurrency=1 \
    --loglevel=INFO \
    -n publishing@%h

# Default workers (batch tasks)
celery -A celery_app worker \
    --queues=default \
    --concurrency=2 \
    --loglevel=INFO \
    -n default@%h

# Flower monitoring (optional)
celery -A celery_app flower --port=5555
```

### 8.3 Supervisor Configuration

```ini
# supervisord.conf

[supervisord]
nodaemon=true
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid
childlogdir=/var/log/supervisor

[unix_http_server]
file=/var/run/supervisor.sock
chmod=0700

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock

# FastAPI Application
[program:fastapi]
command=uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
directory=/app/backend
user=app
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/boeing/fastapi.log
environment=PYTHONPATH="/app/backend"

# Celery Extraction Workers
[program:celery-extraction]
command=celery -A celery_app worker --queues=extraction --concurrency=2 --loglevel=INFO -n extraction@%%h
directory=/app/backend
user=app
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/boeing/celery-extraction.log
environment=PYTHONPATH="/app/backend"
stopwaitsecs=600
stopsignal=TERM

# Celery Normalization Workers
[program:celery-normalization]
command=celery -A celery_app worker --queues=normalization --concurrency=4 --loglevel=INFO -n normalization@%%h
directory=/app/backend
user=app
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/boeing/celery-normalization.log
environment=PYTHONPATH="/app/backend"
stopwaitsecs=600
stopsignal=TERM

# Celery Publishing Worker
[program:celery-publishing]
command=celery -A celery_app worker --queues=publishing --concurrency=1 --loglevel=INFO -n publishing@%%h
directory=/app/backend
user=app
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/boeing/celery-publishing.log
environment=PYTHONPATH="/app/backend"
stopwaitsecs=600
stopsignal=TERM

# Celery Default Workers
[program:celery-default]
command=celery -A celery_app worker --queues=default --concurrency=2 --loglevel=INFO -n default@%%h
directory=/app/backend
user=app
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/boeing/celery-default.log
environment=PYTHONPATH="/app/backend"
stopwaitsecs=600
stopsignal=TERM

# Flower Monitoring (Optional)
[program:flower]
command=celery -A celery_app flower --port=5555
directory=/app/backend
user=app
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/boeing/flower.log
environment=PYTHONPATH="/app/backend"

# Group all Celery workers
[group:celery-workers]
programs=celery-extraction,celery-normalization,celery-publishing,celery-default
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

```python
# app/schemas/bulk.py

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
    """Request to start a bulk publish operation."""
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
    idempotency_key: Optional[str] = None  # Echo back for client tracking


class FailedItem(BaseModel):
    """Details of a failed item."""
    part_number: str
    error: str
    timestamp: Optional[datetime] = None


class BatchStatusResponse(BaseModel):
    """Detailed batch status response."""
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

### 10.2 Retry Configuration

```python
# Default retry settings for external API calls
RETRY_CONFIG = {
    "autoretry_for": (
        ConnectionError,
        TimeoutError,
        HTTPException,  # 5xx errors
    ),
    "retry_backoff": True,
    "retry_backoff_max": 300,  # 5 minutes
    "retry_jitter": True,
    "max_retries": 3,
}
```

### 10.3 Custom Exceptions

```python
# app/core/exceptions.py

class BoeingDataHubException(Exception):
    """Base exception for Boeing Data Hub."""
    pass


# ============================================
# RETRYABLE ERRORS - Will trigger Celery retry
# ============================================
class RetryableError(BoeingDataHubException):
    """Base class for errors that should trigger retry."""
    pass


class ExternalAPIError(RetryableError):
    """Error from external API (Boeing, Shopify) - typically transient."""
    def __init__(self, service: str, message: str, status_code: int = None):
        self.service = service
        self.status_code = status_code
        super().__init__(f"{service} API error: {message}")


class RateLimitError(RetryableError):
    """Rate limit exceeded - should retry after delay."""
    def __init__(self, service: str, retry_after: int = 60):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"{service} rate limited. Retry after {retry_after}s")


class ConnectionTimeoutError(RetryableError):
    """Connection or timeout error - typically transient."""
    pass


class DatabaseTransientError(RetryableError):
    """Transient database error (connection pool, deadlock)."""
    pass


# ============================================
# NON-RETRYABLE ERRORS - No automatic retry
# ============================================
class NonRetryableError(BoeingDataHubException):
    """Base class for errors that should NOT trigger retry."""
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
    """API authentication failed - needs config fix, not retry."""
    pass
```

### 10.4 Using Error Categories in Tasks

```python
# In celery tasks, configure retry behavior based on error type:

from app.core.exceptions import RetryableError, NonRetryableError

@celery_app.task(
    bind=True,
    base=BaseTask,
    # Only retry for RetryableError types
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    # Never retry for these
    dont_autoretry_for=(NonRetryableError, ValidationError, AuthenticationError),
    retry_backoff=True,
    max_retries=3,
)
def extract_chunk(self, batch_id: str, part_numbers: List[str], ...):
    try:
        # ... task logic ...
    except NonRetryableError as e:
        # Log and fail immediately - no retry
        logger.error(f"Non-retryable error: {e}")
        batch_store.record_failure(batch_id, part_number, str(e))
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
│   ├── test_shopify_tasks.py
│   └── test_batch_store.py
├── integration/
│   └── test_bulk_flow.py
└── e2e/
    └── test_api_endpoints.py
```

### 11.2 Test Fixtures

```python
# tests/conftest.py

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
def mock_batch_store():
    """Mock batch store."""
    store = MagicMock()
    store.get_batch = MagicMock(return_value={
        "id": "test-batch",
        "status": "processing",
        "total_items": 10,
        "extracted_count": 0,
        "normalized_count": 0,
        "failed_count": 0,
    })
    return store
```

### 11.3 Example Unit Test

```python
# tests/unit/test_extraction_tasks.py

import pytest
from unittest.mock import patch, MagicMock

from celery_app.tasks.extraction_tasks import extract_chunk


class TestExtractChunk:
    """Tests for extract_chunk task."""

    @patch("celery_app.tasks.extraction_tasks.get_boeing_client")
    @patch("celery_app.tasks.extraction_tasks.get_batch_store")
    def test_extract_chunk_success(self, mock_get_batch, mock_get_boeing):
        """Test successful extraction."""
        # Setup mocks
        mock_boeing = MagicMock()
        mock_boeing.fetch_price_availability_batch = AsyncMock(
            return_value={"lineItems": [], "currency": "USD"}
        )
        mock_get_boeing.return_value = mock_boeing

        # Execute
        result = extract_chunk("batch-123", ["PN-001", "PN-002"])

        # Verify
        mock_boeing.fetch_price_availability_batch.assert_called_once()
```

---

## 12. Deployment Checklist

### 12.1 Pre-Deployment

- [ ] All environment variables configured in `.env`
- [ ] Redis server installed and running
- [ ] Supabase `batches` table created
- [ ] Helper functions deployed to Supabase
- [ ] All dependencies installed (`pip install -r requirements.txt`)
- [ ] Unit tests passing
- [ ] Integration tests passing

### 12.2 Deployment Steps

```bash
# 1. Install Redis (if not already installed)
sudo apt update
sudo apt install redis-server -y
sudo systemctl enable redis-server
sudo systemctl start redis-server

# 2. Verify Redis
redis-cli ping  # Should return PONG

# 3. Install Python dependencies
cd backend
pip install -r requirements.txt

# 4. Run database migrations (Supabase SQL)
# Execute SQL from section 6 in Supabase dashboard

# 5. Test Celery connection
celery -A celery_app inspect ping

# 6. Start workers (development)
# Terminal 1: FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2-5: Workers
celery -A celery_app worker --queues=extraction --concurrency=2 -n extraction@%h
celery -A celery_app worker --queues=normalization --concurrency=4 -n normalization@%h
celery -A celery_app worker --queues=shopify --concurrency=1 -n shopify@%h
celery -A celery_app worker --queues=default --concurrency=2 -n default@%h

# 7. Or use supervisor (production)
supervisord -c supervisord.conf
```

### 12.3 Post-Deployment Verification

- [ ] FastAPI health endpoint returns OK
- [ ] All Celery workers connected (check Flower)
- [ ] Test bulk search with small batch (5 parts)
- [ ] Verify batch status endpoint works
- [ ] Check logs for any errors
- [ ] Monitor Redis memory usage

---

## 13. Monitoring & Observability

### 13.1 Flower Dashboard

Access Flower at `http://localhost:5555` to monitor:
- Active workers
- Task queues
- Task history
- Worker stats

### 13.2 Health Endpoints

```python
# Add to app/main.py

@app.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy"}


@app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check including dependencies."""
    from celery_app.tasks.health_tasks import check_system_health
    result = check_system_health.delay()
    return result.get(timeout=10)
```

### 13.3 Logging Configuration

```python
# Recommended logging format
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        },
        "json": {
            "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "/var/log/boeing/app.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "formatter": "json",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console", "file"],
    },
}
```

### 13.4 Key Metrics to Monitor

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Queue depth | Redis/Flower | > 1000 pending |
| Task failure rate | Flower | > 5% |
| Worker CPU | System | > 80% |
| Redis memory | Redis | > 80% capacity |
| API response time | FastAPI | > 5s |
| Batch completion rate | Database | < 95% |

---

## Appendix A: Quick Reference Commands

```bash
# Start all workers (development)
make workers

# Stop all workers
make workers-stop

# View worker logs
tail -f /var/log/boeing/celery-*.log

# Check queue status
celery -A celery_app inspect active_queues

# Purge a queue (careful!)
celery -A celery_app purge -Q extraction

# Cancel a specific task
celery -A celery_app control revoke <task_id> --terminate

# Scale workers
supervisorctl update
supervisorctl start celery-workers:*

# Check Redis memory
redis-cli info memory
```

---

## Appendix B: Troubleshooting

| Issue | Possible Cause | Solution |
|-------|---------------|----------|
| Workers not starting | Redis not running | `sudo systemctl start redis-server` |
| Tasks stuck in queue | Workers crashed | Restart workers, check logs |
| High memory usage | Large task payloads | Reduce batch size |
| Rate limit errors | API throttling | Reduce rate_limit setting |
| Database timeouts | Connection pool exhausted | Increase pool size |

---

## Appendix C: Performance Tuning

### For 20,000 Part Numbers

| Setting | Value | Reason |
|---------|-------|--------|
| Boeing batch size | 20 | API optimal batch |
| Extraction concurrency | 2 | API rate limit |
| Normalization concurrency | 4 | CPU-bound, fast |
| Shopify concurrency | 1 | Rate limit protection |
| Redis maxmemory | 256MB | Sufficient for queues |

### Estimated Processing Time

```
20,000 parts ÷ 20 per batch = 1,000 Boeing API calls
1,000 calls ÷ 20/min rate limit = 50 minutes extraction
Normalization: ~5 minutes (parallel with extraction)
Total search time: ~55 minutes

Publishing (when triggered):
20,000 products ÷ 30/min = ~667 minutes = ~11 hours
```

---

---

## Appendix D: Tech Review Improvements (v1.1)

This section documents the improvements made after senior tech review.

### Summary of Changes

| Area | Issue | Resolution |
|------|-------|------------|
| **Publishing** | Transaction atomicity missing | Added Shopify delete compensation when DB save fails |
| **Imports** | Duplicate imports in publishing.py | Removed dead import block |
| **Task Names** | Mismatch (`extraction_tasks` vs `extraction`) | Fixed all task names to use correct module names |
| **Chunk Size** | Hardcoded to 20 | Made configurable via `BOEING_BATCH_SIZE` env var (default: 10) |
| **Idempotency** | Missing duplicate prevention | Added `idempotency_key` to requests and batches table |
| **Error Handling** | No error categorization | Added `RetryableError` vs `NonRetryableError` hierarchy |
| **Cancellation** | No way to cancel batches | Added `cancel_batch` task with Celery `revoke()` |
| **Input Options** | Only list input supported | Added `part_numbers_text` for comma/newline separated input |
| **Limits** | Hardcoded max sizes | Made configurable via `MAX_BULK_SEARCH_SIZE`, `MAX_BULK_PUBLISH_SIZE` |

### Key Production Safety Features Added

1. **Transaction Compensation (Saga Pattern)**
   - If Shopify publish succeeds but DB save fails, automatically delete the orphaned Shopify product
   - Critical failures logged with `ORPHANED PRODUCT` prefix for manual reconciliation

2. **Idempotency Keys**
   - Clients can provide a UUID to prevent duplicate batch creation on network retries
   - Unique constraint on `idempotency_key` column

3. **Error Categorization**
   - `RetryableError`: Connection timeouts, rate limits, transient DB errors → auto-retry
   - `NonRetryableError`: Validation errors, auth failures, not found → fail immediately

4. **Configurable Chunk Size**
   - Start with 10 parts per Boeing API call for safety
   - Tune to 20 via `BOEING_BATCH_SIZE` env var after stability is confirmed
   - Smaller chunks = faster retries on failure, less data loss per failed chunk

> **Note:** The implementation order is now in **Section 4: Implementation Checklist (Single Phase)**

---

*Document Version: 1.1*
*Last Updated: 2025-01-26*
*Author: System Architecture Team*
*Reviewed By: Senior Tech Lead*
