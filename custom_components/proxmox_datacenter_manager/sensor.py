"""Sensor platform for Proxmox Datacenter Manager."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
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

    entities: list[SensorEntity] = [
        PDMMigrationStateSensor(coordinator, entry),
        PDMVMCountSensor(coordinator, entry),
        PDMNodeCountSensor(coordinator, entry),
        PDMRemoteCountSensor(coordinator, entry),
    ]

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
