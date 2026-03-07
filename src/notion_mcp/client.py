"""Async MCP client for Notion MCP Server (Streamable HTTP transport)."""

import asyncio
import itertools
import json as _json
import time
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = structlog.get_logger()


class MCPError(Exception):
    """Error from the MCP server."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP Error {code}: {message}")


class MCPSessionExpired(Exception):
    """MCP session has expired (410 Gone)."""


class MCPRateLimited(Exception):
    """MCP rate limit hit (429)."""


class NotionMCPClient:
    """
    Async client for Notion MCP Server via Streamable HTTP transport.

    Sends JSON-RPC 2.0 requests to the MCP endpoint.
    Manages session lifecycle and rate limiting.
    """

    PROTOCOL_VERSION = "2025-03-26"
    MAX_REQUESTS_PER_MIN = 180
    MAX_SEARCHES_PER_MIN = 30

    def __init__(self, mcp_url: str, auth_token: str):
        self._mcp_url = mcp_url
        self._auth_token = auth_token
        self._session_id: str | None = None
        self._id_counter = itertools.count(1)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._request_timestamps: list[float] = []
        self._search_timestamps: list[float] = []
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        return self._http

    @staticmethod
    def _parse_sse_response(text: str) -> dict[str, Any]:
        """Extract the last JSON-RPC message from an SSE stream."""
        last_data = None
        for line in text.splitlines():
            if line.startswith("data: "):
                last_data = line[6:]
        if last_data is None:
            raise MCPError(-1, f"No data line in SSE response: {text[:200]}")
        return _json.loads(last_data)

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _rate_limit(self, is_search: bool = False) -> None:
        """Enforce rate limits by waiting if necessary."""
        now = time.monotonic()
        window = 60.0

        # Clean old timestamps
        self._request_timestamps = [
            t for t in self._request_timestamps if now - t < window
        ]
        if is_search:
            self._search_timestamps = [
                t for t in self._search_timestamps if now - t < window
            ]

        # Wait if at limit
        if len(self._request_timestamps) >= self.MAX_REQUESTS_PER_MIN:
            wait_time = window - (now - self._request_timestamps[0])
            if wait_time > 0:
                logger.warning("mcp_rate_limit_wait", wait_seconds=round(wait_time, 1))
                await asyncio.sleep(wait_time)

        if is_search and len(self._search_timestamps) >= self.MAX_SEARCHES_PER_MIN:
            wait_time = window - (now - self._search_timestamps[0])
            if wait_time > 0:
                logger.warning(
                    "mcp_search_rate_limit_wait", wait_seconds=round(wait_time, 1)
                )
                await asyncio.sleep(wait_time)

        self._request_timestamps.append(time.monotonic())
        if is_search:
            self._search_timestamps.append(time.monotonic())

    async def _send_jsonrpc(
        self, method: str, params: dict | None = None
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the result."""
        http = await self._get_http()
        request_id = next(self._id_counter)
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        headers = self._build_headers()
        start = time.monotonic()

        try:
            response = await http.post(self._mcp_url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error("mcp_request_error", method=method, error=str(exc))
            raise

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if response.status_code == 410:
            logger.warning("mcp_session_expired", method=method)
            self._session_id = None
            self._initialized = False
            raise MCPSessionExpired()

        if response.status_code == 429:
            logger.warning("mcp_rate_limited", method=method)
            raise MCPRateLimited()

        response.raise_for_status()

        # Capture session ID from response headers
        if "mcp-session-id" in response.headers:
            self._session_id = response.headers["mcp-session-id"]

        # Parse response — may be plain JSON or SSE (text/event-stream)
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            body = self._parse_sse_response(response.text)
        else:
            body = response.json()
        logger.debug(
            "mcp_call",
            method=method,
            request_id=request_id,
            elapsed_ms=elapsed,
        )

        if "error" in body:
            err = body["error"]
            raise MCPError(err.get("code", -1), err.get("message", "Unknown error"), err.get("data"))

        return body.get("result", {})

    async def initialize(self) -> dict[str, Any]:
        """Establish MCP session with the server."""
        result = await self._send_jsonrpc(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "opslens", "version": "1.0.0"},
            },
        )
        self._initialized = True
        logger.info(
            "mcp_initialized",
            server_name=result.get("serverInfo", {}).get("name", "unknown"),
            session_id=self._session_id,
        )
        # Send initialized notification
        http = await self._get_http()
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        headers = self._build_headers()
        await http.post(self._mcp_url, json=notification, headers=headers)
        return result

    async def _ensure_initialized(self) -> None:
        """Auto-initialize if session not yet established."""
        if not self._initialized:
            async with self._init_lock:
                if not self._initialized:
                    await self.initialize()

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all available MCP tools."""
        await self._ensure_initialized()
        await self._rate_limit()
        result = await self._send_jsonrpc("tools/list")
        return result.get("tools", [])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((MCPRateLimited, httpx.RequestError)),
        reraise=True,
    )
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a Notion MCP tool by name with arguments."""
        try:
            await self._ensure_initialized()
        except MCPSessionExpired:
            await self.initialize()

        is_search = tool_name in ("notion-search",)
        await self._rate_limit(is_search=is_search)

        try:
            result = await self._send_jsonrpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
        except MCPSessionExpired:
            # Re-initialize and retry once
            await self.initialize()
            result = await self._send_jsonrpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )

        logger.info("mcp_tool_call", tool=tool_name)
        return result

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
