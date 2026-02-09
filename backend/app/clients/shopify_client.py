import logging
from typing import Any, Dict, Optional
import httpx
from fastapi import HTTPException

from app.core.config import Settings

logger = logging.getLogger("shopify_client")

class ShopifyClient:
    def __init__(self, settings: Settings) -> None:
        raw_domain = settings.shopify_store_domain
        self._store_domain = self._normalize_store_domain(raw_domain)
        self._token = settings.shopify_admin_api_token
        self._api_version = settings.shopify_api_version
        self._location_map = {}
        self._location_name_map = settings.shopify_location_map or {}
        # Map Boeing location names to 3-char inventory location codes for metafield
        # Example: {"Dallas Central": "1D1", "Chicago Warehouse": "CHI"}
        self._inventory_location_codes = settings.shopify_inventory_location_codes or {}
        logger.info(f"ShopifyClient initialized: domain={self._store_domain} (raw: {raw_domain}), inventory_location_codes: {self._inventory_location_codes}")

    @staticmethod
    def _normalize_store_domain(domain: Optional[str]) -> Optional[str]:
        """
        Normalize Shopify store domain to ensure it has .myshopify.com suffix.

        Handles these formats:
        - "my-store" -> "my-store.myshopify.com"
        - "my-store.myshopify.com" -> "my-store.myshopify.com" (unchanged)
        - "https://my-store.myshopify.com" -> "my-store.myshopify.com" (strips protocol)
        """
        if not domain:
            return domain

        # Strip protocol if present
        domain = domain.replace("https://", "").replace("http://", "")

        # Strip trailing slashes
        domain = domain.rstrip("/")

        # Add .myshopify.com if not present
        if not domain.endswith(".myshopify.com"):
            domain = f"{domain}.myshopify.com"

        return domain

    async def _get_location_map(self) -> Dict[str, int]:
        if self._location_map:
            return self._location_map
        data = await self._call_shopify("GET", "/locations.json")
        locations = data.get("locations") or []
        self._location_map = {
            loc.get("name"): int(loc.get("id"))
            for loc in locations
            if loc.get("name") and loc.get("id") is not None
        }
        logger.info("shopify locations loaded=%s", list(self._location_map.keys()))
        return self._location_map

    async def _set_inventory_levels(
        self,
        inventory_item_id: int,
        location_quantities: list[dict],
    ) -> None:
        if not location_quantities:
            return
        try:
            location_map = await self._get_location_map()
        except HTTPException as exc:
            logger.info("shopify inventory levels skipped error=%s", exc.detail)
            return
        matched = 0
        total_qty = 0
        for loc in location_quantities:
            loc_name = loc.get("location")
            qty = loc.get("quantity")
            if loc_name is None or qty is None:
                continue
            total_qty += int(qty)
            mapped_name = self._location_name_map.get(loc_name, loc_name)
            location_id = location_map.get(mapped_name)
            if location_id is None:
                logger.info("shopify location missing name=%s mapped=%s", loc_name, mapped_name)
                continue
            payload = {
                "location_id": location_id,
                "inventory_item_id": inventory_item_id,
                "available": int(qty),
            }
            await self._call_shopify("POST", "/inventory_levels/set.json", json=payload)
            matched += 1

        if matched == 0 and location_map:
            fallback_location_id = next(iter(location_map.values()))
            payload = {
                "location_id": fallback_location_id,
                "inventory_item_id": inventory_item_id,
                "available": int(total_qty),
            }
            logger.info("shopify inventory fallback location_id=%s total_qty=%s", fallback_location_id, total_qty)
            await self._call_shopify("POST", "/inventory_levels/set.json", json=payload)

    async def _set_inventory_cost(self, inventory_item_id: int, cost_per_item: float | None) -> None:
        if cost_per_item is None:
            return
        payload = {
            "inventory_item": {
                "id": inventory_item_id,
                "cost": float(cost_per_item),
            }
        }
        await self._call_shopify("PUT", f"/inventory_items/{inventory_item_id}.json", json=payload)

    def _to_gid(self, entity: str, value: str | int) -> str:
        if isinstance(value, str) and value.startswith("gid://"):
            return value
        return f"gid://shopify/{entity}/{value}"

    async def _set_product_category(self, product_id: int) -> None:
        """Set product category to 'Aircraft Parts & Accessories' using GraphQL.

        Uses the category ID field directly as per Shopify's ProductInput specification.
        The category ID 'gid://shopify/TaxonomyCategory/vp-1-1' corresponds to:
        Vehicles & Parts > Vehicle Parts & Accessories > Aircraft Parts & Accessories

        Note: category field in ProductInput requires API version 2024-10+.
        """
        # Category ID for "Aircraft Parts & Accessories"
        # Full path: Vehicles & Parts > Vehicle Parts & Accessories > Aircraft Parts & Accessories
        # See: https://shopify.github.io/product-taxonomy/
        category_gid = "gid://shopify/TaxonomyCategory/vp-1-1"

        mutation = """
            mutation productUpdate($input: ProductInput!) {
                productUpdate(input: $input) {
                    product {
                        id
                        category {
                            id
                            name
                            fullName
                        }
                    }
                    userErrors { field message }
                }
            }
        """
        variables = {
            "input": {
                "id": self._to_gid("Product", product_id),
                "category": category_gid,
            }
        }

        try:
            result = await self._call_shopify_graphql(mutation, variables)
            errors = (result.get("data") or {}).get("productUpdate", {}).get("userErrors") or []
            if errors:
                logger.info("shopify category set failed product_id=%s errors=%s", product_id, errors)
            else:
                category = (result.get("data") or {}).get("productUpdate", {}).get("product", {}).get("category")
                logger.info("shopify category set success product_id=%s category=%s", product_id, category)
        except HTTPException as exc:
            logger.info("shopify category set error product_id=%s detail=%s", product_id, exc.detail)

    async def _set_inventory_levels_graphql(
        self,
        inventory_item_id: str | int,
        location_quantities: list[dict],
    ) -> None:
        if not location_quantities:
            return
        try:
            location_map = await self._get_location_map()
        except HTTPException as exc:
            logger.info("shopify inventory levels skipped error=%s", exc.detail)
            return

        matched = 0
        total_qty = 0
        set_quantities = []
        for loc in location_quantities:
            loc_name = loc.get("location")
            qty = loc.get("quantity")
            if loc_name is None or qty is None:
                continue
            total_qty += int(qty)
            mapped_name = self._location_name_map.get(loc_name, loc_name)
            location_id = location_map.get(mapped_name)
            if location_id is None:
                logger.info("shopify location missing name=%s mapped=%s", loc_name, mapped_name)
                continue
            set_quantities.append(
                {
                    "inventoryItemId": self._to_gid("InventoryItem", inventory_item_id),
                    "locationId": self._to_gid("Location", location_id),
                    "quantity": int(qty),
                }
            )
            matched += 1

        if matched == 0 and location_map:
            fallback_location_id = next(iter(location_map.values()))
            set_quantities.append(
                {
                    "inventoryItemId": self._to_gid("InventoryItem", inventory_item_id),
                    "locationId": self._to_gid("Location", fallback_location_id),
                    "quantity": int(total_qty),
                }
            )
            logger.info("shopify inventory fallback location_id=%s total_qty=%s", fallback_location_id, total_qty)

        if not set_quantities:
            return

        mutation = (
            "mutation InventorySetOnHand($input: InventorySetOnHandQuantitiesInput!) { "
            "inventorySetOnHandQuantities(input: $input) { userErrors { field message } } }"
        )
        variables = {
            "input": {
                "reason": "correction",
                "setQuantities": set_quantities,
            }
        }
        data = await self._call_shopify_graphql(mutation, variables)
        errors = (data.get("data") or {}).get("inventorySetOnHandQuantities", {}).get("userErrors") or []
        if errors:
            raise HTTPException(status_code=502, detail=str(errors))

    def _base_url(self) -> str:
        if not self._store_domain or not self._token:
            raise HTTPException(status_code=500, detail="Shopify env vars missing")
        return f"https://{self._store_domain}/admin/api/{self._api_version}"

    async def _call_shopify(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = self._base_url()
        url = f"{base}{path}"
        logger.info("shopify request method=%s path=%s params=%s", method, path, params)

        headers = {
            "X-Shopify-Access-Token": self._token or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method=method, url=url, headers=headers, json=json, params=params)

        logger.info("shopify response status=%s path=%s", resp.status_code, path)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        if resp.text:
            return resp.json()
        return {}

    async def _call_shopify_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        data = await self._call_shopify("POST", "/graphql.json", json=payload)
        if data.get("errors"):
            raise HTTPException(status_code=502, detail=str(data.get("errors")))
        return data

    def _map_unit_of_measure(self, uom: str) -> str:
        """Map Boeing UOM values to Shopify allowed choices.

        Shopify custom.unit_of_measure is a Choice list with allowed values:
        - EA, Inches, Pound, Pack (1PK = 25EA)

        Returns empty string if no valid mapping found (field will be skipped).
        """
        if not uom:
            return ""

        uom_upper = uom.upper().strip()

        # Direct mappings
        if uom_upper in ("EA", "EACH"):
            return "EA"
        if uom_upper in ("IN", "INCH", "INCHES"):
            return "Inches"
        if uom_upper in ("LB", "LBS", "POUND", "POUNDS"):
            return "Pound"
        if uom_upper in ("PK", "PACK"):
            return "Pack (1PK = 25EA)"

        # Default to EA for common unit types
        if uom_upper in ("PC", "PCS", "PIECE", "PIECES", "UNIT", "UNITS"):
            return "EA"

        # If no mapping found, skip this field to avoid validation errors
        logger.info(f"shopify UOM not mapped, skipping: {uom}")
        return ""

    def _map_cert(self, cert: str) -> str:
        """Map cert values to Shopify allowed choices.

        Shopify custom.cert (tracedoc) is a Choice list with allowed values:
        - EASA Form 1, FAA 8130-3, Brazil Form SEGV00 003, OEM Cert,
        - 121 Trace, 129 Trace, 145 Trace, Transport Canada Form 1, C of C, CAA UK

        Returns empty string if no valid mapping found (field will be skipped).
        """
        if not cert:
            return ""

        cert_upper = cert.upper().strip()

        # Direct/partial mappings
        if "8130" in cert_upper or "FAA" in cert_upper:
            return "FAA 8130-3"
        if "EASA" in cert_upper:
            return "EASA Form 1"
        if "BRAZIL" in cert_upper or "SEGV" in cert_upper:
            return "Brazil Form SEGV00 003"
        if "OEM" in cert_upper:
            return "OEM Cert"
        if "121" in cert_upper:
            return "121 Trace"
        if "129" in cert_upper:
            return "129 Trace"
        if "145" in cert_upper:
            return "145 Trace"
        if "CANADA" in cert_upper or "TRANSPORT" in cert_upper:
            return "Transport Canada Form 1"
        if "C OF C" in cert_upper or "COC" in cert_upper or "CERTIFICATE OF CONFORMANCE" in cert_upper:
            return "C of C"
        if "CAA" in cert_upper and "UK" in cert_upper:
            return "CAA UK"

        # Default to FAA 8130-3 for aerospace parts
        return "FAA 8130-3"

    def _validate_trace_url(self, trace: str) -> str:
        """Validate trace URL against Shopify allowed domains.

        Shopify custom.tracedoc (Link type) only allows:
        - https://cdn.shopify.com/
        - https://www.getsmartcert.com/

        Returns empty string if URL doesn't match allowed domains.
        """
        if not trace:
            return ""

        trace = trace.strip()

        # Check if URL matches allowed domains
        allowed_domains = [
            "https://cdn.shopify.com/",
            "https://www.getsmartcert.com/",
        ]

        for domain in allowed_domains:
            if trace.startswith(domain):
                return trace

        # URL not from allowed domain, skip to avoid validation error
        logger.info(f"shopify trace URL not from allowed domain, skipping: {trace}")
        return ""

    def _map_inventory_location(self, location: str, location_id: str = "") -> str:
        """Map inventory location to exactly 3 characters.

        Shopify custom.inventory_location has Character limit: Min 3, Max 3.
        This field stores a location ID (e.g., "1D1"), not a location name.

        Args:
            location: Full location string like "Dallas Central: 106"
            location_id: Optional pre-defined location ID (exactly 3 chars)

        Returns:
            3-character location ID or empty string if not available

        Configuration:
            Set SHOPIFY_INVENTORY_LOCATION_CODES env var as JSON:
            {"Dallas Central": "1D1", "Chicago Warehouse": "CHI"}
        """
        logger.info(f"shopify _map_inventory_location called: location='{location}', location_id='{location_id}', codes={self._inventory_location_codes}")

        # If a location_id is explicitly provided and valid, use it
        if location_id and len(location_id.strip()) == 3:
            logger.info(f"shopify inventory_location using provided location_id: {location_id.strip()}")
            return location_id.strip()

        # If the location string itself is exactly 3 chars, it might be an ID
        if location and len(location.strip()) == 3:
            logger.info(f"shopify inventory_location using 3-char location string: {location.strip()}")
            return location.strip()

        # Try to map using the configured location codes
        if location and self._inventory_location_codes:
            # Extract just the location name (before the colon if present)
            # e.g., "Dallas Central: 106" -> "Dallas Central"
            # Handle multiple locations (semicolon-separated) - use the first one
            first_location = location.split(";")[0].strip() if ";" in location else location
            location_name = first_location.split(":")[0].strip() if ":" in first_location else first_location.strip()

            logger.info(f"shopify inventory_location extracted name: '{location_name}' from '{location}'")

            # Check for exact match
            if location_name in self._inventory_location_codes:
                code = self._inventory_location_codes[location_name]
                logger.info(f"shopify inventory_location exact match found: '{location_name}' -> '{code}'")
                if len(code) == 3:
                    return code

            # Check for partial match (case-insensitive)
            location_upper = location_name.upper()
            for name, code in self._inventory_location_codes.items():
                if name.upper() in location_upper or location_upper in name.upper():
                    logger.info(f"shopify inventory_location partial match found: '{name}' -> '{code}'")
                    if len(code) == 3:
                        return code

        # Cannot determine location ID - skip this field
        if location:
            logger.info(f"shopify inventory_location skipped - no mapping for: '{location}', available codes: {self._inventory_location_codes}")
        return ""

    def _build_metafields(self, product: Dict[str, Any]) -> list[Dict[str, Any]]:
        """Build metafields using 'custom' namespace matching Shopify admin definitions.

        Shopify Metafield Definitions and Validations (from store):
        - inventory_location: single_line_text_field (no validation)
        - part_number: single_line_text_field (no validation)
        - alternate_part_number: single_line_text_field (no validation)
        - unit_of_measure: Choice list - EA, Inches, Pound, Pack (1PK = 25EA)
        - condition: single_line_text_field (max 3 chars - NE, OH, SV, etc.)
        - cert: Choice list - EASA Form 1, FAA 8130-3, etc.
        - trace (tracedoc): Link (url) - Limited to: cdn.shopify.com, getsmartcert.com
        - manufacturer: single_line_text_field (no validation)
        - expiration_date: date (YYYY-MM-DD format)
        - pma: boolean (True or False)
        - estimated_lead_time: number_integer
        - notes: multi_line_text_field (no validation)

        IMPORTANT: Some fields have strict validation. We skip fields that would fail validation.
        """
        shopify_data = product.get("shopify") or {}

        # Get the raw SKU (e.g., "4811-160-3=E9")
        raw_sku = shopify_data.get("sku") or product.get("sku") or product.get("partNumber") or ""

        # Part number: strip the =XX suffix (e.g., "4811-160-3=E9" -> "4811-160-3")
        part_number = raw_sku.split("=")[0] if "=" in raw_sku else raw_sku

        # Alternate part number: should be blank (not pushed)
        title = shopify_data.get("title") or product.get("title") or ""
        alternate_part_number = ""

        manufacturer = shopify_data.get("manufacturer") or product.get("manufacturer") or product.get("supplier_name") or ""
        notes = shopify_data.get("notes") or product.get("notes") or ""
        expiration_date = shopify_data.get("expiration_date") or product.get("expiration_date") or ""

        # Inventory location: Must be EXACTLY 3 characters (e.g., "1D1")
        # The Shopify metafield has Min 3, Max 3 character limit
        # This is a location ID, not a location name
        raw_location = shopify_data.get("location_summary") or product.get("location_summary") or ""
        location_id = shopify_data.get("location_id") or product.get("location_id") or ""
        inventory_location = self._map_inventory_location(raw_location, location_id)

        # Condition: Must be max 3 characters (NE, OH, SV, AR, etc.)
        raw_condition = product.get("condition") or "NE"
        condition = raw_condition[:2] if len(raw_condition) > 3 else raw_condition  # Truncate to 2 chars to be safe

        # Map values to Shopify allowed choices
        raw_uom = shopify_data.get("unit_of_measure") or product.get("baseUOM") or product.get("base_uom") or ""
        unit_of_measure = self._map_unit_of_measure(raw_uom)

        # Build metafields list - only include fields with valid values
        metafields = []

        # Fields with no validation constraints (always include if non-empty)
        # part_name: Boeing "name" field (e.g., "Proponent® 2138-0123-1 Plug, Rubber, For Aircrafts")
        part_name = product.get("name") or ""
        simple_fields = [
            ("part_number", part_number, "single_line_text_field"),
            ("part_name", part_name, "single_line_text_field"),
            ("alternate_part_number", alternate_part_number, "single_line_text_field"),
            ("manufacturer", manufacturer, "single_line_text_field"),
            ("notes", notes, "multi_line_text_field"),
        ]

        for key, value, mtype in simple_fields:
            if str(value).strip():
                metafields.append({
                    "namespace": "custom",
                    "key": key,
                    "value": str(value),
                    "type": mtype,
                })

        # Inventory location: EXACTLY 3 characters required (Min 3, Max 3)
        if inventory_location and len(inventory_location) == 3:
            metafields.append({
                "namespace": "custom",
                "key": "inventory_location",
                "value": inventory_location,
                "type": "single_line_text_field",
            })

        # Condition: max 3 characters
        if condition and len(condition) <= 3:
            metafields.append({
                "namespace": "custom",
                "key": "condition",
                "value": condition,
                "type": "single_line_text_field",
            })

        # Unit of measure: Choice list field - only include if mapped to valid choice
        if unit_of_measure:
            metafields.append({
                "namespace": "custom",
                "key": "unit_of_measure",
                "value": unit_of_measure,
                "type": "single_line_text_field",
            })

        # Cert field: Choice list with key "trace" (not "cert")
        # Valid values: EASA Form 1, FAA 8130-3, Brazil Form SEGV00 003, OEM Cert,
        #               121 Trace, 129 Trace, 145 Trace, Transport Canada Form 1, C of C, CAA UK
        raw_cert = shopify_data.get("cert") or product.get("cert") or "FAA 8130-3"
        cert = self._map_cert(raw_cert)
        if cert:
            metafields.append({
                "namespace": "custom",
                "key": "trace",  # Note: Shopify key is 'trace' for the Cert field (custom.trace)
                "value": cert,
                "type": "single_line_text_field",
            })

        # URL field (tracedoc) - only include if from allowed domain
        raw_trace_url = shopify_data.get("trace") or product.get("trace") or ""
        trace_url = self._validate_trace_url(raw_trace_url)
        if trace_url:
            metafields.append({
                "namespace": "custom",
                "key": "tracedoc",  # Note: key is 'tracedoc' for the URL/Link field
                "value": trace_url,
                "type": "url",
            })

        # Date field - only include if valid format
        if expiration_date:
            metafields.append({
                "namespace": "custom",
                "key": "expiration_date",
                "value": str(expiration_date),
                "type": "date",
            })

        # Estimated lead time (in days) - Integer field
        estimated_lead_time = (
            shopify_data.get("estimated_lead_time_days")
            or product.get("estimated_lead_time_days")
            or 60  # Default to 60 days
        )
        if estimated_lead_time is not None:
            metafields.append({
                "namespace": "custom",
                "key": "estimated_lead_time",
                "value": str(int(estimated_lead_time)),
                "type": "number_integer",
            })
            logger.info(f"shopify estimated_lead_time added: {estimated_lead_time}")

        return metafields

    def to_shopify_product_body(self, product: Dict[str, Any]) -> Dict[str, Any]:
        shopify_data = product.get("shopify") or {}
        title = shopify_data.get("title") or product.get("title") or product.get("name") or product.get("partNumber")
        description = shopify_data.get("description") or product.get("description") or ""
        body_html = shopify_data.get("body_html") or (f"<p>{description}</p>" if description.strip() else "")
        manufacturer = shopify_data.get("manufacturer") or product.get("manufacturer") or ""
        vendor = shopify_data.get("vendor") or product.get("vendor") or ""
        country_of_origin = shopify_data.get("country_of_origin") or product.get("countryOfOrigin") or ""

        # Pricing logic: cost_per_item is base Boeing listPrice (if available) otherwise netPrice/price;
        # storefront price is 1.1x that base cost unless explicitly overridden.
        base_cost = (
            shopify_data.get("cost_per_item")
            or product.get("list_price")
            or product.get("net_price")
            or product.get("price")
            or 0
        )
        price = shopify_data.get("price") or (base_cost * 1.1 if base_cost else 0)
        inventory = shopify_data.get("inventory_quantity") or product.get("inventory") or product.get("inventory_quantity") or 0
        weight = shopify_data.get("weight") or product.get("weight") or 0
        weight_unit = shopify_data.get("weight_uom") or product.get("weightUnit") or "lb"

        part_number = shopify_data.get("sku") or product.get("sku") or product.get("partNumber") or ""
        dim = {
            "length": shopify_data.get("length") or product.get("length") or product.get("dim_length"),
            "width": shopify_data.get("width") or product.get("width") or product.get("dim_width"),
            "height": shopify_data.get("height") or product.get("height") or product.get("dim_height"),
            "unit": shopify_data.get("dim_uom") or product.get("dimensionUom") or product.get("dim_uom"),
        }

        base_uom = shopify_data.get("unit_of_measure") or product.get("baseUOM") or product.get("base_uom") or ""
        hazmat_code = product.get("hazmatCode") or product.get("hazmat_code") or ""
        faa_approval = product.get("faaApprovalCode") or product.get("faa_approval_code") or ""
        eccn = product.get("eccn") or ""
        schedule_b_code = product.get("schedule_b_code") or ""
        condition = product.get("condition") or "NE"
        pma = product.get("pma") or False
        lead_time = product.get("estimated_lead_time_days") or product.get("estimatedLeadTimeDays") or 3
        trace = shopify_data.get("trace") or product.get("trace") or ""
        expiration_date = shopify_data.get("expiration_date") or product.get("expiration_date") or ""
        notes = shopify_data.get("notes") or product.get("notes") or ""

        tags = [
            "boeing",
            "aerospace",
            "Aircraft Parts & Accessories",
        ]
        if country_of_origin:
            tags.append(f"origin-{country_of_origin.lower().replace(' ', '-')}")

        images = []
        # Use only the image_url (which should be either Supabase-hosted or placeholder)
        # Don't add Boeing URLs directly as Shopify can't fetch them
        primary_image = product.get("image_url") or shopify_data.get("product_image") or product.get("product_image")
        if primary_image:
            images.append({"src": primary_image})
        # Only add thumbnail if it's different AND not a Boeing/aviall URL (which Shopify can't fetch)
        thumbnail_image = shopify_data.get("thumbnail_image") or product.get("thumbnail_image")
        if thumbnail_image and thumbnail_image != primary_image:
            # Skip Boeing/aviall URLs - Shopify can't fetch them
            if "aviall.com" not in thumbnail_image and "boeing.com" not in thumbnail_image:
                images.append({"src": thumbnail_image})
        if not images:
            images.append(
                {
                    "src": "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto",
                }
            )
        logger.info("shopify product images=%s", images)

        # build a simple human-readable inventory location summary, if provided
        location_quantities = (shopify_data.get("location_quantities") or [])
        inventory_location_str = shopify_data.get("location_summary") or ""
        if not inventory_location_str and location_quantities:
            inventory_location_str = ", ".join(
                f"{loc.get('location')}: {loc.get('quantity')}"
                for loc in location_quantities
                if loc.get("location") and loc.get("quantity") is not None
            )

        payload = {
            "product": {
                "title": title,
                "body_html": body_html,
                "vendor": "",
                "product_type": "Aircraft Parts & Accessories",
                "tags": tags,
                "images": images,
                "variants": [
                    {
                        "sku": part_number,
                        "price": str(price),
                        "inventory_management": "shopify",
                        "inventory_quantity": int(inventory),
                        "weight": float(weight) if weight else 0,
                        "weight_unit": "kg" if weight_unit == "kg" else "lb",
                    }
                ],
                "metafields": [
                    {
                        "namespace": "boeing",
                        "key": "part_number",
                        "value": part_number,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "alternate_part_number",
                        "value": "",
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "dimensions",
                        "value": str(dim),
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "distribution_source",
                        "value": product.get("supplierName") or product.get("supplier_name") or "",
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "country_of_origin",
                        "value": country_of_origin,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "unit_of_measure",
                        "value": base_uom,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "manufacturer",
                        "value": shopify_data.get("manufacturer") or product.get("manufacturer") or product.get("supplier_name") or "",
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "hazmat_code",
                        "value": hazmat_code,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "faa_approval_code",
                        "value": faa_approval,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "eccn",
                        "value": eccn,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "schedule_b_code",
                        "value": schedule_b_code,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "description",
                        "value": shopify_data.get("description") or product.get("name") or "",
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "condition",
                        "value": condition,
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "pma",
                        "value": str(pma).lower(),
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "estimated_lead_time",
                        "value": str(lead_time),
                        "type": "number_integer",
                    },
                    {
                        "namespace": "boeing",
                        "key": "cert",
                        "value": shopify_data.get("cert") or product.get("cert") or "FAA 8130-3",
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "trace",
                        "value": trace,
                        "type": "url",
                    },
                    {
                        "namespace": "boeing",
                        "key": "expiration_date",
                        "value": expiration_date,
                        "type": "date",
                    },
                    {
                        "namespace": "boeing",
                        "key": "notes",
                        "value": notes,
                        "type": "multi_line_text_field",
                    },
                    {
                        "namespace": "boeing",
                        "key": "inventory_location",
                        "value": inventory_location_str,
                        "type": "single_line_text_field",
                    },
                ],
            }
        }
        payload["product"]["metafields"] = self._build_metafields(product)
        return payload

    async def create_metafield_definitions(self) -> None:
        """Create metafield definitions using 'custom' namespace to match Shopify admin."""
        definitions = [
            {"namespace": "custom", "key": "part_number", "name": "Part Number", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "alternate_part_number", "name": "Alternate part number", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "dimensions", "name": "Dimensions", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "distribution_source", "name": "Distribution Source", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "country_of_origin", "name": "Country Of Origin", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "unit_of_measure", "name": "Unit of measure", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "manufacturer", "name": "Manufacturer", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "hazmat_code", "name": "Hazmat Code", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "faa_approval_code", "name": "FAA Approval Code", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "eccn", "name": "ECCN", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "schedule_b_code", "name": "Schedule B Code", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "description", "name": "Description", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "condition", "name": "Condition", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "pma", "name": "PMA", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "estimated_lead_time", "name": "Estimated lead time (in days)", "type": "number_integer"},
            {"namespace": "custom", "key": "cert", "name": "Cert", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "inventory_location", "name": "Inventory location", "type": "single_line_text_field"},
            {"namespace": "custom", "key": "trace", "name": "Trace", "type": "url"},
            {"namespace": "custom", "key": "expiration_date", "name": "Expiration date", "type": "date"},
            {"namespace": "custom", "key": "notes", "name": "Notes", "type": "multi_line_text_field"},
        ]

        for definition in definitions:
            payload = {"metafield_definition": {**definition, "owner_type": "product"}}
            try:
                await self._call_shopify("POST", "/metafield_definitions.json", json=payload)
            except HTTPException as exc:
                # 422: definition already exists; 406: not acceptable/unsupported via REST on this store.
                if exc.status_code in (406, 422):
                    logger.info(
                        "shopify metafield definition skipped status=%s key=%s detail=%s",
                        exc.status_code,
                        definition["key"],
                        exc.detail,
                    )
                    continue
                raise

    async def publish_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Publish product using REST API for better metafield support."""
        body = self.to_shopify_product_body(product)
        logger.info("shopify publish payload=%s", body)

        # Use REST API to create product with metafields in one call
        data = await self._call_shopify("POST", "/products.json", json=body)
        shopify_product = data.get("product") or {}
        product_id = shopify_product.get("id")

        logger.info("shopify product created id=%s", product_id)

        # Set product category using GraphQL (REST API doesn't support standardized categories)
        if product_id:
            await self._set_product_category(product_id)

        # Set inventory levels if we have location quantities
        variants = shopify_product.get("variants") or []
        location_quantities = (product.get("shopify") or {}).get("location_quantities") or []
        if variants and location_quantities:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._set_inventory_levels(int(inventory_item_id), location_quantities)

        # Set cost per item
        cost_per_item = (product.get("shopify") or {}).get("cost_per_item")
        if variants and cost_per_item is not None:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._set_inventory_cost(int(inventory_item_id), cost_per_item)

        return {"product": {"id": product_id, "handle": shopify_product.get("handle")}}

    async def update_product(self, shopify_product_id: str, product: Dict[str, Any]) -> Dict[str, Any]:
        body = self.to_shopify_product_body(product)
        body["product"]["id"] = int(shopify_product_id)
        logger.info("shopify update payload id=%s payload=%s", shopify_product_id, body)
        data = await self._call_shopify("PUT", f"/products/{shopify_product_id}.json", json=body)
        shopify_product = data.get("product") or {}
        product_id = shopify_product.get("id")

        # Set product category using GraphQL (REST API doesn't support standardized categories)
        if product_id:
            await self._set_product_category(product_id)

        variants = shopify_product.get("variants") or []
        location_quantities = (product.get("shopify") or {}).get("location_quantities") or []
        if variants and location_quantities:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._set_inventory_levels(int(inventory_item_id), location_quantities)
        if variants:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._set_inventory_cost(int(inventory_item_id), (product.get("shopify") or {}).get("cost_per_item"))
        return data

    async def find_product_by_sku(self, sku: str) -> Optional[str]:
        params = {
            "limit": 50,
            "fields": "id,variants",
        }
        data = await self._call_shopify("GET", "/products.json", params=params)
        products = data.get("products") or []
        for p in products:
            for v in p.get("variants", []):
                if v.get("sku") == sku:
                    return str(p.get("id"))
        return None

    async def get_variant_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        query = (
            "query GetSkuData($skuQuery: String!) { "
            "productVariants(first: 5, query: $skuQuery) { "
            "edges { node { id sku title price compareAtPrice inventoryQuantity } } } }"
        )
        body = {
            "query": query,
            "variables": {"skuQuery": sku},
        }
        data = await self._call_shopify("POST", "/graphql.json", json=body)
        if not data:
            return None
        if data.get("errors"):
            raise HTTPException(status_code=502, detail=str(data.get("errors")))

        edges = (data.get("data") or {}).get("productVariants", {}).get("edges", [])
        if not edges:
            return None
        for edge in edges:
            node = edge.get("node") or {}
            if node.get("sku") == sku:
                return node
        return (edges[0].get("node") or {}) if edges else None

    async def delete_product(self, product_id: int | str) -> bool:
        """
        Delete a product from Shopify.

        Used for compensation/rollback when DB save fails after Shopify publish.
        This implements the Saga pattern for transaction safety.

        Args:
            product_id: Shopify product ID to delete

        Returns:
            bool: True if deletion was successful
        """
        try:
            await self._call_shopify("DELETE", f"/products/{product_id}.json")
            logger.info(f"shopify product deleted id={product_id}")
            return True
        except HTTPException as exc:
            logger.error(f"shopify product delete failed id={product_id} error={exc.detail}")
            raise

    async def update_product_pricing(
        self,
        shopify_product_id: str | int,
        price: float | None = None,
        quantity: int | None = None,
        metafields: list[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """
        Update just the price and/or inventory for a product.

        Used by sync scheduler to update price/availability without
        touching other product fields.

        Args:
            shopify_product_id: Shopify product ID
            price: New price (optional)
            quantity: New inventory quantity (optional)
            metafields: Optional metafields to update

        Returns:
            dict: Updated product data
        """
        # First get current product to update variant
        data = await self._call_shopify("GET", f"/products/{shopify_product_id}.json")
        shopify_product = data.get("product") or {}
        variants = shopify_product.get("variants") or []

        if not variants:
            raise HTTPException(status_code=404, detail="Product has no variants")

        variant = variants[0]
        variant_id = variant.get("id")

        # Build variant update payload
        variant_update = {"id": variant_id}
        if price is not None:
            variant_update["price"] = str(price)

        payload = {
            "product": {
                "id": int(shopify_product_id),
                "variants": [variant_update],
            }
        }

        if metafields:
            payload["product"]["metafields"] = metafields

        logger.info(f"shopify update_product_pricing id={shopify_product_id} payload={payload}")
        result = await self._call_shopify("PUT", f"/products/{shopify_product_id}.json", json=payload)

        # Update inventory if quantity provided
        if quantity is not None:
            inventory_item_id = variant.get("inventory_item_id")
            if inventory_item_id:
                await self.update_inventory(shopify_product_id, quantity, inventory_item_id)

        return result

    async def update_inventory(
        self,
        shopify_product_id: str | int,
        quantity: int,
        inventory_item_id: int | None = None,
    ) -> None:
        """
        Update inventory quantity for a product.

        Args:
            shopify_product_id: Shopify product ID
            quantity: New inventory quantity
            inventory_item_id: Optional inventory item ID (will fetch if not provided)
        """
        if inventory_item_id is None:
            # Fetch product to get inventory item ID
            data = await self._call_shopify("GET", f"/products/{shopify_product_id}.json")
            shopify_product = data.get("product") or {}
            variants = shopify_product.get("variants") or []
            if not variants:
                logger.warning(f"Product {shopify_product_id} has no variants, skipping inventory update")
                return
            inventory_item_id = variants[0].get("inventory_item_id")

        if not inventory_item_id:
            logger.warning(f"No inventory_item_id for product {shopify_product_id}")
            return

        # Get location map
        location_map = await self._get_location_map()
        if not location_map:
            logger.warning("No Shopify locations available")
            return

        # Set inventory at first/default location
        location_id = next(iter(location_map.values()))
        payload = {
            "location_id": location_id,
            "inventory_item_id": int(inventory_item_id),
            "available": quantity,
        }

        logger.info(f"shopify update_inventory product_id={shopify_product_id} qty={quantity}")
        await self._call_shopify("POST", "/inventory_levels/set.json", json=payload)

    async def update_inventory_by_location(
        self,
        shopify_product_id: str | int,
        location_quantities: list[dict],
    ) -> None:
        """
        Update inventory quantities per location for a product.

        Used by sync scheduler to update inventory levels at each location
        based on Boeing location availability data.

        Args:
            shopify_product_id: Shopify product ID
            location_quantities: List of dicts with 'location' and 'quantity' keys
                Example: [{"location": "Dallas Central", "quantity": 10}, ...]
        """
        if not location_quantities:
            return

        # Fetch product to get inventory item ID
        data = await self._call_shopify("GET", f"/products/{shopify_product_id}.json")
        shopify_product = data.get("product") or {}
        variants = shopify_product.get("variants") or []

        if not variants:
            logger.warning(f"Product {shopify_product_id} has no variants, skipping inventory update")
            return

        inventory_item_id = variants[0].get("inventory_item_id")
        if not inventory_item_id:
            logger.warning(f"No inventory_item_id for product {shopify_product_id}")
            return

        # Use existing _set_inventory_levels method
        await self._set_inventory_levels(int(inventory_item_id), location_quantities)
        logger.info(
            f"shopify update_inventory_by_location product_id={shopify_product_id} "
            f"locations={len(location_quantities)}"
        )
