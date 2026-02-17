"""
Celery application package — task registration and exports.

Celery application package.
Exports the main Celery app instance.
Version: 1.0.0
"""
from app.celery_app.celery_config import celery_app

__all__ = ["celery_app"]
