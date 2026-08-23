"""Celery tasks for durable background work."""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
