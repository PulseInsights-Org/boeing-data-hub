import logging
import uuid
from typing import Any, Dict, List

import httpx
from urllib.parse import urlsplit
from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.core.config import Settings
from app.clients.supabase_client import SupabaseClient


logger = logging.getLogger("supabase_store")


class SupabaseStore:
    def __init__(self, settings: Settings) -> None:
        self._supabase_client = SupabaseClient(settings)
        self._bucket = settings.supabase_storage_bucket
        self._url = settings.supabase_url
        self._key = settings.supabase_service_role_key

        # Storage URL still needed for public URL construction
        self._storage_url = self._url.rstrip("/") + "/storage/v1"

    @property
    def _client(self):
        """Get the Supabase client instance."""
        return self._supabase_client.client

    async def _insert(self, table: str, rows: List[Dict[str, Any]]) -> None:
        """Insert rows into a table."""
        if not rows:
            return

        try:
            self._client.table(table).insert(rows).execute()
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase insert into {table} failed: {e}",
            )

    async def _upsert(
        self, table: str, rows: List[Dict[str, Any]], on_conflict: str | None = None
    ) -> None:
        """Upsert rows into a table (insert or update on conflict).

        Args:
            table: Table name
            rows: List of row dictionaries to upsert
            on_conflict: Column(s) to use for conflict resolution (e.g., "user_id,sku")
        """
        if not rows:
            return

        try:
            if on_conflict:
                self._client.table(table).upsert(rows, on_conflict=on_conflict).execute()
            else:
                self._client.table(table).upsert(rows).execute()
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase upsert into {table} failed: {e}",
            )

    async def _select(
        self, table: str, columns: str = "*", filters: Dict[str, Any] | None = None
    ) -> List[Dict[str, Any]]:
        """Select rows from a table with optional filters."""
        try:
            query = self._client.table(table).select(columns)

            if filters:
                for key, value in filters.items():
                    query = query.eq(key, value)

            response = query.execute()
            return response.data or []
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase select from {table} failed: {e}",
            )

    async def _update(
        self, table: str, filters: Dict[str, Any], payload: Dict[str, Any]
    ) -> None:
        """Update rows in a table matching the filters."""
        try:
            query = self._client.table(table).update(payload)

            for key, value in filters.items():
                query = query.eq(key, value)

            query.execute()
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update {table} failed: {e}",
            )

    async def insert_boeing_raw_data(
        self, search_query: str, raw_payload: Dict[str, Any], user_id: str = "system"
    ) -> None:
        row = {
            "search_query": search_query,
            "raw_payload": raw_payload,
            "user_id": user_id,
        }
        await self._insert("boeing_raw_data", [row])

    async def upsert_product_staging(
        self, records: List[Dict[str, Any]], user_id: str = "system", batch_id: str | None = None
    ) -> None:
        if not records:
            return

        db_rows: List[Dict[str, Any]] = []

        for rec in records:
            shopify_data: Dict[str, Any] = rec.get("shopify") or {}

            part_number = (
                shopify_data.get("sku") or rec.get("sku") or rec.get("aviall_part_number")
            )
            name = shopify_data.get("title") or rec.get("title") or part_number
            description = shopify_data.get("body_html") or rec.get("description") or ""

            manufacturer = shopify_data.get("manufacturer") or rec.get("manufacturer") or ""
            vendor = shopify_data.get("vendor") or rec.get("vendor") or manufacturer

            price = (
                shopify_data.get("price")
                or shopify_data.get("cost_per_item")
                or rec.get("price")
                or rec.get("cost_per_item")
            )
            cost_per_item = shopify_data.get("cost_per_item") or rec.get("cost_per_item")
            list_price = rec.get("list_price")
            net_price = rec.get("net_price")
            currency = shopify_data.get("currency") or rec.get("currency")

            inventory_qty = shopify_data.get("inventory_quantity") or rec.get(
                "inventory_quantity"
            )
            inventory_status = rec.get("inventory_status")
            location_summary = shopify_data.get("location_summary") or rec.get(
                "location_summary"
            )

            weight = shopify_data.get("weight") or rec.get("weight")
            weight_unit = shopify_data.get("weight_uom") or rec.get("weight_uom")

            country_of_origin = shopify_data.get("country_of_origin") or rec.get(
                "country_of_origin"
            )

            dim_length = shopify_data.get("length") or rec.get("dim_length")
            dim_width = shopify_data.get("width") or rec.get("dim_width")
            dim_height = shopify_data.get("height") or rec.get("dim_height")
            dim_uom = shopify_data.get("dim_uom") or rec.get("dim_uom")
            base_uom = shopify_data.get("unit_of_measure") or rec.get("base_uom")
            hazmat_code = rec.get("hazmat_code")
            faa_approval_code = rec.get("faa_approval_code")
            eccn = rec.get("eccn")
            schedule_b_code = rec.get("schedule_b_code")
            supplier_name = rec.get("supplier_name")
            boeing_name = rec.get("name")
            boeing_description = rec.get("description")
            boeing_image_url = rec.get("boeing_image_url") or rec.get("product_image")
            boeing_thumbnail_url = rec.get("boeing_thumbnail_url") or rec.get(
                "thumbnail_image"
            )
            image_url = rec.get("image_url")
            image_path = rec.get("image_path")
            condition = rec.get("condition")
            pma = rec.get("pma")
            estimated_lead_time_days = rec.get("estimated_lead_time_days")
            trace = rec.get("trace")
            expiration_date = rec.get("expiration_date")
            notes = rec.get("notes")

            status = rec.get("status") or "fetched"

            row_data = {
                    "id": str(uuid.uuid4()),
                    "sku": part_number,
                    "title": name,
                    "body_html": description,
                    "vendor": vendor,
                    "price": price,
                    "cost_per_item": cost_per_item,
                    "list_price": list_price,
                    "net_price": net_price,
                    "currency": currency,
                    "inventory_quantity": inventory_qty,
                    "inventory_status": inventory_status,
                    "location_summary": location_summary,
                    "weight": weight,
                    "weight_unit": weight_unit,
                    "country_of_origin": country_of_origin,
                    "dim_length": dim_length,
                    "dim_width": dim_width,
                    "dim_height": dim_height,
                    "dim_uom": dim_uom,
                    "base_uom": base_uom,
                    "hazmat_code": hazmat_code,
                    "faa_approval_code": faa_approval_code,
                    "eccn": eccn,
                    "schedule_b_code": schedule_b_code,
                    "supplier_name": supplier_name,
                    "boeing_name": boeing_name,
                    "boeing_description": boeing_description,
                    "boeing_image_url": boeing_image_url,
                    "boeing_thumbnail_url": boeing_thumbnail_url,
                    "image_url": image_url,
                    "image_path": image_path,
                    "condition": condition,
                    "pma": pma,
                    "estimated_lead_time_days": estimated_lead_time_days,
                    "trace": trace,
                    "expiration_date": expiration_date,
                    "notes": notes,
                    "status": status,
                    "user_id": user_id,
                }
            # Add batch_id if provided
            if batch_id:
                row_data["batch_id"] = batch_id
            db_rows.append(row_data)

        # Upsert using composite unique constraint (user_id, sku)
        # This ensures same part number for same user updates existing record
        await self._upsert("product_staging", db_rows, on_conflict="user_id,sku")

    async def upsert_product(
        self, record: Dict[str, Any], shopify_product_id: str | None = None, user_id: str = "system"
    ) -> None:
        shopify_data: Dict[str, Any] = record.get("shopify") or {}

        part_number = (
            shopify_data.get("sku")
            or record.get("sku")
            or record.get("aviall_part_number")
        )
        name = shopify_data.get("title") or record.get("title") or part_number
        description = shopify_data.get("body_html") or record.get("description") or ""

        manufacturer = (
            shopify_data.get("manufacturer") or record.get("manufacturer") or ""
        )
        vendor = shopify_data.get("vendor") or record.get("vendor") or manufacturer

        price = (
            shopify_data.get("price")
            or shopify_data.get("cost_per_item")
            or record.get("price")
            or record.get("cost_per_item")
        )
        cost_per_item = shopify_data.get("cost_per_item") or record.get("cost_per_item")
        list_price = record.get("list_price")
        net_price = record.get("net_price")
        currency = shopify_data.get("currency") or record.get("currency")

        inventory_qty = shopify_data.get("inventory_quantity") or record.get(
            "inventory_quantity"
        )
        inventory_status = record.get("inventory_status")
        location_summary = shopify_data.get("location_summary") or record.get(
            "location_summary"
        )

        weight = shopify_data.get("weight") or record.get("weight")
        weight_unit = shopify_data.get("weight_uom") or record.get("weight_uom")

        country_of_origin = shopify_data.get("country_of_origin") or record.get(
            "country_of_origin"
        )

        dim_length = shopify_data.get("length") or record.get("dim_length")
        dim_width = shopify_data.get("width") or record.get("dim_width")
        dim_height = shopify_data.get("height") or record.get("dim_height")
        dim_uom = shopify_data.get("dim_uom") or record.get("dim_uom")
        base_uom = shopify_data.get("unit_of_measure") or record.get("base_uom")
        hazmat_code = record.get("hazmat_code")
        faa_approval_code = record.get("faa_approval_code")
        eccn = record.get("eccn")
        schedule_b_code = record.get("schedule_b_code")
        supplier_name = record.get("supplier_name")
        boeing_name = record.get("name")
        boeing_description = record.get("description")
        boeing_image_url = record.get("boeing_image_url") or record.get("product_image")
        boeing_thumbnail_url = record.get("boeing_thumbnail_url") or record.get(
            "thumbnail_image"
        )
        image_url = record.get("image_url")
        image_path = record.get("image_path")
        condition = record.get("condition")
        pma = record.get("pma")
        estimated_lead_time_days = record.get("estimated_lead_time_days")
        trace = record.get("trace")
        expiration_date = record.get("expiration_date")
        notes = record.get("notes")

        db_row = {
            "id": str(uuid.uuid4()),
            "sku": part_number,
            "title": name,
            "body_html": description,
            "vendor": vendor,
            "price": price,
            "cost_per_item": cost_per_item,
            "list_price": list_price,
            "net_price": net_price,
            "currency": currency,
            "inventory_quantity": inventory_qty,
            "inventory_status": inventory_status,
            "location_summary": location_summary,
            "weight": weight,
            "weight_unit": weight_unit,
            "country_of_origin": country_of_origin,
            "dim_length": dim_length,
            "dim_width": dim_width,
            "dim_height": dim_height,
            "dim_uom": dim_uom,
            "base_uom": base_uom,
            "hazmat_code": hazmat_code,
            "faa_approval_code": faa_approval_code,
            "eccn": eccn,
            "schedule_b_code": schedule_b_code,
            "supplier_name": supplier_name,
            "boeing_name": boeing_name,
            "boeing_description": boeing_description,
            "boeing_image_url": boeing_image_url,
            "boeing_thumbnail_url": boeing_thumbnail_url,
            "image_url": image_url,
            "image_path": image_path,
            "condition": condition,
            "pma": pma,
            "estimated_lead_time_days": estimated_lead_time_days,
            "trace": trace,
            "expiration_date": expiration_date,
            "notes": notes,
            "shopify_product_id": shopify_product_id,
            "user_id": user_id,
        }

        # Upsert using composite unique constraint (user_id, sku)
        # This ensures same part number for same user updates existing record
        await self._upsert("product", [db_row], on_conflict="user_id,sku")

    async def upsert_quote_form_data(self, record: Dict[str, Any]) -> None:
        await self._upsert("quotes", [record])

    async def get_product_staging_by_part_number(
        self, part_number: str, user_id: str | None = None
    ) -> Dict[str, Any] | None:
        """Get a product staging record by part number (checks both id and sku).

        Args:
            part_number: The part number/SKU to look up
            user_id: Optional user ID to filter by (for user-specific lookup)
        """
        try:
            # Build query with optional user_id filter
            query = self._client.table("product_staging").select("*")

            if user_id:
                query = query.eq("user_id", user_id)

            # Try to find by sku first (primary identifier)
            response = query.eq("sku", part_number).limit(1).execute()

            if response.data:
                return response.data[0]

            # If not found by sku, try by id
            query = self._client.table("product_staging").select("*")
            if user_id:
                query = query.eq("user_id", user_id)

            response = query.eq("id", part_number).limit(1).execute()

            return response.data[0] if response.data else None
        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase select from product_staging failed: {e}",
            )

    async def get_product_by_part_number(
        self, part_number: str, user_id: str | None = None
    ) -> Dict[str, Any] | None:
        """Get a product record by part number (checks both id and sku).

        Args:
            part_number: The part number/SKU to look up
            user_id: Optional user ID to filter by (for user-specific lookup)
        """
        try:
            query = self._client.table("product").select("*")

            if user_id:
                query = query.eq("user_id", user_id)

            # Try to find by sku first
            response = query.eq("sku", part_number).limit(1).execute()

            if response.data:
                return response.data[0]

            # If not found by sku, try by id
            query = self._client.table("product").select("*")
            if user_id:
                query = query.eq("user_id", user_id)

            response = query.eq("id", part_number).limit(1).execute()

            return response.data[0] if response.data else None
        except APIError as e:
            logger.info("supabase error table=product detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase select from product failed: {e}",
            )

    async def update_product_staging_shopify_id(
        self, part_number: str, shopify_product_id: str, user_id: str | None = None
    ) -> None:
        """Update the shopify_product_id and status for a product staging record.

        Args:
            part_number: The part number/SKU to update
            shopify_product_id: The Shopify product ID to set
            user_id: Optional user ID to filter by
        """
        payload = {
            "shopify_product_id": shopify_product_id,
            "status": "published",
        }

        try:
            # Build and execute query - update by sku with user_id filter
            if user_id:
                response = (
                    self._client.table("product_staging")
                    .update(payload)
                    .eq("user_id", user_id)
                    .eq("sku", part_number)
                    .execute()
                )
            else:
                response = (
                    self._client.table("product_staging")
                    .update(payload)
                    .eq("sku", part_number)
                    .execute()
                )

            # If no rows updated by sku, try by id
            if not response.data:
                logger.info(f"No rows updated by sku={part_number}, trying by id")
                if user_id:
                    response = (
                        self._client.table("product_staging")
                        .update(payload)
                        .eq("user_id", user_id)
                        .eq("id", part_number)
                        .execute()
                    )
                else:
                    response = (
                        self._client.table("product_staging")
                        .update(payload)
                        .eq("id", part_number)
                        .execute()
                    )

            if response.data:
                logger.info(f"Updated product_staging status to published for {part_number}, user_id={user_id}")
            else:
                logger.warning(f"No product_staging record found to update for {part_number}, user_id={user_id}")

        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update product_staging failed: {e}",
            )

    async def update_product_staging_image(
        self, part_number: str, image_url: str, image_path: str
    ) -> None:
        """Update product staging image by part number (checks both id and sku)."""
        payload = {
            "image_url": image_url,
            "image_path": image_path,
        }

        try:
            # Try to update by id first
            response = (
                self._client.table("product_staging")
                .update(payload)
                .eq("id", part_number)
                .execute()
            )

            # If no rows updated by id, try by sku
            if not response.data:
                self._client.table("product_staging").update(payload).eq(
                    "sku", part_number
                ).execute()
        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update product_staging failed: {e}",
            )

    async def upload_image_from_url(
        self, image_url: str, part_number: str
    ) -> tuple[str, str]:
        if not image_url:
            raise HTTPException(
                status_code=400, detail="Image URL is required for upload"
            )
        fallback_url = "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto"
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

        # For aviall.com URLs, try multiple approaches
        if parsed.netloc.endswith("aviall.com") or "boeing" in parsed.netloc:
            # Try the original URL first (with redirects)
            urls_to_try.append(("original", image_url, "https://www.aviall.com/"))
            # Then try shop.boeing.com version
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

        # Try each URL with appropriate headers
        last_error = None
        status = 0
        resp_headers = {}
        body = b""

        for source_name, download_url, referer in urls_to_try:
            # Use headers that closely mimic a real Chrome browser
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
                # Use shorter timeout (30s) per attempt, follow redirects
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

                    # If successful, break out of the loop
                    if status < 300 and len(body) > 1000:  # Must be > 1KB to be a real image
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
            # All URLs failed or returned small/empty response
            if image_url != fallback_url:
                logger.info(
                    "image download all sources failed, fallback to placeholder url=%s",
                    fallback_url,
                )
                return await self.upload_image_from_url(fallback_url, part_number)
            raise HTTPException(
                status_code=502, detail=f"Image download error: {last_error!r}"
            ) from last_error

        if status >= 300:
            location = resp_headers.get("Location")
            if image_url != fallback_url:
                logger.info(
                    "image download fallback to placeholder url=%s", fallback_url
                )
                return await self.upload_image_from_url(fallback_url, part_number)
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

        # Validate we actually got image data, not an HTML error page
        is_image = content_type.startswith("image/")
        is_small_response = len(body) < 1000  # Real images are usually larger than 1KB

        if not is_image or (is_small_response and b"<html" in body.lower()):
            logger.info(
                "image download got non-image response content_type=%s size=%s url=%s",
                content_type,
                len(body),
                download_url,
            )
            if image_url != fallback_url:
                logger.info(
                    "image download fallback to placeholder due to invalid content url=%s",
                    fallback_url,
                )
                return await self.upload_image_from_url(fallback_url, part_number)
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
