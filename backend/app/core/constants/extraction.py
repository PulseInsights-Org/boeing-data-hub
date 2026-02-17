"""
Extraction constants — batch sizes, default supplier, system user.

Extraction pipeline constants (Boeing API defaults).
Version: 1.0.0
"""
from app.core.config import settings

DEFAULT_SUPPLIER: str = "BDI"
DEFAULT_VENDOR: str = "BDI"
SYSTEM_USER_ID: str = "system"

BOEING_BATCH_SIZE: int = settings.boeing_batch_size
