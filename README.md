# Proxmox Datacenter Manager Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration for [Proxmox Datacenter Manager (PDM)](https://www.proxmox.com/en/products/proxmox-datacenter-manager) that enables live VM migration between clusters and provides VM management capabilities.

## Features

- **Live VM Migration**: Migrate VMs between nodes within a cluster or across different clusters
  - Auto-detects target cluster from node name (just specify `target_host`)
  - Auto-detects node IP for cross-cluster migrations
  - Validates for duplicate node names across remotes
  - Prevents migration to same node VM is already on
- **VM Power Control**: Start, stop, and shutdown VMs by name
- **Bulk Shutdown Actions**: Shutdown all VMs on a host or remote, shutdown individual hosts, or shutdown all hosts in a remote (gracefully skips offline nodes)
- **Resource Monitoring**: Track VMs, nodes, and remotes across your infrastructure
  - CPU and memory sensors for nodes
  - CPU and memory sensors for VMs (with optional regex filter)
- **Migration Status Sensor**: Real-time tracking of migration progress
- **Configurable Options**: Filter which VM sensors are created using regex patterns
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
2. Navigate to **Configuration → Access Control → API Tokens**
3. Create a new token for a user (e.g., `root@pam!homeassistant`)
4. Save the Token ID and Secret value securely

### Required API Permissions

PDM uses role-based access control. Here are the privileges needed for each feature:

| Feature | Required Privilege | Path |
|---------|-------------------|------|
| **View resources/VMs** | `Resource.Audit` | `/` or `/resource/{remote}` |
| **Start/Stop/Shutdown VMs** | `Resource.Manage` | `/resource/{remote}/guest/{vmid}` |
| **Migrate VMs (local)** | `Resource.Migrate` | `/resource/{remote}/guest/{vmid}` |
| **Migrate VMs (cross-cluster)** | `Resource.Migrate` | Both source and target paths |
| **Shutdown hosts** | `Sys.PowerMgmt` | `/nodes/{node}` |
| **View system info** | `System.Audit` | `/` |

#### Recommended Setup: Administrator Role

For full functionality, assign the **Administrator** role at the root path `/`:

```
Path: /
User: your-user@pam
Role: Administrator
Propagate: Yes
```

This grants all privileges and propagates them to all child paths.

#### Minimal Permissions Setup

If you prefer minimal permissions:

1. **For read-only monitoring:**
   - Role: `Auditor` on path `/`

2. **For VM power control:**
   - Privileges: `Resource.Audit`, `Resource.Manage` on path `/`

3. **For VM migration:**
   - Privileges: `Resource.Audit`, `Resource.Manage`, `Resource.Migrate` on path `/`

### PDM Privilege Reference

| Privilege | Description |
|-----------|-------------|
| `System.Audit` | View system status and configuration |
| `System.Modify` | Modify system-level configuration |
| `Resource.Audit` | View guests, storage, and other resources |
| `Resource.Manage` | Start, stop, shutdown guests |
| `Resource.Modify` | Change guest configuration |
| `Resource.Migrate` | Migrate guests between nodes/clusters |
| `Resource.Create` | Create new guests |
| `Resource.Delete` | Delete guests |
| `Access.Audit` | View permissions and users |
| `Access.Modify` | Modify permissions and users |

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

Migrate a VM to a target host. The integration automatically detects whether this is a local (same cluster) or remote (cross-cluster) migration based on where the target node is located.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `vm_name` | Yes | Name or VMID of the VM to migrate |
| `target_host` | Yes | Target node name |
| `target_remote` | No | Target cluster (only needed if node name exists in multiple remotes) |
| `online` | No | Live migration (default: true) |
| `with_local_disks` | No | Include local disks for local migrations (default: false) |

**Simple migration (auto-detects everything):**
```yaml
service: proxmox_datacenter_manager.migrate_vm
data:
  vm_name: "my-vm"
  target_host: "pve-node2"
```

The integration will:
1. Find the VM across all remotes
2. Find which remote `pve-node2` belongs to
3. Automatically determine if it's a local or cross-cluster migration
4. For cross-cluster migrations, auto-detect the target node's IP address

**Explicit cross-cluster migration** (required if node names are duplicated across remotes):
```yaml
service: proxmox_datacenter_manager.migrate_vm
data:
  vm_name: "my-vm"
  target_host: "pve-node1"
  target_remote: "datacenter2"
```

**Validation:**
- If `target_host` exists in multiple remotes, you must specify `target_remote` to disambiguate
- Migration to the same node the VM is already on will fail with a helpful error message

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

### `proxmox_datacenter_manager.shutdown_host_vms`

Gracefully shutdown all running VMs on a specific host.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `host_name` | Yes | Name of the Proxmox host/node |
| `remote_name` | No | Remote/cluster name (only needed if host name exists in multiple remotes) |

```yaml
service: proxmox_datacenter_manager.shutdown_host_vms
data:
  host_name: "pve-node1"
```

Returns a detailed response with per-VM results:
```yaml
success: true
host: "pve-node1"
remote: "my-cluster"
total: 3
succeeded: 3
failed: 0
vms_shutdown:
  - vm_name: "web-server"
    vm_id: 100
    success: true
    upid: "UPID:..."
```

### `proxmox_datacenter_manager.shutdown_remote_vms`

Gracefully shutdown all running VMs across an entire remote/cluster.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `remote_name` | Yes | Remote/cluster name |

```yaml
service: proxmox_datacenter_manager.shutdown_remote_vms
data:
  remote_name: "my-cluster"
```

Returns per-VM results including which node each VM was on.

### `proxmox_datacenter_manager.shutdown_host`

Shutdown a Proxmox host/node.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `host_name` | Yes | Name of the Proxmox host/node |
| `remote_name` | No | Remote/cluster name (only needed if host name exists in multiple remotes) |

```yaml
service: proxmox_datacenter_manager.shutdown_host
data:
  host_name: "pve-node1"
```

### `proxmox_datacenter_manager.shutdown_all_hosts`

Shutdown all hosts in a remote/cluster. Gracefully skips hosts that are already offline -- for example, a 3-node cluster with 1 node already off will only attempt to shutdown the 2 that are on.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `remote_name` | Yes | Remote/cluster name |

```yaml
service: proxmox_datacenter_manager.shutdown_all_hosts
data:
  remote_name: "my-cluster"
```

Returns a detailed response showing which hosts were shut down, skipped, or failed:
```yaml
success: true
remote: "my-cluster"
total: 3
shutdown: 2
skipped: 1
failed: 0
hosts_shutdown:
  - host: "pve-node1"
    success: true
    skipped: false
    upid: "UPID:..."
  - host: "pve-node2"
    success: true
    skipped: false
    upid: "UPID:..."
  - host: "pve-node3"
    success: true
    skipped: true
    reason: "Already offline"
```

### `proxmox_datacenter_manager.reset_migration_state`

Reset the migration state sensor to idle.

```yaml
service: proxmox_datacenter_manager.reset_migration_state
```

### `proxmox_datacenter_manager.list_vms`

List all discovered VMs and containers. Useful for debugging and verifying the integration can see your VMs.

```yaml
service: proxmox_datacenter_manager.list_vms
response_variable: vms
```

Returns:
```yaml
success: true
count: 5
vms:
  - name: "my-vm"
    vmid: 100
    node: "pve-node1"
    remote: "datacenter1"
    type: "qemu"
    status: "running"
```

### `proxmox_datacenter_manager.debug_api`

Inspect raw API responses from PDM. Use this to troubleshoot connection or data issues.

```yaml
service: proxmox_datacenter_manager.debug_api
response_variable: debug_info
```

## Sensors

The integration creates the following sensors:

### Summary Sensors

| Sensor | Description |
|--------|-------------|
| `sensor.pdm_*_migration_state` | Current migration state (idle/searching/migrating/completed/failed) |
| `sensor.pdm_*_total_vms` | Total number of VMs across all remotes |
| `sensor.pdm_*_total_nodes` | Total number of nodes across all remotes |
| `sensor.pdm_*_total_remotes` | Number of configured remotes |

### Node Sensors

For each node in your infrastructure:

| Sensor | Description |
|--------|-------------|
| `sensor.pdm_*_node_*_cpu` | Node CPU usage percentage |
| `sensor.pdm_*_node_*_memory` | Node memory usage percentage |

### VM Sensors (Optional)

For each VM matching your filter pattern:

| Sensor | Description |
|--------|-------------|
| `sensor.pdm_*_vm_*_cpu` | VM CPU usage percentage |
| `sensor.pdm_*_vm_*_memory` | VM memory usage percentage |

VM sensors are controlled by the **VM Filter Regex** option (see Options below).

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

## Options

After initial setup, you can configure additional options by clicking "Configure" on the integration:

| Option | Description |
|--------|-------------|
| **Enable Node Sensors** | Create CPU/memory sensors for each node (default: enabled) |
| **Enable VM Sensors** | Create CPU/memory sensors for VMs (default: disabled) |
| **VM Filter Regex** | Only create sensors for VMs matching this pattern (e.g., `^prod-.*` for VMs starting with "prod-") |

To access options:
1. Go to Settings → Devices & Services
2. Find "Proxmox Datacenter Manager"
3. Click "Configure"

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

### Graceful cluster shutdown

Shutdown all VMs first, then all hosts in a cluster:

```yaml
automation:
  - alias: "Graceful cluster shutdown"
    trigger:
      - platform: event
        event_type: call_service
        event_data:
          domain: input_button
          service: press
    action:
      - service: proxmox_datacenter_manager.shutdown_remote_vms
        data:
          remote_name: "my-cluster"
      - delay:
          minutes: 5
      - service: proxmox_datacenter_manager.shutdown_all_hosts
        data:
          remote_name: "my-cluster"
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

### Sensors show 0 for VMs/Nodes/Remotes

This usually indicates the API is not returning data correctly. Debug steps:

1. **Check API permissions**: Ensure your API token has `Resource.Audit` privilege on path `/`

2. **Use the debug service** to inspect raw API responses:
   ```yaml
   service: proxmox_datacenter_manager.debug_api
   response_variable: debug_info
   ```
   Check the response in Developer Tools → Services

3. **Check Home Assistant logs** for debug output:
   ```yaml
   logger:
     logs:
       custom_components.proxmox_datacenter_manager: debug
   ```

4. **Verify remotes are configured** in PDM:
   - Log into PDM web interface
   - Check that PVE remotes are added under Configuration → Remotes
   - Ensure remotes are connected and synced

### VM not found error

When `migrate_vm` or other services return "VM not found":

1. **Use `list_vms` service** to see what VMs the integration can discover:
   ```yaml
   service: proxmox_datacenter_manager.list_vms
   response_variable: vms
   ```

2. **Check VM name vs VMID**: You can search by either:
   - VM name: `"my-vm"`
   - VMID: `"100"`

3. **Verify the VM exists** in PDM's web interface under Resources

4. **Check resource caching**: PDM caches resource data. If a VM was just created, wait 30 seconds or restart the integration

### Cannot connect to PDM

- Verify the host and port are correct (default port: 8443)
- Ensure the PDM server is accessible from your Home Assistant instance
- Check firewall rules allow traffic on port 8443
- For SSL issues, try disabling "Verify SSL" temporarily to test

### Invalid authentication

- Verify the API Token ID format: `user@realm!tokenname`
  - Example: `root@pam!homeassistant`
- Ensure the token secret is the full secret value (usually a long UUID-like string)
- Check the token has not expired
- Verify the token's user has appropriate permissions

### Migration fails

- **"VM is already on node X"**: The VM is already on the target node. No migration needed.

- **"Ambiguous target_host - found in multiple remotes"**: The target node name exists in more than one remote. Specify `target_remote` to indicate which cluster you want to migrate to.

- **"refusing migration to the same node"**: The PDM API rejected the migration because source and target are the same.

- Verify the target node has sufficient resources (CPU, RAM, storage)
- Check network connectivity between nodes
- Ensure shared storage is accessible from both nodes (for shared storage migrations)
- For local disk migrations, enable `with_local_disks: true`
- Check PDM logs for detailed error messages: `journalctl -u proxmox-datacenter-manager`

### Enabling Debug Logging

Add this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.proxmox_datacenter_manager: debug
```

This will log all API requests and responses to help diagnose issues.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [Proxmox](https://www.proxmox.com/) for creating PDM
- [Home Assistant](https://www.home-assistant.io/) community
