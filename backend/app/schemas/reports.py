"""
Report schemas — Pydantic models for report API endpoints.
Version: 1.0.0
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    """Request body for manual report generation."""
    cycle_id: Optional[str] = None


class ReportResponse(BaseModel):
    """A generated sync report."""
    id: str
    cycle_id: str
    report_text: str
    summary_stats: Dict[str, Any] = {}
    file_path: Optional[str] = None
    email_sent: bool = False
    email_recipients: List[str] = []
    created_at: str


class ReportGenerateResponse(BaseModel):
    """Response after queuing report generation."""
    status: str
    message: str
    cycle_id: Optional[str] = None


class CycleProgressResponse(BaseModel):
    """Current sync cycle progress."""
    cycle_id: str
    buckets_completed: List[int]
    total_buckets: int
    is_complete: bool
    progress_percent: float
