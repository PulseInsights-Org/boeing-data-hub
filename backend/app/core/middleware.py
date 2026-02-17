"""
CORS middleware — configures allowed origins, methods, and headers.

Middleware configuration for the FastAPI application.

Extracted from main.py to keep app factory slim.
Version: 1.0.0
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def apply_cors(app: FastAPI) -> None:
    """Apply CORS middleware with permissive defaults."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
