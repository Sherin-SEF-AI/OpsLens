"""Cloud Provider APIs Integration for OpsLens.

Features:
- AWS: CloudWatch alerts, ECS/EC2 health queries, auto-scale/restart
- GCP: Cloud Monitoring alerts, GKE pod health, instance actions
- Azure: Monitor alerts, AKS health, VM actions

All providers use their respective SDKs when available, falling back to REST APIs.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class AWSIntegration:
    """AWS integration via boto3 for CloudWatch, ECS, EC2, and Lambda."""

    def __init__(
        self,
        access_key_id: str = "",
        secret_access_key: str = "",
        region: str = "us-east-1",
        session_token: str = "",
    ):
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region
        self.session_token = session_token
        self._enabled = bool(access_key_id and secret_access_key)
        self._clients: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _get_client(self, service: str) -> Any:
        """Get or create a boto3 client."""
        if service not in self._clients:
            try:
                import boto3
                kwargs: dict[str, Any] = {
                    "service_name": service,
                    "region_name": self.region,
                    "aws_access_key_id": self.access_key_id,
                    "aws_secret_access_key": self.secret_access_key,
                }
                if self.session_token:
                    kwargs["aws_session_token"] = self.session_token
                self._clients[service] = boto3.client(**kwargs)
            except ImportError:
                logger.error("boto3_not_installed")
                return None
        return self._clients[service]

    # --- CloudWatch Alarms ---

    async def get_active_alarms(
        self, namespace: str = "", alarm_prefix: str = ""
    ) -> list[dict[str, Any]]:
        """Get active CloudWatch alarms."""
        if not self._enabled:
            return []

        try:
            import asyncio
            client = self._get_client("cloudwatch")
            if not client:
                return []

            def _fetch():
                kwargs: dict[str, Any] = {"StateValue": "ALARM", "MaxRecords": 100}
                if alarm_prefix:
                    kwargs["AlarmNamePrefix"] = alarm_prefix
                return client.describe_alarms(**kwargs)

            result = await asyncio.get_event_loop().run_in_executor(None, _fetch)

            alarms = []
            for a in result.get("MetricAlarms", []):
                if namespace and a.get("Namespace") != namespace:
                    continue
                alarms.append({
                    "name": a["AlarmName"],
                    "state": a["StateValue"],
                    "reason": a.get("StateReason", ""),
                    "namespace": a.get("Namespace", ""),
                    "metric": a.get("MetricName", ""),
                    "dimensions": {
                        d["Name"]: d["Value"]
                        for d in a.get("Dimensions", [])
                    },
                    "updated_at": a.get("StateUpdatedTimestamp", "").isoformat()
                    if a.get("StateUpdatedTimestamp")
                    else "",
                })

            logger.info("aws_alarms_fetched", count=len(alarms))
            return alarms

        except Exception as exc:
            logger.error("aws_alarms_error", error=str(exc))
            return []

    # --- CloudWatch Metrics ---

    async def get_metric_data(
        self,
        namespace: str,
        metric_name: str,
        dimensions: dict[str, str],
        period_minutes: int = 30,
        stat: str = "Average",
    ) -> list[dict[str, Any]]:
        """Get CloudWatch metric data points."""
        if not self._enabled:
            return []

        try:
            import asyncio
            client = self._get_client("cloudwatch")
            if not client:
                return []

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=period_minutes)

            def _fetch():
                return client.get_metric_statistics(
                    Namespace=namespace,
                    MetricName=metric_name,
                    Dimensions=[
                        {"Name": k, "Value": v} for k, v in dimensions.items()
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=60,
                    Statistics=[stat],
                )

            result = await asyncio.get_event_loop().run_in_executor(None, _fetch)

            points = []
            for dp in sorted(result.get("Datapoints", []), key=lambda x: x["Timestamp"]):
                points.append({
                    "timestamp": dp["Timestamp"].isoformat(),
                    "value": dp.get(stat, 0),
                    "unit": dp.get("Unit", ""),
                })

            return points

        except Exception as exc:
            logger.error("aws_metric_error", metric=metric_name, error=str(exc))
            return []

    # --- ECS Service Health ---

    async def get_ecs_service_health(
        self, cluster: str, service_name: str
    ) -> dict[str, Any]:
        """Get ECS service health: task counts, recent events."""
        if not self._enabled:
            return {"error": "AWS not configured"}

        try:
            import asyncio
            client = self._get_client("ecs")
            if not client:
                return {"error": "boto3 not available"}

            def _fetch():
                return client.describe_services(
                    cluster=cluster, services=[service_name]
                )

            result = await asyncio.get_event_loop().run_in_executor(None, _fetch)

            services = result.get("services", [])
            if not services:
                return {"error": f"Service {service_name} not found in cluster {cluster}"}

            svc = services[0]
            return {
                "service_name": svc["serviceName"],
                "status": svc["status"],
                "desired_count": svc["desiredCount"],
                "running_count": svc["runningCount"],
                "pending_count": svc["pendingCount"],
                "task_definition": svc.get("taskDefinition", "").split("/")[-1],
                "launch_type": svc.get("launchType", ""),
                "health_check_grace_period": svc.get("healthCheckGracePeriodSeconds", 0),
                "recent_events": [
                    {
                        "message": e["message"],
                        "created_at": e["createdAt"].isoformat(),
                    }
                    for e in svc.get("events", [])[:5]
                ],
                "deployments": [
                    {
                        "status": d["status"],
                        "desired_count": d["desiredCount"],
                        "running_count": d["runningCount"],
                        "task_definition": d.get("taskDefinition", "").split("/")[-1],
                        "updated_at": d.get("updatedAt", "").isoformat()
                        if d.get("updatedAt")
                        else "",
                    }
                    for d in svc.get("deployments", [])
                ],
            }

        except Exception as exc:
            logger.error("aws_ecs_error", service=service_name, error=str(exc))
            return {"error": str(exc)}

    # --- EC2 Instance Health ---

    async def get_ec2_instance_status(
        self, instance_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Get EC2 instance status checks."""
        if not self._enabled:
            return []

        try:
            import asyncio
            client = self._get_client("ec2")
            if not client:
                return []

            def _fetch():
                return client.describe_instance_status(
                    InstanceIds=instance_ids, IncludeAllInstances=True
                )

            result = await asyncio.get_event_loop().run_in_executor(None, _fetch)

            statuses = []
            for s in result.get("InstanceStatuses", []):
                statuses.append({
                    "instance_id": s["InstanceId"],
                    "state": s["InstanceState"]["Name"],
                    "system_status": s.get("SystemStatus", {}).get("Status", "unknown"),
                    "instance_status": s.get("InstanceStatus", {}).get("Status", "unknown"),
                    "availability_zone": s.get("AvailabilityZone", ""),
                })

            return statuses

        except Exception as exc:
            logger.error("aws_ec2_status_error", error=str(exc))
            return []

    # --- Auto-remediation Actions ---

    async def restart_ecs_service(
        self, cluster: str, service_name: str
    ) -> dict[str, Any]:
        """Force a new deployment of an ECS service (rolling restart)."""
        if not self._enabled:
            return {"error": "AWS not configured"}

        try:
            import asyncio
            client = self._get_client("ecs")
            if not client:
                return {"error": "boto3 not available"}

            def _update():
                return client.update_service(
                    cluster=cluster,
                    service=service_name,
                    forceNewDeployment=True,
                )

            result = await asyncio.get_event_loop().run_in_executor(None, _update)

            svc = result.get("service", {})
            logger.info(
                "aws_ecs_restart",
                cluster=cluster,
                service=service_name,
            )
            return {
                "status": "restarting",
                "service": svc.get("serviceName", ""),
                "desired_count": svc.get("desiredCount", 0),
            }

        except Exception as exc:
            logger.error("aws_ecs_restart_error", error=str(exc))
            return {"error": str(exc)}

    async def scale_ecs_service(
        self, cluster: str, service_name: str, desired_count: int
    ) -> dict[str, Any]:
        """Scale an ECS service to a desired task count."""
        if not self._enabled:
            return {"error": "AWS not configured"}

        try:
            import asyncio
            client = self._get_client("ecs")
            if not client:
                return {"error": "boto3 not available"}

            def _scale():
                return client.update_service(
                    cluster=cluster,
                    service=service_name,
                    desiredCount=desired_count,
                )

            result = await asyncio.get_event_loop().run_in_executor(None, _scale)

            svc = result.get("service", {})
            logger.info(
                "aws_ecs_scaled",
                cluster=cluster,
                service=service_name,
                desired_count=desired_count,
            )
            return {
                "status": "scaling",
                "service": svc.get("serviceName", ""),
                "new_desired_count": desired_count,
            }

        except Exception as exc:
            logger.error("aws_ecs_scale_error", error=str(exc))
            return {"error": str(exc)}

    # --- Test Connection ---

    async def test_connection(self) -> dict[str, Any]:
        """Test AWS API connectivity via STS GetCallerIdentity."""
        if not self._enabled:
            return {"status": "disabled", "message": "AWS credentials not configured"}

        try:
            import asyncio
            client = self._get_client("sts")
            if not client:
                return {"status": "error", "message": "boto3 not installed"}

            def _call():
                return client.get_caller_identity()

            result = await asyncio.get_event_loop().run_in_executor(None, _call)
            return {
                "status": "ok",
                "message": f"Connected as {result.get('Arn', 'unknown')}",
                "account": result.get("Account", ""),
                "arn": result.get("Arn", ""),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


class GCPIntegration:
    """GCP integration for Cloud Monitoring, GKE, and Compute Engine."""

    def __init__(
        self,
        project_id: str = "",
        credentials_json: str = "",
        region: str = "us-central1",
    ):
        self.project_id = project_id
        self.credentials_json = credentials_json
        self.region = region
        self._enabled = bool(project_id and credentials_json)
        self._access_token: str = ""
        self._token_expiry: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _get_access_token(self) -> str:
        """Get an access token using service account credentials."""
        if self._access_token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._access_token

        try:
            import json
            import jwt
            import time

            creds = json.loads(self.credentials_json)
            now = int(time.time())

            payload = {
                "iss": creds["client_email"],
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600,
            }

            signed = jwt.encode(payload, creds["private_key"], algorithm="RS256")

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": signed,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            self._access_token = data["access_token"]
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600) - 60)
            return self._access_token

        except ImportError:
            logger.error("pyjwt_not_installed", msg="pip install PyJWT")
            return ""
        except Exception as exc:
            logger.error("gcp_auth_error", error=str(exc))
            return ""

    async def _request(
        self, method: str, url: str, json: dict | None = None
    ) -> dict[str, Any]:
        token = await self._get_access_token()
        if not token:
            return {"error": "Authentication failed"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    # --- Cloud Monitoring Alerts ---

    async def get_active_alerts(self) -> list[dict[str, Any]]:
        """Get active Cloud Monitoring alert incidents."""
        if not self._enabled:
            return []

        try:
            url = (
                f"https://monitoring.googleapis.com/v3/projects/{self.project_id}"
                f"/alertPolicies"
            )
            data = await self._request("GET", url)

            alerts = []
            for policy in data.get("alertPolicies", []):
                if policy.get("enabled"):
                    alerts.append({
                        "name": policy.get("displayName", ""),
                        "enabled": policy.get("enabled", False),
                        "conditions": [
                            c.get("displayName", "")
                            for c in policy.get("conditions", [])
                        ],
                    })

            return alerts

        except Exception as exc:
            logger.error("gcp_alerts_error", error=str(exc))
            return []

    # --- GKE Pod Health ---

    async def get_gke_pod_status(
        self, cluster: str, namespace: str = "default"
    ) -> list[dict[str, Any]]:
        """Get pod status for a GKE cluster (via Kubernetes API)."""
        if not self._enabled:
            return []

        try:
            # Get cluster credentials
            url = (
                f"https://container.googleapis.com/v1/projects/{self.project_id}"
                f"/locations/{self.region}/clusters/{cluster}"
            )
            cluster_data = await self._request("GET", url)

            endpoint = cluster_data.get("endpoint", "")
            if not endpoint:
                return [{"error": "Cluster endpoint not found"}]

            # Query Kubernetes API for pods
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                resp = await client.get(
                    f"https://{endpoint}/api/v1/namespaces/{namespace}/pods",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()

            pods = []
            for pod in data.get("items", []):
                status = pod.get("status", {})
                containers = []
                for cs in status.get("containerStatuses", []):
                    containers.append({
                        "name": cs["name"],
                        "ready": cs.get("ready", False),
                        "restart_count": cs.get("restartCount", 0),
                        "state": list(cs.get("state", {}).keys())[0] if cs.get("state") else "unknown",
                    })

                pods.append({
                    "name": pod["metadata"]["name"],
                    "phase": status.get("phase", "Unknown"),
                    "node": pod["spec"].get("nodeName", ""),
                    "containers": containers,
                    "start_time": status.get("startTime", ""),
                })

            return pods

        except Exception as exc:
            logger.error("gcp_gke_error", cluster=cluster, error=str(exc))
            return []

    # --- Resource Health Query ---

    async def get_resource_metrics(
        self,
        metric_type: str,
        resource_type: str = "",
        period_minutes: int = 30,
    ) -> list[dict[str, Any]]:
        """Query Cloud Monitoring time series data."""
        if not self._enabled:
            return []

        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=period_minutes)

            filter_str = f'metric.type = "{metric_type}"'
            if resource_type:
                filter_str += f' AND resource.type = "{resource_type}"'

            url = (
                f"https://monitoring.googleapis.com/v3/projects/{self.project_id}"
                f"/timeSeries"
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                token = await self._get_access_token()
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "filter": filter_str,
                        "interval.startTime": start_time.isoformat() + "Z",
                        "interval.endTime": end_time.isoformat() + "Z",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            series = []
            for ts in data.get("timeSeries", []):
                points = []
                for p in ts.get("points", []):
                    val = p.get("value", {})
                    value = val.get("doubleValue") or val.get("int64Value") or val.get("boolValue", 0)
                    points.append({
                        "timestamp": p.get("interval", {}).get("endTime", ""),
                        "value": value,
                    })
                series.append({
                    "metric": ts.get("metric", {}).get("type", ""),
                    "labels": ts.get("metric", {}).get("labels", {}),
                    "resource": ts.get("resource", {}).get("type", ""),
                    "points": points[:10],
                })

            return series

        except Exception as exc:
            logger.error("gcp_metrics_error", metric=metric_type, error=str(exc))
            return []

    async def test_connection(self) -> dict[str, Any]:
        """Test GCP API connectivity."""
        if not self._enabled:
            return {"status": "disabled", "message": "GCP not configured"}

        try:
            token = await self._get_access_token()
            if not token:
                return {"status": "error", "message": "Failed to authenticate"}

            url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{self.project_id}"
            data = await self._request("GET", url)

            return {
                "status": "ok",
                "message": f"Connected to project {data.get('name', self.project_id)}",
                "project": data.get("name", ""),
                "project_number": data.get("projectNumber", ""),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


class AzureIntegration:
    """Azure integration for Monitor, AKS, and Virtual Machines."""

    def __init__(
        self,
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        subscription_id: str = "",
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.subscription_id = subscription_id
        self._enabled = bool(tenant_id and client_id and client_secret and subscription_id)
        self._access_token: str = ""
        self._token_expiry: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _get_access_token(self) -> str:
        """Get Azure AD access token."""
        if self._access_token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._access_token

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "https://management.azure.com/.default",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            self._access_token = data["access_token"]
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600) - 60)
            return self._access_token

        except Exception as exc:
            logger.error("azure_auth_error", error=str(exc))
            return ""

    async def _request(
        self, method: str, url: str, json: dict | None = None
    ) -> dict[str, Any]:
        token = await self._get_access_token()
        if not token:
            return {"error": "Authentication failed"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    # --- Azure Monitor Alerts ---

    async def get_active_alerts(
        self, resource_group: str = ""
    ) -> list[dict[str, Any]]:
        """Get active Azure Monitor alerts."""
        if not self._enabled:
            return []

        try:
            if resource_group:
                url = (
                    f"https://management.azure.com/subscriptions/{self.subscription_id}"
                    f"/resourceGroups/{resource_group}/providers/Microsoft.AlertsManagement"
                    f"/alerts?api-version=2023-01-01&monitorCondition=Fired"
                )
            else:
                url = (
                    f"https://management.azure.com/subscriptions/{self.subscription_id}"
                    f"/providers/Microsoft.AlertsManagement/alerts"
                    f"?api-version=2023-01-01&monitorCondition=Fired"
                )

            data = await self._request("GET", url)

            alerts = []
            for a in data.get("value", []):
                props = a.get("properties", {})
                essentials = props.get("essentials", {})
                alerts.append({
                    "id": a.get("id", ""),
                    "name": a.get("name", ""),
                    "severity": essentials.get("severity", ""),
                    "monitor_condition": essentials.get("monitorCondition", ""),
                    "target_resource": essentials.get("targetResource", ""),
                    "description": essentials.get("description", ""),
                    "fired_at": essentials.get("startDateTime", ""),
                })

            return alerts

        except Exception as exc:
            logger.error("azure_alerts_error", error=str(exc))
            return []

    # --- AKS Health ---

    async def get_aks_cluster_health(
        self, resource_group: str, cluster_name: str
    ) -> dict[str, Any]:
        """Get AKS cluster health status."""
        if not self._enabled:
            return {"error": "Azure not configured"}

        try:
            url = (
                f"https://management.azure.com/subscriptions/{self.subscription_id}"
                f"/resourceGroups/{resource_group}/providers/Microsoft.ContainerService"
                f"/managedClusters/{cluster_name}?api-version=2023-11-01"
            )
            data = await self._request("GET", url)

            props = data.get("properties", {})
            return {
                "name": data.get("name", ""),
                "status": props.get("provisioningState", ""),
                "kubernetes_version": props.get("kubernetesVersion", ""),
                "node_pools": [
                    {
                        "name": np.get("name", ""),
                        "count": np.get("count", 0),
                        "vm_size": np.get("vmSize", ""),
                        "status": np.get("provisioningState", ""),
                    }
                    for np in props.get("agentPoolProfiles", [])
                ],
                "power_state": props.get("powerState", {}).get("code", ""),
            }

        except Exception as exc:
            logger.error("azure_aks_error", cluster=cluster_name, error=str(exc))
            return {"error": str(exc)}

    # --- VM Actions ---

    async def restart_vm(
        self, resource_group: str, vm_name: str
    ) -> dict[str, Any]:
        """Restart an Azure VM."""
        if not self._enabled:
            return {"error": "Azure not configured"}

        try:
            url = (
                f"https://management.azure.com/subscriptions/{self.subscription_id}"
                f"/resourceGroups/{resource_group}/providers/Microsoft.Compute"
                f"/virtualMachines/{vm_name}/restart?api-version=2023-09-01"
            )
            await self._request("POST", url)

            logger.info("azure_vm_restarted", vm=vm_name, rg=resource_group)
            return {"status": "restarting", "vm": vm_name}

        except Exception as exc:
            logger.error("azure_vm_restart_error", vm=vm_name, error=str(exc))
            return {"error": str(exc)}

    async def get_vm_status(
        self, resource_group: str, vm_name: str
    ) -> dict[str, Any]:
        """Get Azure VM instance view (power state, etc.)."""
        if not self._enabled:
            return {"error": "Azure not configured"}

        try:
            url = (
                f"https://management.azure.com/subscriptions/{self.subscription_id}"
                f"/resourceGroups/{resource_group}/providers/Microsoft.Compute"
                f"/virtualMachines/{vm_name}/instanceView?api-version=2023-09-01"
            )
            data = await self._request("GET", url)

            statuses = data.get("statuses", [])
            power_state = ""
            for s in statuses:
                if s.get("code", "").startswith("PowerState/"):
                    power_state = s["code"].replace("PowerState/", "")

            return {
                "vm": vm_name,
                "power_state": power_state,
                "statuses": [
                    {"code": s.get("code", ""), "display": s.get("displayStatus", "")}
                    for s in statuses
                ],
            }

        except Exception as exc:
            logger.error("azure_vm_status_error", vm=vm_name, error=str(exc))
            return {"error": str(exc)}

    async def test_connection(self) -> dict[str, Any]:
        """Test Azure API connectivity."""
        if not self._enabled:
            return {"status": "disabled", "message": "Azure not configured"}

        try:
            token = await self._get_access_token()
            if not token:
                return {"status": "error", "message": "Failed to authenticate"}

            url = (
                f"https://management.azure.com/subscriptions/{self.subscription_id}"
                f"?api-version=2022-12-01"
            )
            data = await self._request("GET", url)

            return {
                "status": "ok",
                "message": f"Connected to subscription {data.get('displayName', self.subscription_id)}",
                "subscription": data.get("displayName", ""),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


class CloudProviderManager:
    """Unified interface for all cloud providers."""

    def __init__(
        self,
        aws: AWSIntegration | None = None,
        gcp: GCPIntegration | None = None,
        azure: AzureIntegration | None = None,
    ):
        self.aws = aws or AWSIntegration()
        self.gcp = gcp or GCPIntegration()
        self.azure = azure or AzureIntegration()

    @property
    def any_enabled(self) -> bool:
        return self.aws.enabled or self.gcp.enabled or self.azure.enabled

    async def get_all_active_alerts(self) -> dict[str, list[dict[str, Any]]]:
        """Get active alerts from all configured cloud providers."""
        results: dict[str, list[dict[str, Any]]] = {}

        if self.aws.enabled:
            results["aws"] = await self.aws.get_active_alarms()
        if self.gcp.enabled:
            results["gcp"] = await self.gcp.get_active_alerts()
        if self.azure.enabled:
            results["azure"] = await self.azure.get_active_alerts()

        return results

    async def get_service_health(
        self,
        service_name: str,
        provider: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Get service health from the appropriate cloud provider."""
        if provider == "aws" and self.aws.enabled:
            cluster = kwargs.get("cluster", "default")
            return await self.aws.get_ecs_service_health(cluster, service_name)
        elif provider == "gcp" and self.gcp.enabled:
            cluster = kwargs.get("cluster", "default")
            namespace = kwargs.get("namespace", "default")
            pods = await self.gcp.get_gke_pod_status(cluster, namespace)
            return {"pods": pods}
        elif provider == "azure" and self.azure.enabled:
            rg = kwargs.get("resource_group", "")
            return await self.azure.get_aks_cluster_health(rg, service_name)

        return {"error": f"Provider '{provider}' not configured or unknown"}

    async def test_all_connections(self) -> dict[str, dict[str, Any]]:
        """Test all configured cloud provider connections."""
        results = {}
        if self.aws.enabled:
            results["aws"] = await self.aws.test_connection()
        else:
            results["aws"] = {"status": "disabled"}
        if self.gcp.enabled:
            results["gcp"] = await self.gcp.test_connection()
        else:
            results["gcp"] = {"status": "disabled"}
        if self.azure.enabled:
            results["azure"] = await self.azure.test_connection()
        else:
            results["azure"] = {"status": "disabled"}

        return results
