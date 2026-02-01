"""API client for Proxmox Datacenter Manager."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    RESOURCE_TYPE_LXC,
    RESOURCE_TYPE_NODE,
    RESOURCE_TYPE_QEMU,
)

_LOGGER = logging.getLogger(__name__)


def _strip_pve_prefix(vm_type: str) -> str:
    """Strip the 'pve-' prefix from resource types for API calls.

    PDM uses 'pve-qemu' and 'pve-lxc' in resource listings, but
    the API endpoints use 'qemu' and 'lxc' in the URL path.
    """
    if vm_type.startswith("pve-"):
        return vm_type[4:]
    return vm_type


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
    ) -> Any:
        """Make an API request."""
        session = await self._ensure_session()
        url = f"{self._base_url}{endpoint}"
        headers = self._get_auth_header()

        _LOGGER.debug("PDM API request: %s %s params=%s data=%s", method, url, params, data)

        try:
            # PDM API expects JSON for POST requests
            async with session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data if method != "GET" else None,
                ssl=self._verify_ssl if self._verify_ssl else False,
            ) as response:
                response_text = await response.text()

                _LOGGER.debug(
                    "PDM API response: status=%s, body_preview=%s",
                    response.status,
                    response_text[:500] if len(response_text) > 500 else response_text
                )

                if response.status == 401:
                    raise AuthenticationError("Invalid API token")
                if response.status == 403:
                    raise AuthenticationError("Access denied - check API token permissions")
                if response.status >= 400:
                    raise APIError(f"API error {response.status}: {response_text}")

                try:
                    import json
                    result = json.loads(response_text)
                except Exception as parse_err:
                    _LOGGER.error("Failed to parse JSON response: %s", parse_err)
                    raise APIError(f"Invalid JSON response: {response_text[:200]}")

                # Handle Proxmox API envelope - data is usually in "data" key
                if isinstance(result, dict) and "data" in result:
                    return result["data"]
                return result

        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("Connection error to PDM: %s", err)
            raise ConnectionError(f"Failed to connect to PDM: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("API request failed: %s", err)
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

        result = await self._request("GET", "/resources/list", params=params)
        _LOGGER.debug("get_resources raw result type: %s", type(result))

        # Ensure we return a list
        if result is None:
            return []
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            # Maybe the data is nested differently
            _LOGGER.debug("get_resources got dict with keys: %s", result.keys())
            return [result]
        return []

    async def get_all_vms(self) -> list[VMInfo]:
        """Get all VMs and containers from all remotes."""
        vms: list[VMInfo] = []

        try:
            resources = await self.get_resources()
            _LOGGER.debug("get_all_vms: got %d remote resource entries", len(resources))

            for remote_data in resources:
                _LOGGER.debug("Processing remote_data: %s", type(remote_data))

                if not isinstance(remote_data, dict):
                    _LOGGER.warning("Unexpected remote_data type: %s", type(remote_data))
                    continue

                # Try different possible key names for remote
                remote_name = (
                    remote_data.get("remote") or
                    remote_data.get("name") or
                    remote_data.get("id") or
                    "unknown"
                )

                # Try different possible key names for resources list
                resource_list = (
                    remote_data.get("resources") or
                    remote_data.get("data") or
                    []
                )

                # If the remote_data itself looks like a resource, handle it
                if "type" in remote_data and "vmid" in remote_data:
                    resource_list = [remote_data]
                    remote_name = remote_data.get("remote", "unknown")

                _LOGGER.debug(
                    "Remote %s has %d resources",
                    remote_name,
                    len(resource_list) if isinstance(resource_list, list) else 0
                )

                if not isinstance(resource_list, list):
                    continue

                for resource in resource_list:
                    if not isinstance(resource, dict):
                        continue

                    res_type = resource.get("type", "")
                    _LOGGER.debug("Resource type: %s, keys: %s", res_type, resource.keys())

                    if res_type in (RESOURCE_TYPE_QEMU, RESOURCE_TYPE_LXC):
                        # Handle both snake_case and kebab-case field names
                        vmid = resource.get("vmid", 0)
                        name = resource.get("name", f"VM {vmid}")
                        node = resource.get("node", "unknown")

                        # Get remote from resource if available, otherwise use parent
                        res_remote = resource.get("remote", remote_name)

                        vm = VMInfo(
                            vmid=vmid,
                            name=name,
                            node=node,
                            remote=res_remote,
                            vm_type=res_type,
                            status=resource.get("status", "unknown"),
                            mem=resource.get("mem", 0),
                            maxmem=resource.get("maxmem", resource.get("max-mem", 0)),
                            cpu=resource.get("cpu", 0.0),
                            maxcpu=resource.get("maxcpu", resource.get("max-cpu", 0)),
                            uptime=resource.get("uptime", 0),
                        )
                        vms.append(vm)
                        _LOGGER.debug("Found VM: %s (id=%d) on %s/%s", vm.name, vm.vmid, vm.remote, vm.node)

            _LOGGER.info("get_all_vms: found %d total VMs/containers", len(vms))

        except ProxmoxDatacenterManagerError as err:
            _LOGGER.error("Failed to get VMs: %s", err)
            raise

        return vms

    async def find_vm_by_name(self, name: str) -> VMInfo | None:
        """Find a VM by name or VMID across all remotes and nodes."""
        vms = await self.get_all_vms()
        _LOGGER.debug("find_vm_by_name('%s'): searching %d VMs", name, len(vms))

        # Try to parse as VMID first
        try:
            search_vmid = int(name)
            for vm in vms:
                if vm.vmid == search_vmid:
                    _LOGGER.debug("Found VM by VMID: %s", vm)
                    return vm
        except ValueError:
            pass  # Not a number, search by name

        # Exact name match (case-insensitive)
        for vm in vms:
            if vm.name.lower() == name.lower():
                _LOGGER.debug("Found VM by exact name match: %s", vm)
                return vm

        # Partial name match as fallback
        for vm in vms:
            if name.lower() in vm.name.lower():
                _LOGGER.debug("Found VM by partial name match: %s", vm)
                return vm

        _LOGGER.warning("VM not found: '%s'. Available VMs: %s", name, [f"{v.name}({v.vmid})" for v in vms])
        return None

    async def get_nodes(self, remote: str) -> list[dict[str, Any]]:
        """Get all nodes for a remote from resources."""
        # Extract nodes from the resources response
        try:
            resources = await self.get_resources()
            nodes: list[dict[str, Any]] = []

            for remote_data in resources:
                if not isinstance(remote_data, dict):
                    continue

                remote_name = remote_data.get("remote", "")
                if remote_name != remote:
                    continue

                resource_list = remote_data.get("resources", [])
                if not isinstance(resource_list, list):
                    continue

                for resource in resource_list:
                    if isinstance(resource, dict) and resource.get("type") == RESOURCE_TYPE_NODE:
                        nodes.append(resource)

            return nodes
        except ProxmoxDatacenterManagerError:
            return []

    async def get_remotes(self) -> list[dict[str, Any]]:
        """Get all configured remotes from resources."""
        # Extract unique remotes from the resources response
        # The /remotes endpoint returns subdirectories, not actual remotes
        try:
            resources = await self.get_resources()
            remotes: list[dict[str, Any]] = []
            seen_remotes: set[str] = set()

            for remote_data in resources:
                if not isinstance(remote_data, dict):
                    continue

                remote_name = remote_data.get("remote", "")
                if remote_name and remote_name not in seen_remotes:
                    seen_remotes.add(remote_name)
                    remotes.append({
                        "name": remote_name,
                        "id": remote_name,
                    })

            _LOGGER.debug("get_remotes: found %d remotes from resources", len(remotes))
            return remotes
        except ProxmoxDatacenterManagerError:
            return []

    async def get_all_nodes(self) -> list[dict[str, Any]]:
        """Get all nodes from all remotes."""
        try:
            resources = await self.get_resources()
            nodes: list[dict[str, Any]] = []

            for remote_data in resources:
                if not isinstance(remote_data, dict):
                    continue

                remote_name = remote_data.get("remote", "unknown")
                resource_list = remote_data.get("resources", [])

                if not isinstance(resource_list, list):
                    continue

                for resource in resource_list:
                    if isinstance(resource, dict) and resource.get("type") == RESOURCE_TYPE_NODE:
                        node_info = dict(resource)
                        node_info["remote"] = remote_name
                        nodes.append(node_info)

            _LOGGER.debug("get_all_nodes: found %d nodes", len(nodes))
            return nodes
        except ProxmoxDatacenterManagerError:
            return []

    async def find_node_remote(self, node_name: str) -> str | None:
        """Find which remote a node belongs to.

        Args:
            node_name: The name of the node to find

        Returns:
            The remote name if found, None otherwise
        """
        node_info = await self.find_node_info(node_name)
        if node_info:
            return node_info.get("remote")
        return None

    async def find_node_info(self, node_name: str) -> dict[str, Any] | None:
        """Find full node info including IP address.

        Args:
            node_name: The name of the node to find

        Returns:
            Full node dict if found, None otherwise
        """
        nodes = await self.get_all_nodes()
        for node in nodes:
            name = node.get("node", node.get("name", ""))
            if name.lower() == node_name.lower():
                _LOGGER.debug(
                    "Found node '%s' in remote '%s', full info: %s",
                    node_name, node.get("remote"), node
                )
                return node
        _LOGGER.warning(
            "Node '%s' not found. Available nodes: %s",
            node_name,
            [f"{n.get('node', n.get('name'))}@{n.get('remote')}" for n in nodes]
        )
        return None

    async def debug_api_structure(self) -> dict[str, Any]:
        """Debug helper to inspect API structure."""
        debug_info: dict[str, Any] = {}

        try:
            # Test version endpoint
            version = await self._request("GET", "/version")
            debug_info["version"] = version
        except Exception as e:
            debug_info["version_error"] = str(e)

        try:
            # Test remotes endpoint
            remotes = await self._request("GET", "/remotes")
            debug_info["remotes_raw"] = remotes
            debug_info["remotes_type"] = str(type(remotes))
        except Exception as e:
            debug_info["remotes_error"] = str(e)

        try:
            # Test resources endpoint
            resources = await self._request("GET", "/resources/list")
            debug_info["resources_raw"] = resources
            debug_info["resources_type"] = str(type(resources))
        except Exception as e:
            debug_info["resources_error"] = str(e)

        try:
            # Get all nodes with their fields (helpful for finding IP address field)
            nodes = await self.get_all_nodes()
            debug_info["nodes"] = nodes
            if nodes:
                debug_info["node_fields"] = list(nodes[0].keys()) if nodes else []
        except Exception as e:
            debug_info["nodes_error"] = str(e)

        return debug_info

    async def migrate_vm_local(
        self,
        remote: str,
        vmid: int,
        target_node: str,
        source_node: str | None = None,
        vm_type: str = RESOURCE_TYPE_QEMU,
        online: bool = True,
        with_local_disks: bool = False,
    ) -> str:
        """Migrate a VM within the same cluster (local migration).

        Args:
            remote: The remote/cluster name
            vmid: The VM ID
            target_node: The destination node name
            source_node: The source node name (optional, PDM can auto-detect)
            vm_type: The VM type (pve-qemu or pve-lxc)
            online: Perform live migration if VM is running
            with_local_disks: Enable live storage migration for local disks
        """
        # Strip 'pve-' prefix for API endpoint
        api_vm_type = _strip_pve_prefix(vm_type)
        endpoint = f"/pve/remotes/{remote}/{api_vm_type}/{vmid}/migrate"

        # Use boolean values, not integers
        data: dict[str, Any] = {
            "target": target_node,
        }

        # Include source node if provided (helps PDM locate the VM)
        if source_node:
            data["node"] = source_node

        # Only include optional boolean parameters if True
        if online:
            data["online"] = True

        if with_local_disks:
            data["with-local-disks"] = True

        _LOGGER.debug("migrate_vm_local: endpoint=%s, data=%s", endpoint, data)
        result = await self._request("POST", endpoint, data=data)
        return result.get("data", result) if isinstance(result, dict) else result

    async def migrate_vm_remote(
        self,
        source_remote: str,
        vmid: int,
        target_remote: str,
        target_node: str | None = None,
        vm_type: str = RESOURCE_TYPE_QEMU,
        online: bool = True,
        delete_source: bool = True,
        storage_map: dict[str, str] | None = None,
        bridge_map: dict[str, str] | None = None,
    ) -> str:
        """Migrate a VM between different clusters (remote migration).

        Args:
            source_remote: The source remote/cluster name
            vmid: The VM ID
            target_remote: The destination remote/cluster name
            target_node: The target node within the destination remote
            vm_type: The VM type (pve-qemu or pve-lxc)
            online: Perform live migration if VM is running
            delete_source: Delete VM from source after migration
            storage_map: Storage name mappings (source:target)
            bridge_map: Bridge name mappings (source:target)
        """
        # Strip 'pve-' prefix for API endpoint
        api_vm_type = _strip_pve_prefix(vm_type)
        endpoint = f"/pve/remotes/{source_remote}/{api_vm_type}/{vmid}/remote-migrate"

        # Build storage and bridge mappings (required for remote migration)
        # Format: ["source:target", ...]
        storage_mappings: list[str] = []
        bridge_mappings: list[str] = []

        if storage_map:
            storage_mappings = [f"{k}:{v}" for k, v in storage_map.items()]
        else:
            # Default: same storage name on target
            storage_mappings = ["local:local"]

        if bridge_map:
            bridge_mappings = [f"{k}:{v}" for k, v in bridge_map.items()]
        else:
            # Default: same bridge name on target
            bridge_mappings = ["vmbr0:vmbr0"]

        # Build the data payload
        data: dict[str, Any] = {
            "target": target_remote,
            "target-storage": storage_mappings,
            "target-bridge": bridge_mappings,
            "delete": delete_source,
            "online": online,
        }

        # Try to get target node's IP address for target-endpoint
        # PDM GUI shows target-endpoint by IP address, not node name
        if target_node:
            node_info = await self.find_node_info(target_node)
            if node_info:
                # Look for IP address in common field names
                node_ip = (
                    node_info.get("ip") or
                    node_info.get("address") or
                    node_info.get("host") or
                    node_info.get("endpoint")
                )
                if node_ip:
                    # Format: IP:port (default Proxmox port is 8006)
                    if ":" not in str(node_ip):
                        node_ip = f"{node_ip}:8006"
                    data["target-endpoint"] = node_ip
                    _LOGGER.info(
                        "Cross-cluster migration: using target-endpoint '%s' for node '%s'",
                        node_ip, target_node
                    )
                else:
                    _LOGGER.warning(
                        "Cross-cluster migration: could not find IP for node '%s'. "
                        "Available node fields: %s. PDM will auto-select a node.",
                        target_node, list(node_info.keys())
                    )
            else:
                _LOGGER.warning(
                    "Cross-cluster migration: target_node '%s' not found in PDM data. "
                    "PDM will auto-select a node in remote '%s'.",
                    target_node, target_remote
                )

        _LOGGER.info(
            "migrate_vm_remote: VM %d from %s to %s",
            vmid, source_remote, target_remote
        )
        _LOGGER.debug("migrate_vm_remote: endpoint=%s, data=%s", endpoint, data)
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
        api_vm_type = _strip_pve_prefix(vm_type)
        endpoint = f"/pve/remotes/{remote}/{api_vm_type}/{vmid}/start"
        result = await self._request("POST", endpoint)
        return result.get("data", result) if isinstance(result, dict) else result

    async def stop_vm(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> str:
        """Stop a VM or container."""
        api_vm_type = _strip_pve_prefix(vm_type)
        endpoint = f"/pve/remotes/{remote}/{api_vm_type}/{vmid}/stop"
        result = await self._request("POST", endpoint)
        return result.get("data", result) if isinstance(result, dict) else result

    async def shutdown_vm(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> str:
        """Shutdown a VM or container gracefully."""
        api_vm_type = _strip_pve_prefix(vm_type)
        endpoint = f"/pve/remotes/{remote}/{api_vm_type}/{vmid}/shutdown"
        result = await self._request("POST", endpoint)
        return result.get("data", result) if isinstance(result, dict) else result

    async def get_vm_status(
        self, remote: str, vmid: int, vm_type: str = RESOURCE_TYPE_QEMU
    ) -> dict[str, Any]:
        """Get the status of a specific VM."""
        api_vm_type = _strip_pve_prefix(vm_type)
        endpoint = f"/pve/remotes/{remote}/{api_vm_type}/{vmid}/status"
        return await self._request("GET", endpoint)
