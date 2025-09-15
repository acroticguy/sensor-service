"""
Common HTTP utilities for the lidar service
"""
import httpx
from typing import Dict, Any, Optional
from ..core.logging_config import logger


async def make_postgrest_request(
    url: str,
    payload: Dict[str, Any],
    timeout: int = 10
) -> Dict[str, Any]:
    """
    Make a POST request to PostgREST endpoint

    Args:
        url: The PostgREST URL
        payload: The JSON payload to send
        timeout: Request timeout in seconds

    Returns:
        Response data as dictionary

    Raises:
        httpx.RequestError: For network errors
        httpx.HTTPStatusError: For HTTP errors
    """
    headers = {'Content-Type': 'application/json'}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def get_lasers_by_berth(berth_id: int, db_host: str) -> list:
    """
    Get all lasers associated with a berth via PostgREST

    Args:
        berth_id: The berth ID to query
        db_host: The database host URL

    Returns:
        List of laser data
    """
    postgrest_url = f"{db_host}/rpc/get_lasers_by_berth"
    payload = {"p_berth_id": berth_id}

    try:
        return await make_postgrest_request(postgrest_url, payload)
    except httpx.RequestError as req_err:
        logger.exception(f"Network error querying lasers for berth {berth_id}: {req_err}")
        raise
    except httpx.HTTPStatusError as http_err:
        logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {http_err.response.status_code} - {http_err.response.text}")
        raise


class SharedHTTPClient:
    """Shared HTTP client for making multiple requests efficiently"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def post(self, url: str, json_data: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Make a POST request"""
        if not self._client:
            raise RuntimeError("HTTP client not initialized. Use as async context manager.")

        request_headers = {'Content-Type': 'application/json'}
        if headers:
            request_headers.update(headers)

        response = await self._client.post(url, headers=request_headers, json=json_data)
        response.raise_for_status()
        return response.json()