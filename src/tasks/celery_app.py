"""Celery application setup for OpsLens background task processing."""

from __future__ import annotations

import logging
import os

from celery import Celery
from celery.schedules import crontab, schedule
from celery.signals import after_setup_logger, after_setup_task_logger

import structlog

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_CONCURRENCY: int = int(os.environ.get("CELERY_CONCURRENCY", "4"))
CELERY_PREFETCH_MULTIPLIER: int = int(
    os.environ.get("CELERY_PREFETCH_MULTIPLIER", "1")
)

# ---------------------------------------------------------------------------
# Celery app creation
# ---------------------------------------------------------------------------

celery_app = Celery("opslens")

celery_app.conf.update(
    # Broker / backend
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    broker_connection_retry_on_startup=True,

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    # Concurrency
    worker_concurrency=CELERY_CONCURRENCY,
    worker_prefetch_multiplier=CELERY_PREFETCH_MULTIPLIER,

    # Task routing — each task module maps to its own queue
    task_routes={
        "src.tasks.webhook_tasks.*": {"queue": "webhooks"},
        "src.tasks.agent_tasks.*": {"queue": "agents"},
        "src.tasks.enterprise_tasks.*": {"queue": "enterprise"},
    },

    # Default retry policy applied to all tasks unless overridden
    task_default_retry_delay=10,
    task_annotations={
        "src.tasks.webhook_tasks.*": {
            "rate_limit": "200/m",
            "max_retries": 3,
            "retry_backoff": True,
            "retry_backoff_max": 300,
            "soft_time_limit": 30,
            "time_limit": 60,
        },
        "src.tasks.agent_tasks.*": {
            "max_retries": 3,
            "retry_backoff": True,
            "retry_backoff_max": 300,
            "soft_time_limit": 300,
            "time_limit": 600,
        },
        "src.tasks.enterprise_tasks.*": {
            "max_retries": 3,
            "retry_backoff": True,
            "retry_backoff_max": 300,
            "soft_time_limit": 120,
            "time_limit": 300,
        },
    },

    # Result settings
    result_expires=3600,  # 1 hour

    # Task tracking
    task_track_started=True,
    task_acks_late=True,  # Re-deliver if worker crashes
    worker_cancel_long_running_tasks_on_connection_loss=True,

    # Auto-discover task modules
    include=[
        "src.tasks.webhook_tasks",
        "src.tasks.agent_tasks",
        "src.tasks.enterprise_tasks",
    ],

    # Beat schedule — periodic tasks
    beat_schedule={
        "check_sla_breaches": {
            "task": "src.tasks.enterprise_tasks.check_sla_breaches_task",
            "schedule": schedule(run_every=60.0),
            "options": {"queue": "enterprise"},
        },
        "rotate_oncall": {
            "task": "src.tasks.enterprise_tasks.rotate_oncall_schedules_task",
            "schedule": schedule(run_every=3600.0),
            "options": {"queue": "enterprise"},
        },
        "notion_sync": {
            "task": "src.tasks.enterprise_tasks.notion_sync_task",
            "schedule": schedule(run_every=30.0),
            "options": {"queue": "enterprise"},
        },
        "command_center_update": {
            "task": "src.tasks.enterprise_tasks.command_center_update_task",
            "schedule": schedule(run_every=120.0),
            "options": {"queue": "enterprise"},
        },
        "cleanup_old_data": {
            "task": "src.tasks.enterprise_tasks.cleanup_old_data_task",
            "schedule": crontab(hour=3, minute=0),
            "args": (90,),
            "options": {"queue": "enterprise"},
        },
    },
)


# ---------------------------------------------------------------------------
# Structlog integration for Celery workers
# ---------------------------------------------------------------------------

@after_setup_logger.connect  # type: ignore[misc]
def setup_celery_logger(logger: logging.Logger, **kwargs: object) -> None:
    """Configure the Celery logger to use structlog processors."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(),
    )
    for handler in logger.handlers:
        handler.setFormatter(formatter)


@after_setup_task_logger.connect  # type: ignore[misc]
def setup_celery_task_logger(logger: logging.Logger, **kwargs: object) -> None:
    """Ensure task loggers share the same formatting as the main logger."""
    setup_celery_logger(logger)
