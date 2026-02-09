"""
Celery application configuration.
Configures Redis broker, task queues, rate limiting, and retry policies.

Note: On Windows, Celery's prefork pool doesn't work properly.
Use --pool=solo or --pool=threads on Windows.

=============================================================================
RUNNING WORKERS (Separate Terminals for Better Log Visibility)
=============================================================================

Option A: ALL QUEUES IN ONE TERMINAL (harder to read logs)
    celery -A celery_app worker --pool=solo -Q extraction,normalization,publishing,default,sync_boeing,sync_shopify -l info

Option B: SEPARATE TERMINALS (recommended for debugging)

    Terminal 1 - Extraction & Normalization:
        celery -A celery_app worker --pool=solo -Q extraction,normalization -l info -n extract@%h

    Terminal 2 - Publishing:
        celery -A celery_app worker --pool=solo -Q publishing -l info -n publish@%h

    Terminal 3 - Sync (Boeing + Shopify):
        celery -A celery_app worker --pool=solo -Q sync_boeing,sync_shopify --concurrency=1 -l info -n sync@%h

    Terminal 4 - Default (dispatchers, batch tasks):
        celery -A celery_app worker --pool=solo -Q default -l info -n default@%h

    Terminal 5 - Celery Beat (scheduler):
        celery -A celery_app beat -l info

The -n flag gives each worker a unique name that appears in logs.

=============================================================================
ENVIRONMENT VARIABLES
=============================================================================
    SYNC_MODE: "production" or "testing" (default: production)
    SYNC_DISPATCH_MINUTE: Minute of each hour to run sync (default: 45)
    SYNC_RETRY_HOURS: Hours between retry attempts (default: 4)
    SYNC_CLEANUP_HOUR: Hour (UTC) to run daily cleanup (default: 0)
    BOEING_RATE_LIMIT: Boeing API calls per minute (default: 2)
    SHOPIFY_RATE_LIMIT: Shopify API calls per minute (default: 30)
"""
import logging
from celery import Celery
from celery.schedules import crontab
from kombu import Queue
import os
import platform
from dotenv import load_dotenv
load_dotenv()

# Configure logging for this module
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Detect Windows platform for pool configuration
IS_WINDOWS = platform.system() == "Windows"

# =============================================================================
# SYNC SCHEDULER CONFIGURATION (via environment variables)
# =============================================================================

# SYNC_MODE: "production" or "testing"
# - production: Uses hour buckets (0-23), dispatch at :45 of each hour
# - testing: Uses minute buckets (0-5), dispatch every 10 minutes
SYNC_MODE = os.getenv("SYNC_MODE", "production")

# When to run sync dispatch
# Options:
#   - "45" = runs at minute 45 of every hour (production default)
#   - "*/10" = runs every 10 minutes (testing mode)
#   - "*/5" = runs every 5 minutes (aggressive testing)
# Default: "45" for production, "*/10" for testing
SYNC_DISPATCH_MINUTE = os.getenv(
    "SYNC_DISPATCH_MINUTE",
    "*/10" if SYNC_MODE == "testing" else "45"
)

# Testing mode: Number of minute buckets (6 buckets = 10-min intervals)
SYNC_TEST_BUCKET_COUNT = int(os.getenv("SYNC_TEST_BUCKET_COUNT", "6"))

# Hours between retry attempts for failed products
# Default: 4 (retries at 0:15, 4:15, 8:15, 12:15, 16:15, 20:15)
SYNC_RETRY_HOURS = int(os.getenv("SYNC_RETRY_HOURS", "4"))

# Hour (UTC) to run daily cleanup task
# Default: 0 (midnight UTC)
SYNC_CLEANUP_HOUR = int(os.getenv("SYNC_CLEANUP_HOUR", "0"))

# Boeing API rate limit (requests per minute)
# Default: 2 (Boeing's actual limit)
BOEING_RATE_LIMIT = os.getenv("BOEING_RATE_LIMIT", "2/m")

# Shopify API rate limit (requests per minute)
# Default: 30
SHOPIFY_RATE_LIMIT = os.getenv("SHOPIFY_RATE_LIMIT", "30/m")

# =============================================================================
# LOG SYNC MODE CONFIGURATION AT STARTUP
# =============================================================================
def _log_sync_mode_config():
    """Log sync scheduler configuration at startup."""
    border = "=" * 60
    if SYNC_MODE == "testing":
        print(f"\n{border}")
        print("  🧪 SYNC SCHEDULER: TESTING MODE ACTIVE")
        print(border)
        print(f"  • Bucket type: MINUTE buckets (0-{SYNC_TEST_BUCKET_COUNT - 1})")
        print(f"  • Main sync (dispatch-hourly-sync): Every 10 minutes (crontab: {SYNC_DISPATCH_MINUTE})")
        print(f"  • Retry sync (dispatch-retry-sync): Every {SYNC_RETRY_HOURS} hours at minute :15")
        print(f"  • Daily cleanup (end-of-day-cleanup): {SYNC_CLEANUP_HOUR}:00 UTC")
        print(f"  • Sync window check: DISABLED (processes immediately)")
        print(f"  • Bucket count: {SYNC_TEST_BUCKET_COUNT}")
        print(border)
        print(f"  ⚠️  Note: Retry sync still runs every {SYNC_RETRY_HOURS} hours (not adapted to testing mode)")
        print("  ⚠️  Switch to SYNC_MODE=production for hourly syncs")
        print(f"{border}\n")
    else:
        print(f"\n{border}")
        print("  🚀 SYNC SCHEDULER: PRODUCTION MODE")
        print(border)
        print(f"  • Bucket type: HOUR buckets (0-23)")
        print(f"  • Main sync (dispatch-hourly-sync): At minute {SYNC_DISPATCH_MINUTE} of each hour")
        print(f"  • Retry sync (dispatch-retry-sync): Every {SYNC_RETRY_HOURS} hours at minute :15")
        print(f"  • Daily cleanup (end-of-day-cleanup): {SYNC_CLEANUP_HOUR}:00 UTC")
        print(f"  • Sync window check: ENABLED (only at :45 mark)")
        print(border)
        print("  ℹ️  Set SYNC_MODE=testing for faster 10-minute intervals")
        print(f"{border}\n")

# Log configuration when module is loaded
_log_sync_mode_config()

celery_app = Celery(
    "boeing_data_hub",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "celery_app.tasks.extraction",
        "celery_app.tasks.normalization",
        "celery_app.tasks.publishing",
        "celery_app.tasks.batch",
        "celery_app.tasks.sync_dispatcher",
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

    # Task routing - 6 queues for pipeline stages + sync scheduler
    task_queues=(
        Queue("extraction"),
        Queue("normalization"),
        Queue("publishing"),
        Queue("default"),
        Queue("sync_boeing"),   # Boeing API sync (rate limited: 2/min)
        Queue("sync_shopify"),  # Shopify updates (rate limited: 30/min)
    ),
    task_routes={
        "celery_app.tasks.extraction.*": {"queue": "extraction"},
        "celery_app.tasks.normalization.*": {"queue": "normalization"},
        "celery_app.tasks.publishing.*": {"queue": "publishing"},
        "celery_app.tasks.batch.*": {"queue": "default"},
        # Sync dispatchers run on default queue
        "celery_app.tasks.sync_dispatcher.dispatch_hourly_sync": {"queue": "default"},
        "celery_app.tasks.sync_dispatcher.dispatch_retry_sync": {"queue": "default"},
        "celery_app.tasks.sync_dispatcher.end_of_day_cleanup": {"queue": "default"},
        # Boeing sync tasks on dedicated queue for rate limiting
        "celery_app.tasks.sync_dispatcher.sync_boeing_batch": {"queue": "sync_boeing"},
        "celery_app.tasks.sync_dispatcher.sync_single_product_immediate": {"queue": "sync_boeing"},
        # Shopify sync tasks on dedicated queue
        "celery_app.tasks.sync_dispatcher.sync_shopify_product": {"queue": "sync_shopify"},
    },

    # Rate limiting (configurable via env)
    task_annotations={
        "celery_app.tasks.extraction.extract_chunk": {
            "rate_limit": BOEING_RATE_LIMIT,
        },
        "celery_app.tasks.publishing.publish_product": {
            "rate_limit": SHOPIFY_RATE_LIMIT,
        },
        "celery_app.tasks.sync_dispatcher.sync_boeing_batch": {
            "rate_limit": BOEING_RATE_LIMIT,
        },
        "celery_app.tasks.sync_dispatcher.sync_shopify_product": {
            "rate_limit": SHOPIFY_RATE_LIMIT,
        },
    },

    # Beat schedule for periodic tasks (configurable via env)
    # SYNC_DISPATCH_MINUTE: When hourly sync runs (default: 45)
    # SYNC_RETRY_HOURS: Hours between retries (default: 4)
    # SYNC_CLEANUP_HOUR: Hour for daily cleanup (default: 0)
    beat_schedule={
        "dispatch-hourly-sync": {
            "task": "celery_app.tasks.sync_dispatcher.dispatch_hourly_sync",
            "schedule": crontab(minute=SYNC_DISPATCH_MINUTE),
            "options": {"queue": "default"},
        },
        "dispatch-retry-sync": {
            "task": "celery_app.tasks.sync_dispatcher.dispatch_retry_sync",
            "schedule": crontab(minute=15, hour=f"*/{SYNC_RETRY_HOURS}"),
            "options": {"queue": "default"},
        },
        "end-of-day-cleanup": {
            "task": "celery_app.tasks.sync_dispatcher.end_of_day_cleanup",
            "schedule": crontab(minute=0, hour=SYNC_CLEANUP_HOUR),
            "options": {"queue": "default"},
        },
    },

    # Result expiration
    result_expires=3600,  # 1 hour

    # Retry settings
    task_default_retry_delay=30,
    task_max_retries=3,

    # Worker pool configuration for Windows compatibility
    # On Windows, use 'solo' or 'threads' pool instead of 'prefork'
    worker_pool="solo" if IS_WINDOWS else "prefork",

    # Visibility timeout (how long before unacknowledged task is redelivered)
    broker_transport_options={"visibility_timeout": 3600},

    # Custom log format for better task identification
    # Format: [QUEUE] task_name | message
    worker_log_format="[%(asctime)s: %(levelname)s/%(processName)s] %(message)s",
    worker_task_log_format="[%(asctime)s: %(levelname)s/%(processName)s] [%(task_name)s] %(message)s",
)
