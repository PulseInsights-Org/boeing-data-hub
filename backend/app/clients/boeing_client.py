"""
Boeing HTTP client — OAuth authentication and REST API calls.
Version: 1.0.0
"""
from typing import Any, Dict

import httpx
from fastapi import HTTPException

from app.core.config import Settings


class BoeingClient:
    def __init__(self, settings: Settings) -> None:
        self._oauth_token_url = settings.boeing_oauth_token_url
        self._client_id = settings.boeing_client_id
        self._client_secret = settings.boeing_client_secret
        self._scope = settings.boeing_scope
        self._pna_oauth_url = settings.boeing_pna_oauth_url
        self._pna_price_url = settings.boeing_pna_price_url
        self._username = settings.boeing_username
        self._password = settings.boeing_password

    async def _get_oauth_access_token(self) -> str:
        if not (self._client_id and self._client_secret):
            raise HTTPException(
                status_code=500,
                detail="BOEING_CLIENT_ID and BOEING_CLIENT_SECRET env vars are required",
            )

        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": self._scope,
            "grant_type": "client_credentials",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self._oauth_token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Boeing OAuth error: {resp.text}",
            )

        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise HTTPException(status_code=500, detail="No access_token in Boeing OAuth response")
        return token

    async def _get_part_access_token(self, access_token: str) -> str:
        if not (self._username and self._password):
            raise HTTPException(
                status_code=500,
                detail="BOEING_USERNAME and BOEING_PASSWORD env vars are required",
            )

        headers = {
            "x-username": self._username,
            "x-password": self._password,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(self._pna_oauth_url, headers=headers)

        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Boeing PNA oauth error: {resp.text}",
            )

        part_token = resp.headers.get("x-part-access-token")
        if not part_token:
            try:
                body = resp.json()
            except Exception:
                body = {}
            part_token = body.get("x-part-access-token") or body.get("access_token")

        if not part_token:
            raise HTTPException(status_code=500, detail="Missing x-part-access-token in Boeing response")

        return part_token

    async def fetch_price_availability(self, query: str) -> Dict[str, Any]:
        access_token = await self._get_oauth_access_token()
        part_access_token = await self._get_part_access_token(access_token)

        body = {
            "showNoStock": True,
            "showLocation": True,
            "productCodes": [query],
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "x-boeing-parts-authorization": part_access_token,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self._pna_price_url, headers=headers, json=body)

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        return resp.json()

    async def fetch_price_availability_batch(self, part_numbers: list[str]) -> Dict[str, Any]:
        """
        Fetch price and availability for multiple part numbers in a single API call.

        This is the batch version of fetch_price_availability, used by Celery workers
        to process chunks of part numbers efficiently.

        Args:
            part_numbers: List of part numbers to fetch (max recommended: 10-20)

        Returns:
            dict: Raw Boeing API response containing lineItems for each part number
        """
        access_token = await self._get_oauth_access_token()
        part_access_token = await self._get_part_access_token(access_token)

        body = {
            "showNoStock": True,
            "showLocation": True,
            "productCodes": part_numbers,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "x-boeing-parts-authorization": part_access_token,
        }

        # Use longer timeout for batch requests
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self._pna_price_url, headers=headers, json=body)

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        return resp.json()
