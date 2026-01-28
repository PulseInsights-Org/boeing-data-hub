"""
Pydantic schemas for bulk operations.

Provides request/response models for:
- Bulk search requests
- Bulk publish requests
- Batch status responses
"""
import re
import os
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Any
from datetime import datetime

# Configurable limits via environment variables
MAX_BULK_SEARCH_SIZE = int(os.getenv("MAX_BULK_SEARCH_SIZE", "50000"))
MAX_BULK_PUBLISH_SIZE = int(os.getenv("MAX_BULK_PUBLISH_SIZE", "10000"))


class BulkSearchRequest(BaseModel):
    """
    Request to start a bulk search operation.

    Supports multiple input methods:
    - part_numbers: Direct list of part numbers
    - part_numbers_text: Newline or comma-separated text
    - idempotency_key: Client-generated UUID to prevent duplicate batches

    Examples:
        # Direct list
        {"part_numbers": ["PN-001", "PN-002", "PN-003"]}

        # Text input (comma separated)
        {"part_numbers_text": "PN-001, PN-002, PN-003"}

        # Text input (newline separated)
        {"part_numbers_text": "PN-001\\nPN-002\\nPN-003"}

        # With idempotency key
        {"part_numbers": ["PN-001"], "idempotency_key": "uuid-here"}
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
                # Parse newline, comma, or semicolon separated text
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
        # Validate and normalize each part number
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
    """Details of a failed item."""
    part_number: str
    error: str
    timestamp: Optional[datetime] = None


class BatchStatusResponse(BaseModel):
    """
    Detailed batch status response.

    Includes progress tracking and failed items list.
    """
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
    part_numbers: Optional[List[str]] = None  # Original part numbers from search/extraction
    publish_part_numbers: Optional[List[str]] = None  # Part numbers selected for publishing
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class BatchListResponse(BaseModel):
    """Response for listing batches."""
    batches: List[BatchStatusResponse]
    total: int
