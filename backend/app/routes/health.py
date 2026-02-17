"""
Health routes — readiness and liveness probe endpoints.

Health check route.

Provides basic health check endpoint.
Version: 1.0.0
"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}
