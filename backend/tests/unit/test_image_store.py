"""
Unit tests for ImageStore — Supabase storage upload and URL generation.

Tests cover:
- upload_image_from_url raises HTTPException when image_url is empty
- Object path generation follows products/{part_number}/{part_number}.jpg
- Aviall URL fallback logic
- FALLBACK_IMAGE_URL constant
Version: 1.0.0
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from fastapi import HTTPException


def _make_image_store():
    """Create an ImageStore with a mocked SupabaseClient."""
    from app.db.image_store import ImageStore

    mock_supabase_client = MagicMock()
    mock_storage_bucket = MagicMock()
    mock_supabase_client.client.storage.from_.return_value = mock_storage_bucket
    mock_storage_bucket.upload.return_value = None

    store = ImageStore(supabase_client=mock_supabase_client)
    return store, mock_supabase_client, mock_storage_bucket


@pytest.mark.unit
class TestUploadImageFromUrlValidation:
    """Verify input validation on upload_image_from_url."""

    @pytest.mark.asyncio
    async def test_raises_400_when_image_url_empty(self):
        store, _, _ = _make_image_store()

        with pytest.raises(HTTPException) as exc_info:
            await store.upload_image_from_url("", "WF338109")

        assert exc_info.value.status_code == 400
        assert "Image URL is required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_400_when_image_url_none_like(self):
        store, _, _ = _make_image_store()

        with pytest.raises(HTTPException) as exc_info:
            await store.upload_image_from_url(None, "WF338109")

        assert exc_info.value.status_code == 400


@pytest.mark.unit
class TestObjectPathGeneration:
    """Verify object path follows the expected pattern."""

    @pytest.mark.asyncio
    async def test_path_uses_part_number(self):
        store, mock_sb, mock_bucket = _make_image_store()
        fake_image = b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000

        # Mock httpx.AsyncClient as async context manager
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/jpeg"}

        async def mock_aiter():
            yield fake_image

        mock_response.aiter_bytes = mock_aiter

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_http_client = MagicMock()
        mock_http_client.stream.return_value = mock_stream_ctx

        mock_async_client_ctx = MagicMock()
        mock_async_client_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_async_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.image_store.httpx.AsyncClient", return_value=mock_async_client_ctx):
            with patch("app.db.base_store.settings") as mock_settings:
                mock_settings.supabase_url = "https://test.supabase.co"
                mock_settings.supabase_storage_bucket = "product-images"

                public_url, obj_path = await store.upload_image_from_url(
                    "https://example.com/img.jpg", "AN3-12A"
                )

        assert obj_path == "products/AN3-12A/AN3-12A.jpg"
        assert "AN3-12A" in public_url


@pytest.mark.unit
class TestSuccessfulUpload:
    """Verify successful download and upload flow."""

    @pytest.mark.asyncio
    async def test_returns_public_url_and_path(self):
        store, mock_sb, mock_bucket = _make_image_store()
        fake_image = b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/jpeg"}

        async def mock_aiter():
            yield fake_image

        mock_response.aiter_bytes = mock_aiter

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_http_client = MagicMock()
        mock_http_client.stream.return_value = mock_stream_ctx

        mock_async_client_ctx = MagicMock()
        mock_async_client_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_async_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.image_store.httpx.AsyncClient", return_value=mock_async_client_ctx):
            with patch("app.db.base_store.settings") as mock_settings:
                mock_settings.supabase_url = "https://test.supabase.co"
                mock_settings.supabase_storage_bucket = "product-images"

                public_url, obj_path = await store.upload_image_from_url(
                    "https://example.com/img.jpg", "WF338109"
                )

        assert obj_path == "products/WF338109/WF338109.jpg"
        assert "product-images" in public_url
        assert "WF338109" in public_url
        mock_bucket.upload.assert_called_once()


@pytest.mark.unit
class TestUploadErrorHandling:
    """Verify error paths in upload_image_from_url."""

    @pytest.mark.asyncio
    async def test_supabase_upload_error_raises_502(self):
        store, mock_sb, mock_bucket = _make_image_store()
        mock_bucket.upload.side_effect = Exception("Storage unavailable")
        fake_image = b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/jpeg"}

        async def mock_aiter():
            yield fake_image

        mock_response.aiter_bytes = mock_aiter

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_http_client = MagicMock()
        mock_http_client.stream.return_value = mock_stream_ctx

        mock_async_client_ctx = MagicMock()
        mock_async_client_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_async_client_ctx.__aexit__ = AsyncMock(return_value=False)

        from app.db.image_store import FALLBACK_IMAGE_URL

        with patch("app.db.image_store.httpx.AsyncClient", return_value=mock_async_client_ctx):
            with patch("app.db.base_store.settings") as mock_settings:
                mock_settings.supabase_url = "https://test.supabase.co"
                mock_settings.supabase_storage_bucket = "product-images"

                with pytest.raises(HTTPException) as exc_info:
                    await store.upload_image_from_url(FALLBACK_IMAGE_URL, "WF338109")

                assert exc_info.value.status_code == 502


@pytest.mark.unit
class TestFallbackImageUrl:
    """Verify the FALLBACK_IMAGE_URL constant."""

    def test_fallback_url_is_defined(self):
        from app.db.image_store import FALLBACK_IMAGE_URL
        assert FALLBACK_IMAGE_URL.startswith("https://")
        assert "placehold" in FALLBACK_IMAGE_URL

    def test_fallback_url_is_string(self):
        from app.db.image_store import FALLBACK_IMAGE_URL
        assert isinstance(FALLBACK_IMAGE_URL, str)
