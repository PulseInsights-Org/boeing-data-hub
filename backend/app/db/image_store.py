"""
Image store — Supabase storage upload and URL generation.

Image store – Supabase Storage operations for product images.
Version: 1.0.0
"""

import logging
from typing import Any, Dict

import httpx
from urllib.parse import urlsplit
from fastapi import HTTPException

from app.db.base_store import BaseStore

logger = logging.getLogger("image_store")

FALLBACK_IMAGE_URL = "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto"


class ImageStore(BaseStore):
    """Upload / download product images via Supabase Storage."""

    async def upload_image_from_url(
        self, image_url: str, part_number: str
    ) -> tuple[str, str]:
        if not image_url:
            raise HTTPException(
                status_code=400, detail="Image URL is required for upload"
            )
        object_path = f"products/{part_number}/{part_number}.jpg"
        logger.info(
            "supabase upload image bucket=%s object_path=%s original_url=%s",
            self._bucket,
            object_path,
            image_url,
        )

        # Build list of URLs to try in order
        urls_to_try = []
        parsed = urlsplit(image_url)

        if parsed.netloc.endswith("aviall.com") or "boeing" in parsed.netloc:
            urls_to_try.append(("original", image_url, "https://www.aviall.com/"))
            boeing_url = f"https://shop.boeing.com{parsed.path}"
            if parsed.query:
                boeing_url = f"{boeing_url}?{parsed.query}"
            urls_to_try.append(("shop.boeing.com", boeing_url, "https://shop.boeing.com/"))
        else:
            urls_to_try.append(("original", image_url, image_url))

        async def _download_bytes(
            client: httpx.AsyncClient, url: str, headers: dict
        ) -> tuple[int, dict, bytes]:
            async with client.stream("GET", url, headers=headers) as resp:
                data = bytearray()
                async for chunk in resp.aiter_bytes():
                    data.extend(chunk)
                return resp.status_code, dict(resp.headers), bytes(data)

        last_error = None
        status = 0
        resp_headers: Dict[str, Any] = {}
        body = b""

        for source_name, download_url, referer in urls_to_try:
            download_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }

            try:
                timeout = httpx.Timeout(30.0, connect=10.0)
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True, http2=True
                ) as client:
                    logger.info(
                        "image download attempting source=%s url=%s",
                        source_name,
                        download_url,
                    )
                    status, resp_headers, body = await _download_bytes(
                        client, download_url, download_headers
                    )

                    content_type_header = (
                        resp_headers.get("Content-Type")
                        or resp_headers.get("content-type")
                        or "unknown"
                    )
                    first_bytes = body[:100] if body else b""
                    logger.info(
                        "image download status=%s source=%s content_length=%s content_type=%s first_bytes=%s",
                        status,
                        source_name,
                        len(body),
                        content_type_header,
                        first_bytes[:50],
                    )

                    if status < 300 and len(body) > 1000:
                        break

            except httpx.RequestError as exc:
                logger.info(
                    "image download error source=%s url=%s detail=%s",
                    source_name,
                    download_url,
                    repr(exc),
                )
                last_error = exc
                continue
        else:
            if image_url != FALLBACK_IMAGE_URL:
                logger.info(
                    "image download all sources failed, fallback to placeholder url=%s",
                    FALLBACK_IMAGE_URL,
                )
                return await self.upload_image_from_url(FALLBACK_IMAGE_URL, part_number)
            raise HTTPException(
                status_code=502, detail=f"Image download error: {last_error!r}"
            ) from last_error

        if status >= 300:
            location = resp_headers.get("Location")
            if image_url != FALLBACK_IMAGE_URL:
                logger.info(
                    "image download fallback to placeholder url=%s", FALLBACK_IMAGE_URL
                )
                return await self.upload_image_from_url(FALLBACK_IMAGE_URL, part_number)
            raise HTTPException(
                status_code=502,
                detail=f"Image download failed: {status} location={location}",
            )

        content_type = (
            resp_headers.get("Content-Type")
            or resp_headers.get("content-type")
            or "image/jpeg"
        )
        image_bytes = body

        is_image = content_type.startswith("image/")
        is_small_response = len(body) < 1000

        if not is_image or (is_small_response and b"<html" in body.lower()):
            logger.info(
                "image download got non-image response content_type=%s size=%s url=%s",
                content_type,
                len(body),
                download_url,
            )
            if image_url != FALLBACK_IMAGE_URL:
                logger.info(
                    "image download fallback to placeholder due to invalid content url=%s",
                    FALLBACK_IMAGE_URL,
                )
                return await self.upload_image_from_url(FALLBACK_IMAGE_URL, part_number)
            raise HTTPException(
                status_code=502,
                detail=f"Image download returned non-image content: {content_type}",
            )

        # Upload using Supabase Storage SDK
        try:
            logger.info(
                "supabase storage upload bucket=%s path=%s size=%s",
                self._bucket,
                object_path,
                len(image_bytes),
            )
            self._client.storage.from_(self._bucket).upload(
                path=object_path,
                file=image_bytes,
                file_options={"content-type": content_type, "upsert": "true"},
            )
        except Exception as exc:
            logger.info("image upload error path=%s detail=%s", object_path, str(exc))
            raise HTTPException(
                status_code=502, detail=f"Image upload error: {exc}"
            ) from exc

        public_url = f"{self._storage_url}/object/public/{self._bucket}/{object_path}"
        return public_url, object_path
