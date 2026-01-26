"""
Celery application configuration.
Configures Redis broker, task queues, rate limiting, and retry policies.

Note: On Windows, Celery's prefork pool doesn't work properly.
Run workers with --pool=solo or --pool=threads on Windows:
    celery -A celery_app worker --pool=solo -Q extraction,normalization,publishing,default -l info
"""
from celery import Celery
from kombu import Queue
import os
import platform

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Detect Windows platform for pool configuration
IS_WINDOWS = platform.system() == "Windows"

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

    # Worker pool configuration for Windows compatibility
    # On Windows, use 'solo' or 'threads' pool instead of 'prefork'
    worker_pool="solo" if IS_WINDOWS else "prefork",

    # Visibility timeout (how long before unacknowledged task is redelivered)
    broker_transport_options={"visibility_timeout": 3600},
)
