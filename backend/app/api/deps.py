"""Shared FastAPI dependencies (tests override these)."""

from fastapi import Request

from app.jobs import JobQueue


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue
