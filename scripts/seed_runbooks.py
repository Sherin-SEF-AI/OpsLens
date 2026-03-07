#!/usr/bin/env python3
"""Seed the Notion Runbooks database with realistic operational runbooks."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from notion_client import AsyncClient

from src.notion_mcp.templates import runbook_content

load_dotenv()

RUNBOOKS = [
    {
        "title": "High CPU Usage on Application Server",
        "category": "Infrastructure",
        "trigger": "CPU utilization > 90% for 5+ minutes on any application server",
        "severity": ["P1", "P2"],
        "steps": [
            "SSH to affected host and run `top -c` to identify the process consuming CPU",
            "Check if it's a known batch job or scheduled task via `crontab -l`",
            "Run `ps aux --sort=-%cpu | head -20` to get top CPU consumers with full command",
            "Check application logs: `journalctl -u <service> --since '10 minutes ago'`",
            "If application process: check for infinite loops, thread deadlocks, or runaway GC",
            "If system process: check `dmesg` for kernel issues, run `iostat -x 1 5` for I/O wait",
            "Scale horizontally if possible: add instances behind load balancer",
            "If single-threaded spike: restart the affected service with `systemctl restart <service>`",
            "Monitor for 10 minutes post-restart to confirm resolution",
            "If persists after restart: capture thread dump (`kill -3 <pid>`) and escalate to dev team",
        ],
        "rollback": [
            "If recently deployed: initiate rollback to previous version",
            "Run `kubectl rollout undo deployment/<name>` for Kubernetes deployments",
            "Verify rollback successful with health checks",
        ],
        "notes": "Common causes: memory leak triggering excessive GC, regex backtracking, unoptimized database queries returning large result sets.",
    },
    {
        "title": "Database Connection Pool Exhaustion",
        "category": "Database",
        "trigger": "Connection pool utilization > 95% or connection timeout errors in application logs",
        "severity": ["P0", "P1"],
        "steps": [
            "Check current connection count: `SELECT count(*) FROM pg_stat_activity;`",
            "Identify long-running queries: `SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC LIMIT 20;`",
            "Check for idle-in-transaction connections: `SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction';`",
            "Kill long-running idle transactions (>5 min): `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' AND query_start < now() - interval '5 minutes';`",
            "Check application connection pool settings (HikariCP/pgbouncer configs)",
            "Verify no connection leaks: check application logs for unclosed connections",
            "If PgBouncer is used: `SHOW POOLS;` and `SHOW CLIENTS;` to check pool state",
            "Temporarily increase max_connections if safe: `ALTER SYSTEM SET max_connections = 200;` (requires restart)",
            "Restart application services to release stale connections",
            "Monitor connection count recovery over 5 minutes",
        ],
        "rollback": None,
        "notes": "Root cause is often connection leaks in application code (missing try/finally blocks), or a slow downstream dependency causing connections to pile up.",
    },
    {
        "title": "API Gateway 5xx Error Rate Spike",
        "category": "Application",
        "trigger": "5xx error rate > 5% of total requests on API Gateway for 3+ minutes",
        "severity": ["P0", "P1"],
        "steps": [
            "Check API Gateway dashboard for affected endpoints and error distribution",
            "Identify specific HTTP status codes: 500 (server error), 502 (bad gateway), 503 (unavailable), 504 (timeout)",
            "For 502/504: check upstream service health — likely a backend is down or slow",
            "Run `curl -v https://<api-gateway>/health` to verify gateway itself is healthy",
            "Check upstream service health endpoints: `for svc in auth payments users; do curl -s https://$svc/health; done`",
            "Review API Gateway access logs for error patterns: filter by path, client, timing",
            "If specific endpoint: check the backing service's logs and metrics",
            "If all endpoints: check shared infrastructure (database, cache, message queue)",
            "If 503: check if rate limiting or circuit breaker has tripped",
            "For immediate mitigation: enable fallback/cached responses if available",
            "If backend overloaded: scale up backend instances",
            "Monitor error rate every minute for 10 minutes after mitigation",
        ],
        "rollback": [
            "If correlated with deployment: rollback the last deployed service",
            "If config change: revert API Gateway configuration",
            "Restore from last known good config backup",
        ],
        "notes": "Check recent deployments first — most 5xx spikes correlate with bad deploys. Also check for database migration issues.",
    },
    {
        "title": "Certificate Expiration Alert",
        "category": "Security",
        "trigger": "TLS certificate expires within 7 days or has already expired",
        "severity": ["P1", "P2"],
        "steps": [
            "Identify the affected domain: check alert labels for the hostname",
            "Verify expiration: `echo | openssl s_client -servername <host> -connect <host>:443 2>/dev/null | openssl x509 -noout -dates`",
            "Check if auto-renewal (cert-manager/Let's Encrypt) is configured",
            "If cert-manager: `kubectl describe certificate <name>` and `kubectl describe certificaterequest`",
            "Check cert-manager logs: `kubectl logs -n cert-manager deployment/cert-manager`",
            "Common cert-manager issues: DNS challenge failing, rate limits, webhook issues",
            "For manual certificates: locate the cert files and check renewal process documentation",
            "Renew manually if auto-renewal is broken: `certbot renew --cert-name <domain>`",
            "Verify renewal: `openssl x509 -in /path/to/cert.pem -noout -dates`",
            "Reload the web server/ingress: `nginx -s reload` or restart ingress controller",
            "Verify from client side: `curl -vI https://<host>` and check certificate dates",
        ],
        "rollback": None,
        "notes": "Set up monitoring for cert expiration at 30, 14, 7, and 3 days. Automate renewal with cert-manager or ACME clients.",
    },
    {
        "title": "Memory Leak Detection and Mitigation",
        "category": "Application",
        "trigger": "Memory usage increasing linearly over time without plateau, OOM kills in dmesg",
        "severity": ["P1", "P2"],
        "steps": [
            "Confirm the trend: check memory usage graph over 24h — is it linearly increasing?",
            "Check for OOM kills: `dmesg | grep -i 'out of memory'` or `journalctl -k | grep -i oom`",
            "Identify the process: `ps aux --sort=-%mem | head -10`",
            "For JVM apps: take heap dump `jmap -dump:format=b,file=/tmp/heap.hprof <pid>`",
            "For Python apps: check for circular references, unclosed file handles, growing caches",
            "For Node.js: enable `--inspect` and take heap snapshot via Chrome DevTools",
            "Check application-level caches: are they unbounded? Do they have TTL?",
            "Review recent code changes for: new caches, event listeners not being removed, file handle leaks",
            "Immediate mitigation: restart the service to reclaim memory",
            "Set up memory limits: configure container memory limits or JVM heap max",
            "Schedule periodic restarts as temporary measure until root cause is fixed",
            "File a bug ticket with heap dump attached for development team",
        ],
        "rollback": [
            "If correlated with a recent deployment: rollback to previous version",
            "Memory leak may not manifest immediately — check deploy timing",
        ],
        "notes": "Memory leaks are often caused by unbounded caches, event listener accumulation, or circular references preventing garbage collection.",
    },
    {
        "title": "Deployment Rollback Procedure",
        "category": "Deployment",
        "trigger": "Post-deployment health checks failing, error rate spike after deploy, manual rollback requested",
        "severity": ["P0", "P1", "P2"],
        "steps": [
            "Confirm the deploy is the cause: correlate error timing with deploy timestamp",
            "Check deployment status: `kubectl rollout status deployment/<name>`",
            "Get current and previous revision: `kubectl rollout history deployment/<name>`",
            "Execute rollback: `kubectl rollout undo deployment/<name>`",
            "For specific revision: `kubectl rollout undo deployment/<name> --to-revision=<N>`",
            "Monitor rollback progress: `kubectl rollout status deployment/<name>`",
            "Verify pods are running previous version: `kubectl get pods -o jsonpath='{.items[*].spec.containers[*].image}'`",
            "Run smoke tests against the service endpoints",
            "Check error rates are returning to baseline",
            "If database migration was part of deploy: check if migration is backward-compatible",
            "If migration is NOT backward-compatible: THIS IS CRITICAL — do not rollback without DBA review",
            "Notify the team in #incidents Slack channel about the rollback",
            "Create a ticket to investigate the failed deployment root cause",
        ],
        "rollback": None,
        "notes": "Always ensure database migrations are backward-compatible (expand/contract pattern). Feature flags should be used for risky changes.",
    },
    {
        "title": "DDoS Mitigation Playbook",
        "category": "Security",
        "trigger": "Abnormal traffic spike (>10x normal), request rate from single IPs exceeding limits, CDN/WAF alerts",
        "severity": ["P0", "P1"],
        "steps": [
            "Confirm attack: check traffic graphs for sudden spike pattern",
            "Identify attack type: volumetric (bandwidth), protocol (SYN flood), or application layer (HTTP flood)",
            "Check CDN/WAF dashboard for blocked requests and top attacking IPs",
            "Enable rate limiting if not already active: configure per-IP request limits",
            "For application layer: enable CAPTCHA challenges for suspicious traffic",
            "Block obvious attack IPs at WAF/CDN level: add to block list",
            "If using Cloudflare: enable 'Under Attack Mode' for immediate protection",
            "If using AWS: check AWS Shield metrics, consider AWS Shield Advanced",
            "For volumetric: contact ISP/hosting provider to null-route attack traffic upstream",
            "Scale up infrastructure if legitimate traffic is also being affected",
            "Enable geographic blocking if attack is from specific regions",
            "Monitor bandwidth, request rate, and error rate continuously during mitigation",
            "After attack subsides: review logs to extract attack signatures for future prevention",
        ],
        "rollback": [
            "After attack ends: disable 'Under Attack Mode' to restore normal user experience",
            "Remove temporary IP blocks that may be overly broad",
            "Restore normal rate limiting thresholds",
        ],
        "notes": "Keep ISP/hosting provider emergency contact numbers readily available. Pre-configure CDN and WAF rules during peacetime.",
    },
    {
        "title": "Database Replication Lag",
        "category": "Database",
        "trigger": "Replication lag > 30 seconds on read replicas, or replica falling behind WAL",
        "severity": ["P1", "P2"],
        "steps": [
            "Check current lag: `SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;` (on replica)",
            "Check replication status from primary: `SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn FROM pg_stat_replication;`",
            "Calculate byte lag: `SELECT pg_wal_lsn_diff(sent_lsn, replay_lsn) AS byte_lag FROM pg_stat_replication;`",
            "Check replica's I/O: `iostat -x 1 5` — if disk is saturated, replica can't keep up",
            "Check for long-running queries on replica blocking replay: `SELECT * FROM pg_stat_activity WHERE state = 'active' AND backend_type = 'client backend';`",
            "If long queries: consider setting `hot_standby_feedback = on` or `max_standby_streaming_delay`",
            "Check if primary has heavy write load: `SELECT * FROM pg_stat_bgwriter;`",
            "If replica disk I/O bottleneck: upgrade storage IOPS or move to faster storage",
            "For immediate relief: redirect read traffic away from lagging replica",
            "If replica is too far behind: consider rebuilding from fresh base backup",
            "Monitor lag reduction rate — should decrease steadily once cause is addressed",
        ],
        "rollback": None,
        "notes": "Replication lag often spikes during: bulk data loads, schema migrations (especially adding indexes), vacuum operations, or network issues between primary and replica.",
    },
]


async def seed_runbooks() -> None:
    """Create runbook pages in Notion."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    runbooks_db_id = os.getenv("NOTION_RUNBOOKS_DB_ID", "")

    if not notion_token:
        print("ERROR: NOTION_TOKEN not set")
        sys.exit(1)
    if not runbooks_db_id:
        print("ERROR: NOTION_RUNBOOKS_DB_ID not set — run setup_workspace.py first")
        sys.exit(1)

    notion = AsyncClient(auth=notion_token)

    print(f"Seeding {len(RUNBOOKS)} runbooks...")

    for rb in RUNBOOKS:
        content = runbook_content(
            title=rb["title"],
            service="Multiple",
            category=rb["category"],
            trigger_conditions=rb["trigger"],
            steps=rb["steps"],
            rollback_steps=rb.get("rollback"),
            notes=rb.get("notes", ""),
        )

        properties = {
            "Name": {"title": [{"text": {"content": rb["title"]}}]},
            "Category": {"select": {"name": rb["category"]}},
            "Trigger Conditions": {
                "rich_text": [{"text": {"content": rb["trigger"][:2000]}}]
            },
            "Severity Applicability": {
                "multi_select": [{"name": s} for s in rb["severity"]]
            },
            "Effectiveness Rating": {"select": {"name": "Effective"}},
            "Auto-Applicable": {"checkbox": False},
        }

        try:
            page = await notion.pages.create(
                parent={"database_id": runbooks_db_id},
                properties=properties,
                children=_markdown_to_blocks(content),
            )
            print(f"  Created: {rb['title']} ({page['id']})")
        except Exception as e:
            print(f"  ERROR creating {rb['title']}: {e}")


def _markdown_to_blocks(content: str) -> list[dict]:
    """Convert simple markdown to Notion blocks."""
    blocks = []
    for line in content.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                },
            })
        elif line.startswith("**") and line.endswith("**"):
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": line.strip("*")},
                            "annotations": {"bold": True},
                        }
                    ]
                },
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                },
            })
        elif len(line) > 2 and line[0].isdigit() and line[1] == ".":
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [
                        {"type": "text", "text": {"content": line.split(". ", 1)[-1]}}
                    ]
                },
            })
        elif line.startswith("---"):
            blocks.append({
                "object": "block",
                "type": "divider",
                "divider": {},
            })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": line}}]
                },
            })
    return blocks


if __name__ == "__main__":
    asyncio.run(seed_runbooks())
    print("\nDone! Runbooks seeded successfully.")
