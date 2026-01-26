"""
Celery application package.
Exports the main Celery app instance.
"""
from celery_app.celery_config import celery_app

__all__ = ["celery_app"]
