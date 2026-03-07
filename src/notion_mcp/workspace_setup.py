"""One-time Notion workspace bootstrapper using direct Notion API calls."""

import asyncio
import sys
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionAPIClient:
    """Direct async Notion API client."""

    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._http = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    async def create_database(self, parent_page_id: str, title: str, properties: dict) -> dict:
        resp = await self._http.post(
            f"{NOTION_API}/databases",
            json={
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "title": [{"type": "text", "text": {"content": title}}],
                "properties": properties,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def create_page(self, database_id: str, properties: dict, children: list | None = None) -> dict:
        body: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if children:
            body["children"] = children
        resp = await self._http.post(f"{NOTION_API}/pages", json=body)
        if resp.status_code != 200:
            logger.error("page_create_error", status=resp.status_code, body=resp.text[:500])
        resp.raise_for_status()
        return resp.json()

    async def get_child_databases(self, page_id: str) -> dict[str, str]:
        """Get existing child databases of a page. Returns {title: id}."""
        resp = await self._http.get(
            f"{NOTION_API}/blocks/{page_id}/children",
            params={"page_size": 100},
        )
        resp.raise_for_status()
        results = {}
        for block in resp.json().get("results", []):
            if block.get("type") == "child_database":
                title = block.get("child_database", {}).get("title", "")
                results[title] = block["id"]
        return results

    async def close(self):
        await self._http.aclose()


async def create_workspace(notion_token: str, root_page_id: str) -> dict[str, str]:
    """Create all OpsLens databases under the root page (idempotent)."""
    api = NotionAPIClient(notion_token)
    db_ids: dict[str, str] = {}

    # Check for existing databases to avoid duplicates
    existing = await api.get_child_databases(root_page_id)
    logger.info("existing_databases", databases=list(existing.keys()))

    try:
        # --- Services Database ---
        if "OpsLens Services" in existing:
            db_ids["services"] = existing["OpsLens Services"]
            logger.info("found_existing_database", name="Services", id=db_ids["services"])
        else:
            services_db = await api.create_database(
                root_page_id,
                "OpsLens Services",
                {
                    "Name": {"title": {}},
                    "Team": {"rich_text": {}},
                    "Criticality": {
                        "select": {
                            "options": [
                                {"name": "Tier-0", "color": "red"},
                                {"name": "Tier-1", "color": "orange"},
                                {"name": "Tier-2", "color": "yellow"},
                                {"name": "Tier-3", "color": "gray"},
                            ]
                        }
                    },
                    "Repository URL": {"url": {}},
                    "Dashboard URL": {"url": {}},
                    "Alert Rules": {"rich_text": {}},
                    "SLA Target": {"number": {"format": "percent"}},
                    "Last Incident": {"date": {}},
                },
            )
            db_ids["services"] = services_db["id"]
            logger.info("created_database", name="Services", id=db_ids["services"])

        # --- On-Call Database ---
        if "OpsLens On-Call" in existing:
            db_ids["oncall"] = existing["OpsLens On-Call"]
            logger.info("found_existing_database", name="On-Call", id=db_ids["oncall"])
        else:
            oncall_db = await api.create_database(
                root_page_id,
                "OpsLens On-Call",
                {
                    "Name": {"title": {}},
                    "Email": {"email": {}},
                    "Slack Handle": {"rich_text": {}},
                    "Team": {"rich_text": {}},
                    "Rotation Start": {"date": {}},
                    "Rotation End": {"date": {}},
                    "Is Primary": {"checkbox": {}},
                    "Phone": {"phone_number": {}},
                },
            )
            db_ids["oncall"] = oncall_db["id"]
            logger.info("created_database", name="On-Call", id=oncall_db["id"])

        # --- Postmortems Database ---
        if "OpsLens Postmortems" in existing:
            db_ids["postmortems"] = existing["OpsLens Postmortems"]
            logger.info("found_existing_database", name="Postmortems", id=db_ids["postmortems"])
        else:
            postmortems_db = await api.create_database(
                root_page_id,
                "OpsLens Postmortems",
                {
                    "Name": {"title": {}},
                    "Status": {
                        "select": {
                            "options": [
                                {"name": "Draft", "color": "gray"},
                                {"name": "In Review", "color": "yellow"},
                                {"name": "Published", "color": "green"},
                            ]
                        }
                    },
                    "Created At": {"date": {}},
                    "Blameless": {"checkbox": {}},
                    "Action Items Count": {"number": {}},
                    "Follow-Up Due": {"date": {}},
                },
            )
            db_ids["postmortems"] = postmortems_db["id"]
            logger.info("created_database", name="Postmortems", id=postmortems_db["id"])

        # --- Incidents Database ---
        if "OpsLens Incidents" in existing:
            db_ids["incidents"] = existing["OpsLens Incidents"]
            logger.info("found_existing_database", name="Incidents", id=db_ids["incidents"])
        else:
            incidents_db = await api.create_database(
                root_page_id,
                "OpsLens Incidents",
                {
                    "Name": {"title": {}},
                    "Incident ID": {"rich_text": {}},
                    "Status": {
                        "select": {
                            "options": [
                                {"name": "Triggered", "color": "red"},
                                {"name": "Triaged", "color": "orange"},
                                {"name": "Investigating", "color": "yellow"},
                                {"name": "Mitigated", "color": "blue"},
                                {"name": "Resolved", "color": "green"},
                                {"name": "Postmortem", "color": "purple"},
                            ]
                        }
                    },
                    "Severity": {
                        "select": {
                            "options": [
                                {"name": "P0-Critical", "color": "red"},
                                {"name": "P1-High", "color": "orange"},
                                {"name": "P2-Medium", "color": "yellow"},
                                {"name": "P3-Low", "color": "blue"},
                            ]
                        }
                    },
                    "Alert Source": {
                        "select": {
                            "options": [
                                {"name": "Prometheus", "color": "red"},
                                {"name": "Grafana", "color": "orange"},
                                {"name": "PagerDuty", "color": "green"},
                                {"name": "Manual", "color": "gray"},
                                {"name": "Generic", "color": "blue"},
                            ]
                        }
                    },
                    "Service": {
                        "relation": {
                            "database_id": db_ids["services"],
                            "single_property": {},
                        }
                    },
                    "Triggered At": {"date": {}},
                    "Resolved At": {"date": {}},
                    "Impact": {"rich_text": {}},
                    "Root Cause": {"rich_text": {}},
                    "Tags": {
                        "multi_select": {
                            "options": [
                                {"name": "infrastructure", "color": "gray"},
                                {"name": "application", "color": "blue"},
                                {"name": "database", "color": "green"},
                                {"name": "network", "color": "orange"},
                                {"name": "security", "color": "red"},
                                {"name": "deployment", "color": "purple"},
                            ]
                        }
                    },
                    "Postmortem": {
                        "relation": {
                            "database_id": db_ids["postmortems"],
                            "single_property": {},
                        }
                    },
                    "Agent Actions Count": {"number": {}},
                },
            )
            db_ids["incidents"] = incidents_db["id"]
            logger.info("created_database", name="Incidents", id=incidents_db["id"])

        # --- Runbooks Database ---
        if "OpsLens Runbooks" in existing:
            db_ids["runbooks"] = existing["OpsLens Runbooks"]
            logger.info("found_existing_database", name="Runbooks", id=db_ids["runbooks"])
        else:
            runbooks_db = await api.create_database(
                root_page_id,
                "OpsLens Runbooks",
                {
                    "Name": {"title": {}},
                    "Service": {
                        "relation": {
                            "database_id": db_ids["services"],
                            "single_property": {},
                        }
                    },
                    "Category": {
                        "select": {
                            "options": [
                                {"name": "Infrastructure", "color": "gray"},
                                {"name": "Application", "color": "blue"},
                                {"name": "Database", "color": "green"},
                                {"name": "Network", "color": "orange"},
                                {"name": "Security", "color": "red"},
                                {"name": "Deployment", "color": "purple"},
                            ]
                        }
                    },
                    "Trigger Conditions": {"rich_text": {}},
                    "Severity Applicability": {
                        "multi_select": {
                            "options": [
                                {"name": "P0", "color": "red"},
                                {"name": "P1", "color": "orange"},
                                {"name": "P2", "color": "yellow"},
                                {"name": "P3", "color": "blue"},
                            ]
                        }
                    },
                    "Last Used": {"date": {}},
                    "Effectiveness Rating": {
                        "select": {
                            "options": [
                                {"name": "Highly Effective", "color": "green"},
                                {"name": "Effective", "color": "blue"},
                                {"name": "Needs Update", "color": "yellow"},
                                {"name": "Deprecated", "color": "red"},
                            ]
                        }
                    },
                    "Auto-Applicable": {"checkbox": {}},
                },
            )
            db_ids["runbooks"] = runbooks_db["id"]
            logger.info("created_database", name="Runbooks", id=runbooks_db["id"])

    finally:
        await api.close()

    return db_ids


async def cleanup_duplicate_databases(notion_token: str, root_page_id: str) -> list[str]:
    """Remove duplicate databases under the root page, keeping the first of each name."""
    api = NotionAPIClient(notion_token)
    removed = []
    try:
        resp = await api._http.get(
            f"{NOTION_API}/blocks/{root_page_id}/children",
            params={"page_size": 100},
        )
        resp.raise_for_status()
        seen: dict[str, str] = {}
        for block in resp.json().get("results", []):
            if block.get("type") == "child_database":
                title = block.get("child_database", {}).get("title", "")
                block_id = block["id"]
                if title in seen:
                    # This is a duplicate - archive it
                    await api._http.delete(f"{NOTION_API}/blocks/{block_id}")
                    removed.append(f"{title} ({block_id})")
                    logger.info("removed_duplicate_database", title=title, id=block_id)
                else:
                    seen[title] = block_id
    finally:
        await api.close()
    return removed


async def seed_services(notion_token: str, services_db_id: str) -> dict[str, str]:
    """Create sample service entries."""
    api = NotionAPIClient(notion_token)
    services = [
        ("API Gateway", "Platform", "Tier-0", 99.99),
        ("Auth Service", "Identity", "Tier-0", 99.99),
        ("Payment Service", "Payments", "Tier-1", 99.95),
        ("Database Cluster", "Infrastructure", "Tier-0", 99.999),
        ("CDN", "Infrastructure", "Tier-1", 99.9),
        ("Notification Service", "Platform", "Tier-2", 99.9),
        ("Search Service", "Platform", "Tier-2", 99.5),
        ("User Service", "Identity", "Tier-1", 99.95),
    ]
    page_ids: dict[str, str] = {}
    try:
        for name, team, criticality, sla in services:
            page = await api.create_page(
                services_db_id,
                {
                    "Name": {"title": [{"text": {"content": name}}]},
                    "Team": {"rich_text": [{"text": {"content": team}}]},
                    "Criticality": {"select": {"name": criticality}},
                    "SLA Target": {"number": sla},
                },
            )
            page_ids[name] = page["id"]
            logger.info("created_service", name=name, id=page["id"])
    finally:
        await api.close()
    return page_ids


async def main() -> None:
    """Interactive CLI for workspace setup."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    notion_token = os.getenv("NOTION_TOKEN", "")
    root_page_id = os.getenv("NOTION_ROOT_PAGE_ID", "")

    if not notion_token:
        print("ERROR: NOTION_TOKEN not set in .env")
        sys.exit(1)
    if not root_page_id:
        print("ERROR: NOTION_ROOT_PAGE_ID not set in .env")
        sys.exit(1)

    print("=== OpsLens Workspace Setup ===")
    print(f"Root Page ID: {root_page_id}")
    print()

    print("Creating databases...")
    db_ids = await create_workspace(notion_token, root_page_id)

    print()
    print("Creating sample services...")
    await seed_services(notion_token, db_ids["services"])

    print()
    print("=== Add these to your .env file ===")
    print(f'NOTION_INCIDENTS_DB_ID={db_ids["incidents"]}')
    print(f'NOTION_RUNBOOKS_DB_ID={db_ids["runbooks"]}')
    print(f'NOTION_SERVICES_DB_ID={db_ids["services"]}')
    print(f'NOTION_POSTMORTEMS_DB_ID={db_ids["postmortems"]}')
    print(f'NOTION_ONCALL_DB_ID={db_ids["oncall"]}')
    print()
    print("Setup complete!")


if __name__ == "__main__":
    asyncio.run(main())
