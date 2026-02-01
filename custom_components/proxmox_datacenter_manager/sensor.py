"""Sensor platform for Proxmox Datacenter Manager."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import VMInfo
from .const import (
    CONF_NODE_SENSORS,
    CONF_VM_FILTER,
    CONF_VM_SENSORS,
    DEFAULT_NODE_SENSORS,
    DEFAULT_VM_FILTER,
    DEFAULT_VM_SENSORS,
    DOMAIN,
    MIGRATION_STATE_IDLE,
)
from .coordinator import PDMCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Proxmox Datacenter Manager sensors."""
    coordinator: PDMCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Get options
    options = entry.options
    node_sensors_enabled = options.get(CONF_NODE_SENSORS, DEFAULT_NODE_SENSORS)
    vm_sensors_enabled = options.get(CONF_VM_SENSORS, DEFAULT_VM_SENSORS)
    vm_filter = options.get(CONF_VM_FILTER, DEFAULT_VM_FILTER)

    # Base sensors (always created)
    entities: list[SensorEntity] = [
        PDMMigrationStateSensor(coordinator, entry),
        PDMVMCountSensor(coordinator, entry),
        PDMNodeCountSensor(coordinator, entry),
        PDMRemoteCountSensor(coordinator, entry),
    ]

    # Node sensors (if enabled)
    if node_sensors_enabled and coordinator.data:
        for remote_name, nodes in coordinator.data.nodes.items():
            for node in nodes:
                node_name = node.get("node", node.get("name", "unknown"))
                entities.append(PDMNodeSensor(coordinator, entry, remote_name, node_name))

    # VM sensors (if enabled)
    if vm_sensors_enabled and coordinator.data:
        # Compile regex filter if provided
        vm_filter_regex = None
        if vm_filter:
            try:
                vm_filter_regex = re.compile(vm_filter, re.IGNORECASE)
            except re.error:
                pass  # Invalid regex, ignore filter

        for vm in coordinator.data.vms:
            # Apply filter if specified
            if vm_filter_regex and not vm_filter_regex.search(vm.name):
                continue
            entities.append(PDMVMSensor(coordinator, entry, vm))

    async_add_entities(entities)


class PDMBaseSensor(CoordinatorEntity[PDMCoordinator], SensorEntity):
    """Base class for PDM sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PDMCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"PDM {self._entry.data.get(CONF_HOST, 'Unknown')}",
            manufacturer="Proxmox",
            model="Datacenter Manager",
            sw_version=self.coordinator.data.version if self.coordinator.data else "unknown",
        )


class PDMMigrationStateSensor(PDMBaseSensor):
    """Sensor for migration state."""

    def __init__(self, coordinator: PDMCoordinator, entry: ConfigEntry) -> None:
        """Initialize the migration state sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="migration_state",
                name="Migration State",
                icon="mdi:server-network",
            ),
        )

    @property
    def native_value(self) -> str:
        """Return the current migration state."""
        if self.coordinator.data:
            return self.coordinator.data.migration_state
        return MIGRATION_STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, str | int | float | None]:
        """Return extra state attributes."""
        attrs: dict[str, str | int | float | None] = {}

        if self.coordinator.data:
            data = self.coordinator.data

            if data.migration_task:
                task = data.migration_task
                attrs["vm_name"] = task.vm_name
                attrs["vm_id"] = task.vm_id
                attrs["source_remote"] = task.source_remote
                attrs["source_node"] = task.source_node
                attrs["target_node"] = task.target_node
                attrs["task_status"] = task.status
                attrs["progress"] = task.progress

                if task.target_remote:
                    attrs["target_remote"] = task.target_remote

                if task.error:
                    attrs["error"] = task.error

                if task.upid:
                    attrs["upid"] = task.upid

            if data.last_migration_error:
                attrs["last_error"] = data.last_migration_error

        return attrs


class PDMVMCountSensor(PDMBaseSensor):
    """Sensor for VM count."""

    def __init__(self, coordinator: PDMCoordinator, entry: ConfigEntry) -> None:
        """Initialize the VM count sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="vm_count",
                name="Total VMs",
                icon="mdi:server",
                state_class=SensorStateClass.MEASUREMENT,
            ),
        )

    @property
    def native_value(self) -> int:
        """Return the total number of VMs."""
        if self.coordinator.data:
            return len(self.coordinator.data.vms)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Return extra state attributes."""
        attrs: dict[str, int] = {"qemu": 0, "lxc": 0, "running": 0, "stopped": 0}

        if self.coordinator.data:
            for vm in self.coordinator.data.vms:
                if vm.vm_type == "qemu":
                    attrs["qemu"] += 1
                elif vm.vm_type == "lxc":
                    attrs["lxc"] += 1

                if vm.status == "running":
                    attrs["running"] += 1
                else:
                    attrs["stopped"] += 1

        return attrs


class PDMNodeCountSensor(PDMBaseSensor):
    """Sensor for node count."""

    def __init__(self, coordinator: PDMCoordinator, entry: ConfigEntry) -> None:
        """Initialize the node count sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="node_count",
                name="Total Nodes",
                icon="mdi:server-network",
                state_class=SensorStateClass.MEASUREMENT,
            ),
        )

    @property
    def native_value(self) -> int:
        """Return the total number of nodes."""
        if self.coordinator.data:
            return sum(len(nodes) for nodes in self.coordinator.data.nodes.values())
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Return extra state attributes."""
        attrs: dict[str, list[str]] = {}

        if self.coordinator.data:
            for remote, nodes in self.coordinator.data.nodes.items():
                node_names = [n.get("node", n.get("name", "unknown")) for n in nodes]
                attrs[remote] = node_names

        return attrs


class PDMRemoteCountSensor(PDMBaseSensor):
    """Sensor for remote count."""

    def __init__(self, coordinator: PDMCoordinator, entry: ConfigEntry) -> None:
        """Initialize the remote count sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="remote_count",
                name="Total Remotes",
                icon="mdi:lan",
                state_class=SensorStateClass.MEASUREMENT,
            ),
        )

    @property
    def native_value(self) -> int:
        """Return the total number of remotes."""
        if self.coordinator.data:
            return len(self.coordinator.data.remotes)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Return extra state attributes."""
        if self.coordinator.data:
            return {
                "remotes": [
                    r.get("name", r.get("id", "unknown"))
                    for r in self.coordinator.data.remotes
                ]
            }
        return {"remotes": []}


class PDMNodeSensor(CoordinatorEntity[PDMCoordinator], SensorEntity):
    """Sensor for individual Proxmox node status."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PDMCoordinator,
        entry: ConfigEntry,
        remote_name: str,
        node_name: str,
    ) -> None:
        """Initialize the node sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._remote_name = remote_name
        self._node_name = node_name
        self._attr_unique_id = f"{entry.entry_id}_node_{remote_name}_{node_name}"
        self._attr_name = f"Node {node_name}"
        self._attr_icon = "mdi:server"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_node_{self._remote_name}_{self._node_name}")},
            name=f"PDM Node {self._node_name}",
            manufacturer="Proxmox",
            model="PVE Node",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    def _get_node_data(self) -> dict[str, Any] | None:
        """Get the current node data from coordinator."""
        if not self.coordinator.data:
            return None
        nodes = self.coordinator.data.nodes.get(self._remote_name, [])
        for node in nodes:
            if node.get("node", node.get("name")) == self._node_name:
                return node
        return None

    @property
    def native_value(self) -> str:
        """Return the node status."""
        node = self._get_node_data()
        if node:
            return node.get("status", "unknown")
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            "remote": self._remote_name,
            "node": self._node_name,
        }
        node = self._get_node_data()
        if node:
            # CPU usage
            cpu = node.get("cpu", 0)
            maxcpu = node.get("maxcpu", 1)
            if isinstance(cpu, (int, float)) and maxcpu > 0:
                attrs["cpu_usage"] = round(cpu * 100, 1)
            attrs["cpu_cores"] = maxcpu

            # Memory usage
            mem = node.get("mem", 0)
            maxmem = node.get("maxmem", 0)
            if maxmem > 0:
                attrs["memory_usage_percent"] = round((mem / maxmem) * 100, 1)
                attrs["memory_used_gb"] = round(mem / (1024**3), 2)
                attrs["memory_total_gb"] = round(maxmem / (1024**3), 2)

            # Uptime
            uptime = node.get("uptime", 0)
            if uptime:
                attrs["uptime_seconds"] = uptime
                attrs["uptime_days"] = round(uptime / 86400, 1)

        return attrs


class PDMVMSensor(CoordinatorEntity[PDMCoordinator], SensorEntity):
    """Sensor for individual VM/container status."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PDMCoordinator,
        entry: ConfigEntry,
        vm: VMInfo,
    ) -> None:
        """Initialize the VM sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._vmid = vm.vmid
        self._vm_name = vm.name
        self._remote = vm.remote
        self._attr_unique_id = f"{entry.entry_id}_vm_{vm.unique_id}"
        self._attr_name = f"VM {vm.name}"
        self._attr_icon = "mdi:monitor" if vm.vm_type == "pve-qemu" else "mdi:docker"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_vm_{self._vmid}")},
            name=f"PDM VM {self._vm_name}",
            manufacturer="Proxmox",
            model="Virtual Machine",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    def _get_vm_data(self) -> VMInfo | None:
        """Get the current VM data from coordinator."""
        if not self.coordinator.data:
            return None
        for vm in self.coordinator.data.vms:
            if vm.vmid == self._vmid:
                return vm
        return None

    @property
    def native_value(self) -> str:
        """Return the VM status."""
        vm = self._get_vm_data()
        if vm:
            return vm.status
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            "vmid": self._vmid,
            "name": self._vm_name,
        }
        vm = self._get_vm_data()
        if vm:
            attrs["remote"] = vm.remote
            attrs["node"] = vm.node
            attrs["type"] = "qemu" if "qemu" in vm.vm_type else "lxc"

            # CPU usage
            if vm.cpu is not None:
                attrs["cpu_usage"] = round(vm.cpu * 100, 1)
            attrs["cpu_cores"] = vm.maxcpu

            # Memory usage
            if vm.maxmem > 0:
                attrs["memory_usage_percent"] = round((vm.mem / vm.maxmem) * 100, 1)
                attrs["memory_used_mb"] = round(vm.mem / (1024**2), 1)
                attrs["memory_total_mb"] = round(vm.maxmem / (1024**2), 1)

            # Uptime
            if vm.uptime:
                attrs["uptime_seconds"] = vm.uptime
                hours, remainder = divmod(vm.uptime, 3600)
                minutes, _ = divmod(remainder, 60)
                attrs["uptime_formatted"] = f"{hours}h {minutes}m"

        return attrs
