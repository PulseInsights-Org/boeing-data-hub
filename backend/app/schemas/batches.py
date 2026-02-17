"""
Batch schemas — bulk operation request/response models.

Batch operation schemas.

Defines request/response models for bulk search and publish operations.
Version: 1.0.0
"""
import re
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Any
from datetime import datetime

from app.core.config import settings

MAX_BULK_SEARCH_SIZE = settings.max_bulk_search_size
MAX_BULK_PUBLISH_SIZE = settings.max_bulk_publish_size


class BulkSearchRequest(BaseModel):
    """
    Request to start a bulk search operation.

    Supports multiple input methods:
    - part_numbers: Direct list of part numbers
    - part_numbers_text: Newline or comma-separated text
    - idempotency_key: Client-generated UUID to prevent duplicate batches
    """
    part_numbers: Optional[List[str]] = Field(
        None,
        description="List of part numbers to search"
    )
    part_numbers_text: Optional[str] = Field(
        None,
        description="Newline or comma-separated part numbers (alternative to list)"
    )
    idempotency_key: Optional[str] = Field(
        None,
        description="Client-generated UUID to prevent duplicate batch creation on retries"
    )

    @model_validator(mode='before')
    @classmethod
    def parse_part_numbers(cls, values: Any) -> Any:
        """Parse part_numbers from text if provided."""
        if isinstance(values, dict):
            part_numbers = values.get('part_numbers')
            part_numbers_text = values.get('part_numbers_text')

            if part_numbers and part_numbers_text:
                raise ValueError("Provide either 'part_numbers' or 'part_numbers_text', not both")

            if part_numbers_text:
                parsed = [pn.strip() for pn in re.split(r'[,;\n\r]+', part_numbers_text) if pn.strip()]
                if not parsed:
                    raise ValueError("No valid part numbers found in text")
                if len(parsed) > MAX_BULK_SEARCH_SIZE:
                    raise ValueError(f"Maximum {MAX_BULK_SEARCH_SIZE} part numbers allowed")
                values['part_numbers'] = parsed

            if not values.get('part_numbers'):
                raise ValueError("Either 'part_numbers' or 'part_numbers_text' is required")

        return values

    @field_validator('part_numbers')
    @classmethod
    def validate_part_numbers_list(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate part numbers list."""
        if v is None:
            return v
        if len(v) > MAX_BULK_SEARCH_SIZE:
            raise ValueError(f"Maximum {MAX_BULK_SEARCH_SIZE} part numbers allowed")
        validated = []
        for pn in v:
            if not pn or len(pn) > 50:
                raise ValueError(f"Invalid part number: {pn}")
            validated.append(pn.strip().upper())
        return validated


class BulkPublishRequest(BaseModel):
    """
    Request to start a bulk publish operation.

    Same input options as BulkSearchRequest.
    If batch_id is provided, uses that existing batch instead of creating a new one.
    """
    part_numbers: Optional[List[str]] = Field(
        None,
        description="List of part numbers to publish"
    )
    part_numbers_text: Optional[str] = Field(
        None,
        description="Newline or comma-separated part numbers"
    )
    idempotency_key: Optional[str] = Field(
        None,
        description="Client-generated UUID to prevent duplicate batch creation"
    )
    batch_id: Optional[str] = Field(
        None,
        description="Existing batch ID to use for publishing (continues the same pipeline)"
    )

    @model_validator(mode='before')
    @classmethod
    def parse_part_numbers(cls, values: Any) -> Any:
        """Parse part_numbers from text if provided."""
        if isinstance(values, dict):
            part_numbers = values.get('part_numbers')
            part_numbers_text = values.get('part_numbers_text')

            if part_numbers_text and not part_numbers:
                parsed = [pn.strip() for pn in re.split(r'[,;\n\r]+', part_numbers_text) if pn.strip()]
                if parsed:
                    if len(parsed) > MAX_BULK_PUBLISH_SIZE:
                        raise ValueError(f"Maximum {MAX_BULK_PUBLISH_SIZE} part numbers allowed")
                    values['part_numbers'] = parsed

            if not values.get('part_numbers'):
                raise ValueError("Either 'part_numbers' or 'part_numbers_text' is required")

        return values

    @field_validator('part_numbers')
    @classmethod
    def validate_part_numbers_list(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate part numbers list."""
        if v is None:
            return v
        if len(v) > MAX_BULK_PUBLISH_SIZE:
            raise ValueError(f"Maximum {MAX_BULK_PUBLISH_SIZE} part numbers allowed")
        return v


class BulkOperationResponse(BaseModel):
    """Response for bulk operation initiation."""
    batch_id: str
    total_items: int
    status: str
    message: str
    idempotency_key: Optional[str] = None


class FailedItem(BaseModel):
    """Details of a failed item with pipeline stage info."""
    part_number: str
    error: str
    stage: Optional[str] = None
    timestamp: Optional[datetime] = None


class BatchStatusResponse(BaseModel):
    """Detailed batch status response."""
    id: str
    batch_type: str
    status: str
    total_items: int
    extracted_count: int
    normalized_count: int
    published_count: int
    failed_count: int
    progress_percent: float
    failed_items: Optional[List[FailedItem]] = None
    skipped_count: int = 0
    skipped_part_numbers: Optional[List[str]] = None
    part_numbers: Optional[List[str]] = None
    publish_part_numbers: Optional[List[str]] = None
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class BatchListResponse(BaseModel):
    """Response for listing batches."""
    batches: List[BatchStatusResponse]
    total: int
