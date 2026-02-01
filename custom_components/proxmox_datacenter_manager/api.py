"""API client for Proxmox Datacenter Manager."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    RESOURCE_TYPE_LXC,
    RESOURCE_TYPE_QEMU,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class VMInfo:
    """Represents a virtual machine or container."""

    vmid: int
    name: str
    node: str
    remote: str
    vm_type: str  # "qemu" or "lxc"
    status: str
    mem: int = 0
    maxmem: int = 0
    cpu: float = 0.0
    maxcpu: int = 0
    uptime: int = 0

    @property
    def unique_id(self) -> str:
        """Return unique identifier for this VM."""
        return f"{self.remote}_{self.node}_{self.vmid}"


@dataclass
class MigrationTask:
    """Represents a migration task."""

    upid: str
    vm_name: str
    vm_id: int
    source_remote: str
    source_node: str
    target_remote: str | None
    target_node: str
    status: str
    progress: float = 0.0
    error: str | None = None


class ProxmoxDatacenterManagerError(Exception):
    """Base exception for PDM API errors."""


class AuthenticationError(ProxmoxDatacenterManagerError):
    """Authentication failed."""


class ConnectionError(ProxmoxDatacenterManagerError):
    """Connection to PDM failed."""


class APIError(ProxmoxDatacenterManagerError):
    """API returned an error."""


class ProxmoxDatacenterManagerAPI:
    """Client for the Proxmox Datacenter Manager API."""

    def __init__(
        self,
        host: str,
        port: int,
        api_token_id: str,
        api_token_secret: str,
        verify_ssl: bool = True,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._host = host
        self._port = port
        self._api_token_id = api_token_id
        self._api_token_secret = api_token_secret
        self._verify_ssl = verify_ssl
        self._session = session
        self._base_url = f"https://{host}:{port}/api2/json"
        self._owns_session = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an aiohttp session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._verify_ssl)
            self._session = aiohttp.ClientSession(connector=connector)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the API session."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def _get_auth_header(self) -> dict[str, str]:
        """Get the authorization header for API requests."""
        return {
            "Authorization": f"PDMAPIToken={self._api_token_id}:{self._api_token_secret}"
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an API request."""
        session = await self._ensure_session()
        url = f"{self._base_url}{endpoint}"
        headers = self._get_auth_header()

        try:
            async with session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data if method != "GET" else None,
                ssl=self._verify_ssl if self._verify_ssl else False,
            ) as response:
                if response.status == 401:
                    raise AuthenticationError("Invalid API token")
                if response.status == 403:
                    raise AuthenticationError("Access denied - check API token permissions")
                if response.status >= 400:
                    text = await response.text()
                    raise APIError(f"API error {response.status}: {text}")

                result = await response.json()
                return result.get("data", result)

        except aiohttp.ClientConnectorError as err:
            raise ConnectionError(f"Failed to connect to PDM: {err}") from err
        except aiohttp.ClientError as err:
            raise APIError(f"API request failed: {err}") from err

    async def test_connection(self) -> bool:
        """Test the connection to the PDM server."""
        try:
            await self._request("GET", "/version")
            return True
        except ProxmoxDatacenterManagerError:
            return False

    async def get_version(self) -> dict[str, Any]:
        """Get the PDM version information."""
        return await self._request("GET", "/version")

    async def get_resources(
        self,
        resource_type: str | None = None,
        search: str | None = None,
        max_age: int = 30,
    ) -> list[dict[str, Any]]:
        """Get all resources from PDM."""
        params: dict[str, Any] = {"max-age": max_age}
        if resource_type:
            params["resource-type"] = resource_type
        if search:
            params["search"] = search

        return await self._request("GET", "/resources/list", params=params)

    async def get_all_vms(self) -> list[VMInfo]:
        """Get all VMs and containers from all remotes."""
        vms: list[VMInfo] = []

        try:
            resources = await self.get_resources()

            for remote_data in resources:
                remote_name = remote_data.get("remote", "unknown")
                resource_list = remote_data.get("resources", [])

                for resource in resource_list:
                    res_type = resource.get("type", "")
                    if res_type in (RESOURCE_TYPE_QEMU, RESOURCE_TYPE_LXC):
                        vm = VMInfo(
                            vmid=resource.get("vmid", 0),
                            name=resource.get("name", f"VM {resource.get('vmid', 'unknown')}"),
                            node=resource.get("node", "unknown"),
                            remote=remote_name,
                            vm_type=res_type,
                            status=resource.get("status", "unknown"),
                            mem=resource.get("mem", 0),
                            maxmem=resource.get("maxmem", 0),
                            cpu=resource.get("cpu", 0.0),
                            maxcpu=resource.get("maxcpu", 0),
                            uptime=resource.get("uptime", 0),
                        )
                        vms.append(vm)

        except ProxmoxDatacenterManagerError as err:
            _LOGGER.error("Failed to get VMs: %s", err)
            raise

        return vms

    async def find_vm_by_name(self, name: str) -> VMInfo | None:
        """Find a VM by name across all remotes and nodes."""
        vms = await self.get_all_vms()

        # Exact match first
        for vm in vms:
            if vm.name.lower() == name.lower():
                return vm

        # Partial match as fallback
        for vm in vms:
            if name.lower() in vm.name.lower():
                return vm

        return None

    async def get_nodes(self, remote: str) -> list[dict[str, Any]]:
        """Get all nodes for a remote."""
        return await self._request("GET", f"/pve/remotes/{remote}/nodes")

    async def get_remotes(self) -> list[dict[str, Any]]:
        """Get all configured remotes."""
        return await self._request("GET", "/pve/remotes")

    async def migrate_vm_local(
        self,
        remote: str,
        vmid: int,
        target_node: str,
        vm_type: str = RESOURCE_TYPE_QEMU,
        online: bool = True,
        with_local_disks: bool = False,
    ) -> str:
        """Migrate a VM within the same cluster (local migration)."""
        endpoint = f"/pve/remotes/{remote}/{vm_type}/{vmid}/migrate"

        data: dict[str, Any] = {
            "target": target_node,
            "online": 1 if online else 0,
        }

        if with_local_disks:
            data["with-local-disks"] = 1

        result = await self._request("POST", endpoint, data=data)
        return result.get("data", result) if isinstance(result, dict) else result

    async def migrate_vm_remote(
        self,
        source_remote: str,
        vmid: int,
        target_remote: str,
        target_node: str,
        vm_type: str = RESOURCE_TYPE_QEMU,
        online: bool = True,
        delete_source: bool = True,
        storage_map: dict[str, str] | None = None,
        bridge_map: dict[str, str] | None = None,
    ) -> str:
        """Migrate a VM between different clusters (remote migration)."""
        endpoint = f"/pve/remotes/{source_remote}/{vm_type}/{vmid}/remote-migrate"

        data: dict[str, Any] = {
            "target-remote": target_remote,
            "target-node": target_node,
            "online": 1 if online else 0,
            "delete": 1 if delete_source else 0,
        }

        if storage_map:
            # Format: "source-storage:target-storage,..."
            data["target-storage"] = ",".join(
                f"{k}:{v}" for k, v in storage_map.items()
            )

        if bridge_map:
            # Format: "source-bridge:target-bridge,..."
            data["target-bridge"] = ",".join(
                f"{k}:{v}" for k, v in bridge_map.items()
            )

        result = await self._request("POST", endpoint, data=data)
        return result.get("data", result) if isinstance(result, dict) else result

    async def get_task_status(self, remote: str, upid: str) -> dict[str, Any]:
        """Get the status of a task."""
        return await self._request("GET", f"/pve/remotes/{remote}/tasks/{upid}/status")

    async def get_tasks(
        self,
        remote: str | None = None,
        running: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get tasks from PDM."""
        params: dict[str, Any] = {"limit": limit}
        if running:
            params["running"] = 1

        if remote:
            return await self._request("GET", f"/pve/remotes/{remote}/tasks", params=params)

        # Get tasks from all remotes
        all_tasks: list[dict[str, Any]] = []
        try:
            remotes = await self.get_remotes()
            for r in remotes:
                remote_name = r.get("name", r.get("id"))
                if remote_name:
                    tasks = await self._request(
                        "GET", f"/pve/remotes/{remote_name}/tasks", params=params
                    )
                    all_tasks.extend(tasks if isinstance(tasks, list) else [])
        except ProxmoxDatacenterManagerError:
            pass

        return all_tasks

    async def start_vm(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> str:
        """Start a VM or container."""
        endpoint = f"/pve/remotes/{remote}/{vm_type}/{vmid}/start"
        result = await self._request("POST", endpoint)
        return result.get("data", result) if isinstance(result, dict) else result

    async def stop_vm(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> str:
        """Stop a VM or container."""
        endpoint = f"/pve/remotes/{remote}/{vm_type}/{vmid}/stop"
        result = await self._request("POST", endpoint)
        return result.get("data", result) if isinstance(result, dict) else result

    async def shutdown_vm(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> str:
        """Shutdown a VM or container gracefully."""
        endpoint = f"/pve/remotes/{remote}/{vm_type}/{vmid}/shutdown"
        result = await self._request("POST", endpoint)
        return result.get("data", result) if isinstance(result, dict) else result

    async def get_vm_status(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> dict[str, Any]:
        """Get the status of a specific VM."""
        endpoint = f"/pve/remotes/{remote}/{vm_type}/{vmid}/status"
        return await self._request("GET", endpoint)
