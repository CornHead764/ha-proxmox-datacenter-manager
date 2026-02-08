# CLAUDE.md - Project Guide for AI Assistants

## Project Overview

This is a **Home Assistant custom integration** for [Proxmox Datacenter Manager (PDM)](https://www.proxmox.com/en/products/proxmox-datacenter-manager). It provides VM lifecycle management, migration orchestration, and resource monitoring across multiple Proxmox clusters from within Home Assistant.

**Domain**: `proxmox_datacenter_manager`
**Owner**: @CornHead764

## Quick Start

There are no build steps, test frameworks, or linters configured. This is a Home Assistant integration that gets loaded directly by HA at runtime.

To validate Python syntax:
```bash
python3 -m py_compile custom_components/proxmox_datacenter_manager/__init__.py
python3 -m py_compile custom_components/proxmox_datacenter_manager/api.py
python3 -m py_compile custom_components/proxmox_datacenter_manager/coordinator.py
```

## Architecture

```
custom_components/proxmox_datacenter_manager/
├── __init__.py        # Entry point: service registration, setup/teardown
├── api.py             # ProxmoxDatacenterManagerAPI client (aiohttp-based)
├── coordinator.py     # PDMCoordinator: data polling, migration orchestration
├── config_flow.py     # HA config UI: setup, options, reauth flows
├── sensor.py          # Sensor entities (migration state, counts, node/VM metrics)
├── const.py           # All constants, defaults, attribute names
├── services.yaml      # Service definitions (HA action UI)
├── strings.json       # Localization strings (config flow, services, entities)
└── manifest.json      # Integration metadata (version, dependencies)
```

### Key Concepts

- **Remote**: A Proxmox VE cluster registered in PDM. Each remote has its own nodes and VMs.
- **Node/Host**: A physical Proxmox VE server within a remote.
- **VM**: A QEMU virtual machine or LXC container running on a node.
- **PDM**: Proxmox Datacenter Manager - the central management layer that this integration talks to.

### Data Flow

1. `api.py` talks to the PDM REST API at `https://{host}:{port}/api2/json/...`
2. `coordinator.py` polls every 30 seconds, caching VMs, nodes, remotes
3. `sensor.py` creates HA entities from coordinator data
4. `__init__.py` registers HA services (actions) that call the API through the coordinator

### PDM API Structure

The PDM API base URL is `https://{host}:8443/api2/json`. Key endpoint patterns:

| Pattern | Methods | Purpose |
|---------|---------|---------|
| `/resources/list` | GET | All resources across all remotes (VMs, nodes, storage) |
| `/pve/remotes/{remote}/qemu/{vmid}/start` | POST | Start a QEMU VM |
| `/pve/remotes/{remote}/qemu/{vmid}/stop` | POST | Force stop a QEMU VM |
| `/pve/remotes/{remote}/qemu/{vmid}/shutdown` | POST | Graceful VM shutdown |
| `/pve/remotes/{remote}/qemu/{vmid}/migrate` | POST | Local (same-cluster) migration |
| `/pve/remotes/{remote}/qemu/{vmid}/remote-migrate` | POST | Cross-cluster migration |
| `/pve/remotes/{remote}/nodes/{node}/network` | GET | Node network config (for IP lookup) |
| `/pve/remotes/{remote}/nodes/{node}/status` | GET | Node status/metrics (GET only) |
| `/pve/remotes/{remote}/tasks/{upid}/status` | GET | Task status tracking |
| `/nodes/{node}/status` | POST | Shutdown/reboot the PDM host node (`command=shutdown`) |
| `/version` | GET | PDM version info |

**Important**: Resource types from the API use `pve-` prefixes (`pve-qemu`, `pve-lxc`, `pve-node`) but API endpoint paths use unprefixed types (`qemu`, `lxc`). The `_strip_pve_prefix()` helper in `api.py` handles this.

**Important**: `/pve/remotes/{remote}/nodes/{node}/status` only supports GET. Node shutdown uses `/nodes/{node}/status` with POST.

### Authentication

PDM uses API token auth: `Authorization: PDMAPIToken={token_id}:{token_secret}`

## Adding a New Service (Action)

When adding a new HA service, you need to update **5 files**:

1. **`const.py`** - Add service name constant (`SERVICE_*`) and any new attribute constants (`ATTR_*`)
2. **`api.py`** - Add the API method if it needs a new PDM endpoint
3. **`__init__.py`** - Add:
   - Schema (e.g., `SERVICE_*_SCHEMA`)
   - Handler function (`handle_*`)
   - Registration call (`hass.services.async_register(...)`)
   - Import any new constants
   - Add service name to `_async_unregister_services` list
4. **`services.yaml`** - Add service definition with fields, descriptions, selectors
5. **`strings.json`** - Add service entry under `"services"` with name, description, and field strings

### Service Patterns

- **VM-scoped services** use `SERVICE_VM_NAME_SCHEMA` (requires `vm_name`)
- **Host-scoped services** use `SERVICE_HOST_SCHEMA` (requires `host_name`, optional `remote_name`)
- **Remote-scoped services** use `SERVICE_REMOTE_SCHEMA` (requires `remote_name`)
- All services get the coordinator via `get_coordinator()` helper
- All services return `{"success": True/False, ...}` response dicts
- Use `SupportsResponse.OPTIONAL` for services that return data
- Bulk operations iterate items and collect per-item results, continuing on individual failures

### Existing Schemas

```python
SERVICE_VM_NAME_SCHEMA     # {vm_name: str}
SERVICE_HOST_SCHEMA        # {host_name: str, remote_name?: str}
SERVICE_REMOTE_SCHEMA      # {remote_name: str}
SERVICE_MIGRATE_VM_SCHEMA  # {vm_name, target_host, target_remote?, online?, ...}
```

## Common Patterns

### VM Lookup
VMs can be found by VMID (numeric) or name (case-insensitive, with partial match fallback). See `coordinator.find_vm_by_name()`.

### Node Disambiguation
When a host name exists in multiple remotes, the user must specify `remote_name`. Use `api.find_all_nodes_by_name()` to check for ambiguity.

### Error Handling
- API errors → `ProxmoxDatacenterManagerError` (with subtypes `AuthenticationError`, `ConnectionError`, `APIError`)
- Service handlers catch these and return `{"success": False, "error": str(err)}`
- Bulk operations continue past individual failures, collecting results

## PDM API Reference

- [PDM API Viewer](https://pdm.proxmox.com/docs/api-viewer/index.html) (interactive, JS-based)
- [PDM Documentation](https://pdm.proxmox.com/docs/)
- [PDM Command Syntax](https://pdm.proxmox.com/docs/command-syntax.html)
