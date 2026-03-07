"""Typed wrappers for Notion MCP server tools."""

from typing import Any

import structlog

from src.notion_mcp.client import NotionMCPClient

logger = structlog.get_logger()


class NotionMCPTools:
    """Typed wrappers for each Notion MCP tool."""

    def __init__(self, client: NotionMCPClient):
        self.client = client

    def _extract_text(self, result: Any) -> str:
        """Extract text content from MCP tool result."""
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                texts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                return "\n".join(texts)
            return str(result)
        return str(result)

    async def search(self, query: str) -> str:
        """Search across Notion workspace by title."""
        result = await self.client.call_tool("API-post-search", {"query": query})
        return self._extract_text(result)

    async def fetch_page(self, page_id: str) -> str:
        """Fetch page content."""
        result = await self.client.call_tool(
            "API-retrieve-a-page", {"page_id": page_id}
        )
        return self._extract_text(result)

    async def create_page(
        self,
        parent_id: str,
        title: str,
        properties: dict[str, Any] | None = None,
        content: str = "",
    ) -> Any:
        """Create a new page in a database with properties."""
        page_props: dict[str, Any] = properties.copy() if properties else {}
        # Ensure title property is set
        if "Name" not in page_props:
            page_props["Name"] = {"title": [{"text": {"content": title}}]}

        args: dict[str, Any] = {
            "parent": {"database_id": parent_id},
            "properties": page_props,
        }
        result = await self.client.call_tool("API-post-page", args)
        logger.info("notion_page_created", title=title, parent_id=parent_id)
        return result

    async def update_page(
        self,
        page_id: str,
        properties: dict[str, Any] | None = None,
    ) -> Any:
        """Update page properties."""
        args: dict[str, Any] = {"page_id": page_id}
        if properties:
            args["properties"] = properties
        result = await self.client.call_tool("API-patch-page", args)
        logger.info("notion_page_updated", page_id=page_id)
        return result

    async def add_comment(self, page_id: str, comment: str) -> Any:
        """Add a comment to a page (used for timeline entries)."""
        result = await self.client.call_tool(
            "API-create-a-comment",
            {
                "parent": {"page_id": page_id},
                "rich_text": [{"text": {"content": comment}}],
            },
        )
        logger.info("notion_comment_added", page_id=page_id)
        return result

    async def list_comments(self, page_id: str) -> Any:
        """List all comments on a page."""
        result = await self.client.call_tool(
            "API-retrieve-a-comment", {"block_id": page_id}
        )
        return result

    async def query_database(
        self, database_id: str, filter_params: dict[str, Any] | None = None,
        page_size: int | None = None,
    ) -> Any:
        """Query a database."""
        args: dict[str, Any] = {"data_source_id": database_id}
        if filter_params:
            args["filter"] = filter_params
        if page_size:
            args["page_size"] = page_size
        result = await self.client.call_tool("API-query-data-source", args)
        return result
