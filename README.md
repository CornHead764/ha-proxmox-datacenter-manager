# Proxmox Datacenter Manager Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration for [Proxmox Datacenter Manager (PDM)](https://www.proxmox.com/en/products/proxmox-datacenter-manager) that enables live VM migration between clusters and provides VM management capabilities.

## Features

- **Live VM Migration**: Migrate VMs between nodes within a cluster or across different clusters
- **VM Power Control**: Start, stop, and shutdown VMs by name
- **Resource Monitoring**: Track VMs, nodes, and remotes across your infrastructure
- **Migration Status Sensor**: Real-time tracking of migration progress
- **Easy Setup**: Configure via Home Assistant's web UI with API token authentication

## Requirements

- Home Assistant 2024.1.0 or newer
- Proxmox Datacenter Manager instance
- API Token with appropriate permissions

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu in the top right corner
3. Select "Custom repositories"
4. Add this repository URL and select "Integration" as the category
5. Click "Add"
6. Search for "Proxmox Datacenter Manager" and install it
7. Restart Home Assistant

### Manual Installation

1. Download the latest release
2. Copy the `custom_components/proxmox_datacenter_manager` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Configuration

### Creating an API Token in PDM

1. Log into your PDM web interface
2. Navigate to Configuration → Access Control → API Tokens
3. Create a new token with the following permissions:
   - `Resource.Audit` - For viewing VMs and resources
   - `Resource.Migrate` - For migrating VMs
   - `Resource.Manage` - For power operations (start/stop/shutdown)
4. Save the Token ID (format: `user@realm!tokenname`) and Secret

### Adding to Home Assistant

1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "Proxmox Datacenter Manager"
4. Enter your PDM server details:
   - **Host**: Your PDM server hostname or IP
   - **Port**: 8443 (default)
   - **API Token ID**: `user@realm!tokenname`
   - **API Token Secret**: Your token secret
   - **Verify SSL**: Enable for production use

## Services

### `proxmox_datacenter_manager.migrate_vm`

Migrate a VM to a target host.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `vm_name` | Yes | Name of the VM to migrate |
| `target_host` | Yes | Target node name |
| `target_remote` | No | Target cluster (for cross-cluster migration) |
| `online` | No | Live migration (default: true) |
| `with_local_disks` | No | Include local disks (default: false) |

**Example:**
```yaml
service: proxmox_datacenter_manager.migrate_vm
data:
  vm_name: "my-vm"
  target_host: "pve-node2"
  online: true
```

**Cross-cluster migration:**
```yaml
service: proxmox_datacenter_manager.migrate_vm
data:
  vm_name: "my-vm"
  target_host: "pve-node1"
  target_remote: "datacenter2"
  online: true
```

### `proxmox_datacenter_manager.start_vm`

Start a VM by name.

```yaml
service: proxmox_datacenter_manager.start_vm
data:
  vm_name: "my-vm"
```

### `proxmox_datacenter_manager.stop_vm`

Force stop a VM by name.

```yaml
service: proxmox_datacenter_manager.stop_vm
data:
  vm_name: "my-vm"
```

### `proxmox_datacenter_manager.shutdown_vm`

Gracefully shutdown a VM by name.

```yaml
service: proxmox_datacenter_manager.shutdown_vm
data:
  vm_name: "my-vm"
```

### `proxmox_datacenter_manager.reset_migration_state`

Reset the migration state sensor to idle.

```yaml
service: proxmox_datacenter_manager.reset_migration_state
```

## Sensors

The integration creates the following sensors:

| Sensor | Description |
|--------|-------------|
| `sensor.pdm_*_migration_state` | Current migration state (idle/searching/migrating/completed/failed) |
| `sensor.pdm_*_total_vms` | Total number of VMs across all remotes |
| `sensor.pdm_*_total_nodes` | Total number of nodes across all remotes |
| `sensor.pdm_*_total_remotes` | Number of configured remotes |

### Migration State Sensor Attributes

When a migration is active, the sensor includes:
- `vm_name`: Name of the VM being migrated
- `vm_id`: VMID of the VM
- `source_remote`: Source cluster name
- `source_node`: Source node name
- `target_node`: Target node name
- `target_remote`: Target cluster (for cross-cluster migrations)
- `task_status`: Current task status
- `progress`: Migration progress percentage
- `error`: Error message (if failed)

## Automation Examples

### Migrate VM when node load is high

```yaml
automation:
  - alias: "Migrate VM on high load"
    trigger:
      - platform: numeric_state
        entity_id: sensor.pve_node1_cpu
        above: 90
        for:
          minutes: 5
    action:
      - service: proxmox_datacenter_manager.migrate_vm
        data:
          vm_name: "heavy-workload-vm"
          target_host: "pve-node2"
```

### Notify on migration completion

```yaml
automation:
  - alias: "Notify migration complete"
    trigger:
      - platform: state
        entity_id: sensor.pdm_myserver_migration_state
        to: "completed"
    action:
      - service: notify.mobile_app
        data:
          title: "VM Migration Complete"
          message: "{{ state_attr('sensor.pdm_myserver_migration_state', 'vm_name') }} has been migrated successfully"
```

## Troubleshooting

### Cannot connect to PDM

- Verify the host and port are correct
- Ensure the PDM server is accessible from your Home Assistant instance
- Check firewall rules allow traffic on port 8443

### Invalid authentication

- Verify the API Token ID format: `user@realm!tokenname`
- Ensure the token secret is correct
- Check the token has not expired

### Migration fails

- Verify the target node has sufficient resources
- Check network connectivity between nodes
- Ensure shared storage is accessible from both nodes (for shared storage migrations)
- For local disk migrations, enable "with_local_disks"

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [Proxmox](https://www.proxmox.com/) for creating PDM
- [Home Assistant](https://www.home-assistant.io/) community
