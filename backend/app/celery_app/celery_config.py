"""
Celery configuration — broker, task routes, beat schedule.

Celery application configuration.
Configures Redis broker, task queues, rate limiting, and retry policies.

Note: On Windows, Celery's prefork pool doesn't work properly.
Use --pool=solo or --pool=threads on Windows.

=============================================================================
RUNNING WORKERS (Separate Terminals for Better Log Visibility)
=============================================================================

Option A: ALL QUEUES IN ONE TERMINAL (harder to read logs)
    celery -A app.celery_app worker --pool=solo -Q extraction,normalization,publishing,default,sync_boeing,sync_shopify -l info -n all@%h

Option B: SEPARATE TERMINALS (recommended for debugging)

    Terminal 1 - Extraction & Normalization:
        celery -A app.celery_app worker --pool=solo -Q extraction,normalization -l info -n extract@%h

    Terminal 2 - Publishing:
        celery -A app.celery_app worker --pool=solo -Q publishing -l info -n publish@%h

    Terminal 3 - Sync (Boeing + Shopify):
        celery -A app.celery_app worker --pool=solo -Q sync_boeing,sync_shopify --concurrency=1 -l info -n sync@%h

    Terminal 4 - Default (dispatchers, batch tasks):
        celery -A app.celery_app worker --pool=solo -Q default -l info -n default@%h

    Terminal 5 - Celery Beat (scheduler):
        celery -A app.celery_app beat -l info

The -n flag gives each worker a unique name that appears in logs.

IMPORTANT: When running workers manually (Option A or B), set AUTO_START_CELERY=false
in your .env file. Otherwise FastAPI will auto-start a duplicate worker that competes
for tasks on ALL queues. Its logs are piped (not visible), so task failures are silent.

=============================================================================
ENVIRONMENT VARIABLES
=============================================================================
    SYNC_MODE: "production" or "testing" (default: production)
    SYNC_DISPATCH_MINUTE: Minute of each hour to run sync (default: 45)
    SYNC_RETRY_HOURS: Hours between retry attempts (default: 4)
    SYNC_CLEANUP_HOUR: Hour (UTC) to run daily cleanup (default: 0)
    SYNC_ENABLED: "true" or "false" — master on/off for auto-sync (default: true)
    SYNC_FREQUENCY: "daily" or "weekly" (default: daily)
    SYNC_WEEKLY_DAY: Day name for weekly mode, e.g. "Sunday" (default: Sunday)
    BOEING_RATE_LIMIT: Boeing API calls per minute (default: 2)
    SHOPIFY_RATE_LIMIT: Shopify API calls per minute (default: 30)
Version: 1.0.0
"""
import logging
import platform

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.core.config import settings

# Configure logging for this module
logger = logging.getLogger(__name__)

# Detect Windows platform for pool configuration
IS_WINDOWS = platform.system() == "Windows"

# =============================================================================
# SYNC SCHEDULER CONFIGURATION (all values from centralized settings)
# =============================================================================

REDIS_URL = settings.redis_url
SYNC_MODE = settings.sync_mode
SYNC_DISPATCH_MINUTE = settings.sync_dispatch_minute
SYNC_TEST_BUCKET_COUNT = settings.sync_test_bucket_count
SYNC_RETRY_HOURS = settings.sync_retry_hours
SYNC_CLEANUP_HOUR = settings.sync_cleanup_hour
BOEING_RATE_LIMIT = settings.boeing_api_rate_limit
SHOPIFY_RATE_LIMIT = settings.shopify_api_rate_limit
SYNC_ENABLED = settings.sync_enabled
SYNC_FREQUENCY = settings.sync_frequency
SYNC_WEEKLY_DAY = settings.sync_weekly_day

# Map human-readable day names to Celery crontab day_of_week values
_DAY_NAME_TO_CRONTAB = {
    "sunday": "sun", "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat",
    "sun": "sun", "mon": "mon", "tue": "tue", "wed": "wed",
    "thu": "thu", "fri": "fri", "sat": "sat",
}


def _resolve_weekly_day(day_str: str) -> str:
    """Convert human-readable day name (e.g. 'Sunday') to crontab value ('sun')."""
    normalized = day_str.strip().lower()
    resolved = _DAY_NAME_TO_CRONTAB.get(normalized)
    if resolved:
        return resolved
    logger.warning(f"Invalid SYNC_WEEKLY_DAY '{day_str}', defaulting to Sunday")
    return "sun"


SYNC_WEEKLY_DAY_CRON = _resolve_weekly_day(SYNC_WEEKLY_DAY)


def _build_beat_schedule() -> dict:
    """Build Celery Beat schedule based on sync enabled/frequency settings."""
    if not SYNC_ENABLED:
        return {}

    # For weekly mode, restrict all sync schedules to the configured day
    day_kw = {"day_of_week": SYNC_WEEKLY_DAY_CRON} if SYNC_FREQUENCY == "weekly" else {}

    return {
        "dispatch-hourly-sync": {
            "task": "tasks.sync_dispatch.dispatch_hourly",
            "schedule": crontab(minute=SYNC_DISPATCH_MINUTE, **day_kw),
            "options": {"queue": "default"},
        },
        "dispatch-retry-sync": {
            "task": "tasks.sync_dispatch.dispatch_retry",
            "schedule": crontab(minute=15, hour=f"*/{SYNC_RETRY_HOURS}", **day_kw),
            "options": {"queue": "default"},
        },
        "end-of-day-cleanup": {
            "task": "tasks.sync_dispatch.end_of_day_cleanup",
            "schedule": crontab(minute=0, hour=SYNC_CLEANUP_HOUR, **day_kw),
            "options": {"queue": "default"},
        },
    }


# =============================================================================
# LOG SYNC MODE CONFIGURATION AT STARTUP
# =============================================================================
def _log_sync_mode_config():
    """Log sync scheduler configuration at startup."""
    border = "=" * 60

    if not SYNC_ENABLED:
        print(f"\n{border}")
        print("  SYNC SCHEDULER: DISABLED")
        print(border)
        print("  Auto-sync is turned off (SYNC_ENABLED=false).")
        print("  No sync tasks will be scheduled by Celery Beat.")
        print("  Publishing pipeline continues to work normally.")
        print(border)
        return

    frequency_label = SYNC_FREQUENCY.upper()
    if SYNC_FREQUENCY == "weekly":
        frequency_label = f"WEEKLY (every {SYNC_WEEKLY_DAY})"

    if SYNC_MODE == "testing":
        print(f"\n{border}")
        print("  SYNC SCHEDULER: TESTING MODE ACTIVE")
        print(border)
        print(f"  Frequency: {frequency_label}")
        print(f"  Bucket type: MINUTE buckets (0-{SYNC_TEST_BUCKET_COUNT - 1})")
        print(f"  Main sync (dispatch-hourly-sync): Every 10 minutes (crontab: {SYNC_DISPATCH_MINUTE})")
        print(f"  Retry sync (dispatch-retry-sync): Every {SYNC_RETRY_HOURS} hours at minute :15")
        print(f"  Daily cleanup (end-of-day-cleanup): {SYNC_CLEANUP_HOUR}:00 UTC")
        print(f"  Sync window check: DISABLED (processes immediately)")
        print(f"  Bucket count: {SYNC_TEST_BUCKET_COUNT}")
        if SYNC_FREQUENCY == "weekly":
            print(f"  Weekly day filter: {SYNC_WEEKLY_DAY} ({SYNC_WEEKLY_DAY_CRON})")
        print(border)
    else:
        print(f"\n{border}")
        print("  SYNC SCHEDULER: PRODUCTION MODE")
        print(border)
        print(f"  Frequency: {frequency_label}")
        print(f"  Bucket type: HOUR buckets (0-23)")
        print(f"  Main sync (dispatch-hourly-sync): At minute {SYNC_DISPATCH_MINUTE} of each hour")
        print(f"  Retry sync (dispatch-retry-sync): Every {SYNC_RETRY_HOURS} hours at minute :15")
        print(f"  Daily cleanup (end-of-day-cleanup): {SYNC_CLEANUP_HOUR}:00 UTC")
        print(f"  Sync window check: ENABLED (only at :45 mark)")
        if SYNC_FREQUENCY == "weekly":
            print(f"  Weekly day filter: {SYNC_WEEKLY_DAY} ({SYNC_WEEKLY_DAY_CRON})")
        print(border)

# Log configuration when module is loaded
_log_sync_mode_config()

celery_app = Celery(
    "boeing_data_hub",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "app.celery_app.tasks.extraction",
        "app.celery_app.tasks.normalization",
        "app.celery_app.tasks.publishing",
        "app.celery_app.tasks.batch",
        "app.celery_app.tasks.sync_dispatch",
        "app.celery_app.tasks.sync_boeing",
        "app.celery_app.tasks.sync_shopify",
        "app.celery_app.tasks.report_generation",
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
        Queue("sync_boeing"),
        Queue("sync_shopify"),
    ),
    task_routes={
        # Pipeline tasks
        "tasks.extraction.*": {"queue": "extraction"},
        "tasks.normalization.*": {"queue": "normalization"},
        "tasks.publishing.*": {"queue": "publishing"},
        "tasks.batch.*": {"queue": "default"},
        # Sync dispatchers run on default queue
        "tasks.sync_dispatch.*": {"queue": "default"},
        # Boeing sync tasks on dedicated queue for rate limiting
        "tasks.sync_boeing.*": {"queue": "sync_boeing"},
        # Shopify sync tasks on dedicated queue
        "tasks.sync_shopify.*": {"queue": "sync_shopify"},
        # Report generation on default queue
        "tasks.report_generation.*": {"queue": "default"},
    },

    # Rate limiting (configurable via env)
    task_annotations={
        "tasks.extraction.extract_chunk": {
            "rate_limit": BOEING_RATE_LIMIT,
        },
        "tasks.publishing.publish_product": {
            "rate_limit": SHOPIFY_RATE_LIMIT,
        },
        "tasks.sync_boeing.process_boeing_batch": {
            "rate_limit": BOEING_RATE_LIMIT,
        },
        "tasks.sync_shopify.update_shopify_product": {
            "rate_limit": SHOPIFY_RATE_LIMIT,
        },
    },

    # Beat schedule for periodic tasks (built dynamically based on
    # SYNC_ENABLED, SYNC_FREQUENCY, and SYNC_WEEKLY_DAY)
    beat_schedule=_build_beat_schedule(),

    # Result expiration
    result_expires=3600,  # 1 hour

    # Retry settings
    task_default_retry_delay=30,
    task_max_retries=3,

    # Worker pool configuration for Windows compatibility
    worker_pool="solo" if IS_WINDOWS else "prefork",

    # Visibility timeout
    broker_transport_options={"visibility_timeout": 3600},

    # Custom log format
    worker_log_format="[%(asctime)s: %(levelname)s/%(processName)s] %(message)s",
    worker_task_log_format="[%(asctime)s: %(levelname)s/%(processName)s] [%(task_name)s] %(message)s",
)
