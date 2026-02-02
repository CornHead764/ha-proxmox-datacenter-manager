"""The Proxmox Datacenter Manager integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    AuthenticationError,
    ConnectionError,
    ProxmoxDatacenterManagerAPI,
    ProxmoxDatacenterManagerError,
)
from .const import (
    ATTR_BRIDGE_MAP,
    ATTR_ONLINE,
    ATTR_STORAGE_MAP,
    ATTR_TARGET_ENDPOINT,
    ATTR_TARGET_HOST,
    ATTR_TARGET_REMOTE,
    ATTR_VM_NAME,
    ATTR_WITH_LOCAL_DISKS,
    CONF_API_TOKEN_ID,
    CONF_API_TOKEN_SECRET,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    SERVICE_MIGRATE_VM,
)
from .coordinator import PDMCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Service schemas
SERVICE_MIGRATE_VM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_VM_NAME): cv.string,
        vol.Required(ATTR_TARGET_HOST): cv.string,
        vol.Optional(ATTR_TARGET_REMOTE): cv.string,
        vol.Optional(ATTR_TARGET_ENDPOINT): cv.string,
        vol.Optional(ATTR_ONLINE, default=True): cv.boolean,
        vol.Optional(ATTR_WITH_LOCAL_DISKS, default=False): cv.boolean,
        vol.Optional(ATTR_STORAGE_MAP): dict,  # For remote migrations: {"source": "target"}
        vol.Optional(ATTR_BRIDGE_MAP): dict,   # For remote migrations: {"source": "target"}
    }
)

SERVICE_VM_NAME_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_VM_NAME): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Proxmox Datacenter Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    api_token_id = entry.data[CONF_API_TOKEN_ID]
    api_token_secret = entry.data[CONF_API_TOKEN_SECRET]
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    api = ProxmoxDatacenterManagerAPI(
        host=host,
        port=port,
        api_token_id=api_token_id,
        api_token_secret=api_token_secret,
        verify_ssl=verify_ssl,
        session=session,
    )

    try:
        if not await api.test_connection():
            raise ConfigEntryNotReady(f"Cannot connect to PDM at {host}:{port}")
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except ConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot connect to PDM: {err}") from err

    coordinator = PDMCoordinator(
        hass,
        api,
        name=f"PDM {host}",
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _async_setup_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: PDMCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.api.close()

    # Unregister services if no more entries
    if not hass.data[DOMAIN]:
        _async_unregister_services(hass)

    return unload_ok


async def _async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for Proxmox Datacenter Manager."""

    async def get_coordinator() -> PDMCoordinator | None:
        """Get the first available coordinator."""
        for coordinator in hass.data[DOMAIN].values():
            if isinstance(coordinator, PDMCoordinator):
                return coordinator
        return None

    async def handle_migrate_vm(call: ServiceCall) -> dict[str, Any]:
        """Handle the migrate_vm service call."""
        coordinator = await get_coordinator()
        if not coordinator:
            raise ValueError("No Proxmox Datacenter Manager instance configured")

        vm_name = call.data[ATTR_VM_NAME]
        target_host = call.data[ATTR_TARGET_HOST]
        target_remote = call.data.get(ATTR_TARGET_REMOTE)
        target_endpoint = call.data.get(ATTR_TARGET_ENDPOINT)
        online = call.data.get(ATTR_ONLINE, True)
        with_local_disks = call.data.get(ATTR_WITH_LOCAL_DISKS, False)
        storage_map = call.data.get(ATTR_STORAGE_MAP)
        bridge_map = call.data.get(ATTR_BRIDGE_MAP)

        _LOGGER.debug(
            "migrate_vm service called: vm=%s, target_host=%s, target_remote=%s, "
            "target_endpoint=%s, online=%s",
            vm_name, target_host, target_remote, target_endpoint, online
        )

        try:
            task = await coordinator.migrate_vm(
                vm_name=vm_name,
                target_host=target_host,
                target_remote=target_remote,
                target_endpoint=target_endpoint,
                online=online,
                with_local_disks=with_local_disks,
                storage_map=storage_map,
                bridge_map=bridge_map,
            )
            return {
                "success": True,
                "upid": task.upid,
                "vm_name": task.vm_name,
                "vm_id": task.vm_id,
                "source_remote": task.source_remote,
                "source_node": task.source_node,
                "target_node": task.target_node,
                "target_remote": task.target_remote,
            }
        except ValueError as err:
            return {"success": False, "error": str(err)}
        except ProxmoxDatacenterManagerError as err:
            return {"success": False, "error": str(err)}

    async def handle_start_vm(call: ServiceCall) -> dict[str, Any]:
        """Handle the start_vm service call."""
        coordinator = await get_coordinator()
        if not coordinator:
            raise ValueError("No Proxmox Datacenter Manager instance configured")

        vm_name = call.data[ATTR_VM_NAME]

        try:
            vm = await coordinator.find_vm_by_name(vm_name)
            if not vm:
                return {"success": False, "error": f"VM '{vm_name}' not found"}

            upid = await coordinator.api.start_vm(
                remote=vm.remote,
                vmid=vm.vmid,
                vm_type=vm.vm_type,
            )
            return {"success": True, "upid": upid, "vm_name": vm.name, "vm_id": vm.vmid}
        except ProxmoxDatacenterManagerError as err:
            return {"success": False, "error": str(err)}

    async def handle_stop_vm(call: ServiceCall) -> dict[str, Any]:
        """Handle the stop_vm service call."""
        coordinator = await get_coordinator()
        if not coordinator:
            raise ValueError("No Proxmox Datacenter Manager instance configured")

        vm_name = call.data[ATTR_VM_NAME]

        try:
            vm = await coordinator.find_vm_by_name(vm_name)
            if not vm:
                return {"success": False, "error": f"VM '{vm_name}' not found"}

            upid = await coordinator.api.stop_vm(
                remote=vm.remote,
                vmid=vm.vmid,
                vm_type=vm.vm_type,
            )
            return {"success": True, "upid": upid, "vm_name": vm.name, "vm_id": vm.vmid}
        except ProxmoxDatacenterManagerError as err:
            return {"success": False, "error": str(err)}

    async def handle_shutdown_vm(call: ServiceCall) -> dict[str, Any]:
        """Handle the shutdown_vm service call."""
        coordinator = await get_coordinator()
        if not coordinator:
            raise ValueError("No Proxmox Datacenter Manager instance configured")

        vm_name = call.data[ATTR_VM_NAME]

        try:
            vm = await coordinator.find_vm_by_name(vm_name)
            if not vm:
                return {"success": False, "error": f"VM '{vm_name}' not found"}

            upid = await coordinator.api.shutdown_vm(
                remote=vm.remote,
                vmid=vm.vmid,
                vm_type=vm.vm_type,
            )
            return {"success": True, "upid": upid, "vm_name": vm.name, "vm_id": vm.vmid}
        except ProxmoxDatacenterManagerError as err:
            return {"success": False, "error": str(err)}

    async def handle_reset_migration_state(call: ServiceCall) -> None:
        """Handle the reset_migration_state service call."""
        coordinator = await get_coordinator()
        if coordinator:
            coordinator.reset_migration_state()

    async def handle_list_vms(call: ServiceCall) -> dict[str, Any]:
        """Handle the list_vms service call - useful for debugging."""
        coordinator = await get_coordinator()
        if not coordinator:
            return {"success": False, "error": "No PDM instance configured"}

        try:
            vms = await coordinator.api.get_all_vms()
            return {
                "success": True,
                "count": len(vms),
                "vms": [
                    {
                        "name": vm.name,
                        "vmid": vm.vmid,
                        "node": vm.node,
                        "remote": vm.remote,
                        "type": vm.vm_type,
                        "status": vm.status,
                    }
                    for vm in vms
                ],
            }
        except ProxmoxDatacenterManagerError as err:
            return {"success": False, "error": str(err)}

    async def handle_debug_api(call: ServiceCall) -> dict[str, Any]:
        """Handle the debug_api service call - inspect raw API responses."""
        coordinator = await get_coordinator()
        if not coordinator:
            return {"success": False, "error": "No PDM instance configured"}

        try:
            debug_info = await coordinator.api.debug_api_structure()
            return {"success": True, **debug_info}
        except Exception as err:
            return {"success": False, "error": str(err)}

    # Register all services
    if not hass.services.has_service(DOMAIN, SERVICE_MIGRATE_VM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MIGRATE_VM,
            handle_migrate_vm,
            schema=SERVICE_MIGRATE_VM_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, "start_vm"):
        hass.services.async_register(
            DOMAIN,
            "start_vm",
            handle_start_vm,
            schema=SERVICE_VM_NAME_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, "stop_vm"):
        hass.services.async_register(
            DOMAIN,
            "stop_vm",
            handle_stop_vm,
            schema=SERVICE_VM_NAME_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, "shutdown_vm"):
        hass.services.async_register(
            DOMAIN,
            "shutdown_vm",
            handle_shutdown_vm,
            schema=SERVICE_VM_NAME_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, "reset_migration_state"):
        hass.services.async_register(
            DOMAIN,
            "reset_migration_state",
            handle_reset_migration_state,
        )

    if not hass.services.has_service(DOMAIN, "list_vms"):
        hass.services.async_register(
            DOMAIN,
            "list_vms",
            handle_list_vms,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, "debug_api"):
        hass.services.async_register(
            DOMAIN,
            "debug_api",
            handle_debug_api,
            supports_response=SupportsResponse.ONLY,
        )


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister services."""
    for service in [SERVICE_MIGRATE_VM, "start_vm", "stop_vm", "shutdown_vm", "reset_migration_state", "list_vms", "debug_api"]:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
