"""DataUpdateCoordinator for Proxmox Datacenter Manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    MigrationTask,
    ProxmoxDatacenterManagerAPI,
    ProxmoxDatacenterManagerError,
    VMInfo,
)
from .const import (
    DOMAIN,
    MIGRATION_STATE_COMPLETED,
    MIGRATION_STATE_FAILED,
    MIGRATION_STATE_IDLE,
    MIGRATION_STATE_MIGRATING,
    MIGRATION_STATE_SEARCHING,
    SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PDMData:
    """Data class for PDM coordinator data."""

    vms: list[VMInfo] = field(default_factory=list)
    remotes: list[dict[str, Any]] = field(default_factory=list)
    nodes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    migration_state: str = MIGRATION_STATE_IDLE
    migration_task: MigrationTask | None = None
    last_migration_error: str | None = None
    version: str = "unknown"


class PDMCoordinator(DataUpdateCoordinator[PDMData]):
    """Coordinator for Proxmox Datacenter Manager data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ProxmoxDatacenterManagerAPI,
        name: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.api = api
        self._data = PDMData()
        self._migration_lock = asyncio.Lock()
        self._active_upid: str | None = None
        self._active_remote: str | None = None

    async def _async_update_data(self) -> PDMData:
        """Fetch data from PDM."""
        try:
            # Get all VMs
            self._data.vms = await self.api.get_all_vms()

            # Get remotes (extracted from resources)
            try:
                self._data.remotes = await self.api.get_remotes()
                _LOGGER.debug("Found %d remotes", len(self._data.remotes))
            except ProxmoxDatacenterManagerError:
                _LOGGER.debug("Failed to get remotes list")

            # Get all nodes (extracted from resources)
            try:
                all_nodes = await self.api.get_all_nodes()
                # Group nodes by remote
                self._data.nodes = {}
                for node in all_nodes:
                    remote_name = node.get("remote", "unknown")
                    if remote_name not in self._data.nodes:
                        self._data.nodes[remote_name] = []
                    self._data.nodes[remote_name].append(node)
                _LOGGER.debug("Found %d total nodes across %d remotes", len(all_nodes), len(self._data.nodes))
            except ProxmoxDatacenterManagerError:
                _LOGGER.debug("Failed to get nodes list")

            # Get version info
            try:
                version_info = await self.api.get_version()
                self._data.version = version_info.get("version", "unknown") if isinstance(version_info, dict) else "unknown"
            except ProxmoxDatacenterManagerError:
                pass

            # Check migration task status if active
            if self._active_upid and self._active_remote:
                await self._update_migration_status()

        except ProxmoxDatacenterManagerError as err:
            raise UpdateFailed(f"Error communicating with PDM: {err}") from err

        return self._data

    async def _update_migration_status(self) -> None:
        """Update the status of an active migration task."""
        if not self._active_upid or not self._active_remote:
            return

        try:
            status = await self.api.get_task_status(self._active_remote, self._active_upid)
            task_status = status.get("status", "unknown")

            if self._data.migration_task:
                if task_status == "stopped":
                    exit_status = status.get("exitstatus", "")
                    if exit_status == "OK":
                        self._data.migration_state = MIGRATION_STATE_COMPLETED
                        self._data.migration_task.status = "completed"
                        self._data.migration_task.progress = 100.0
                    else:
                        self._data.migration_state = MIGRATION_STATE_FAILED
                        self._data.migration_task.status = "failed"
                        self._data.migration_task.error = exit_status
                        self._data.last_migration_error = exit_status

                    # Clear active task tracking
                    self._active_upid = None
                    self._active_remote = None
                else:
                    self._data.migration_task.status = task_status

        except ProxmoxDatacenterManagerError as err:
            _LOGGER.error("Failed to get migration task status: %s", err)

    async def find_vm_by_name(self, name: str) -> VMInfo | None:
        """Find a VM by name or VMID."""
        # Try to parse as VMID first
        search_vmid: int | None = None
        try:
            search_vmid = int(name)
        except ValueError:
            pass

        # First check cached data
        for vm in self._data.vms:
            if search_vmid is not None and vm.vmid == search_vmid:
                return vm
            if vm.name.lower() == name.lower():
                return vm

        # If not found, refresh and try again
        await self.async_refresh()

        for vm in self._data.vms:
            if search_vmid is not None and vm.vmid == search_vmid:
                return vm
            if vm.name.lower() == name.lower():
                return vm

        # Try partial match
        for vm in self._data.vms:
            if name.lower() in vm.name.lower():
                return vm

        _LOGGER.warning("VM not found: '%s'. Available: %s", name, [f"{v.name}({v.vmid})" for v in self._data.vms])
        return None

    async def migrate_vm(
        self,
        vm_name: str,
        target_host: str,
        target_remote: str | None = None,
        online: bool = True,
        with_local_disks: bool = False,
        storage_map: dict[str, str] | None = None,
        bridge_map: dict[str, str] | None = None,
    ) -> MigrationTask:
        """Migrate a VM to a target host."""
        async with self._migration_lock:
            # Update state to searching
            self._data.migration_state = MIGRATION_STATE_SEARCHING
            self._data.last_migration_error = None
            self.async_set_updated_data(self._data)

            # Find the VM
            vm = await self.find_vm_by_name(vm_name)
            if not vm:
                self._data.migration_state = MIGRATION_STATE_FAILED
                self._data.last_migration_error = f"VM '{vm_name}' not found"
                self.async_set_updated_data(self._data)
                raise ValueError(f"VM '{vm_name}' not found")

            # Determine if this is a local or remote migration
            is_remote_migration = target_remote is not None and target_remote != vm.remote

            _LOGGER.info(
                "Migrating VM %s (id=%d) from %s/%s to %s%s, is_remote=%s",
                vm.name, vm.vmid, vm.remote, vm.node, target_host,
                f" on {target_remote}" if target_remote else "",
                is_remote_migration
            )

            # Update state to migrating
            self._data.migration_state = MIGRATION_STATE_MIGRATING

            task = MigrationTask(
                upid="",
                vm_name=vm.name,
                vm_id=vm.vmid,
                source_remote=vm.remote,
                source_node=vm.node,
                target_remote=target_remote if is_remote_migration else None,
                target_node=target_host,
                status="starting",
                progress=0.0,
            )
            self._data.migration_task = task
            self.async_set_updated_data(self._data)

            try:
                if is_remote_migration:
                    # Remote migration between clusters
                    upid = await self.api.migrate_vm_remote(
                        source_remote=vm.remote,
                        vmid=vm.vmid,
                        target_remote=target_remote,
                        target_node=target_host,
                        vm_type=vm.vm_type,
                        online=online,
                        delete_source=True,
                        storage_map=storage_map,
                        bridge_map=bridge_map,
                    )
                else:
                    # Local migration within the same cluster
                    upid = await self.api.migrate_vm_local(
                        remote=vm.remote,
                        vmid=vm.vmid,
                        target_node=target_host,
                        vm_type=vm.vm_type,
                        online=online,
                        with_local_disks=with_local_disks,
                    )

                task.upid = upid if isinstance(upid, str) else str(upid)
                task.status = "running"
                self._active_upid = task.upid
                self._active_remote = vm.remote

            except ProxmoxDatacenterManagerError as err:
                self._data.migration_state = MIGRATION_STATE_FAILED
                task.status = "failed"
                task.error = str(err)
                self._data.last_migration_error = str(err)
                self.async_set_updated_data(self._data)
                raise

            self.async_set_updated_data(self._data)
            return task

    def get_all_nodes(self) -> list[dict[str, Any]]:
        """Get all nodes from all remotes."""
        all_nodes: list[dict[str, Any]] = []
        for remote_name, nodes in self._data.nodes.items():
            for node in nodes:
                all_nodes.append({
                    "remote": remote_name,
                    "node": node.get("node", node.get("name", "unknown")),
                    "status": node.get("status", "unknown"),
                })
        return all_nodes

    def reset_migration_state(self) -> None:
        """Reset the migration state to idle."""
        self._data.migration_state = MIGRATION_STATE_IDLE
        self._data.migration_task = None
        self._active_upid = None
        self._active_remote = None
        self.async_set_updated_data(self._data)
