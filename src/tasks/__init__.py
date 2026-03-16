"""OpsLens background task infrastructure powered by Celery + Redis."""

from src.tasks.celery_app import celery_app

__all__ = ["celery_app"]
