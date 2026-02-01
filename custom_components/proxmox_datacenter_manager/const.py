"""Constants for the Proxmox Datacenter Manager integration."""

DOMAIN = "proxmox_datacenter_manager"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_TOKEN_ID = "api_token_id"
CONF_API_TOKEN_SECRET = "api_token_secret"
CONF_VERIFY_SSL = "verify_ssl"

# Default values
DEFAULT_PORT = 8443
DEFAULT_VERIFY_SSL = True

# API endpoints
API_BASE = "/api2/json"
API_RESOURCES = "/resources/list"
API_ACCESS_TICKET = "/access/ticket"

# Resource types (PDM uses "pve-" prefix)
RESOURCE_TYPE_QEMU = "pve-qemu"
RESOURCE_TYPE_LXC = "pve-lxc"
RESOURCE_TYPE_NODE = "pve-node"
RESOURCE_TYPE_STORAGE = "pve-storage"

# Migration states
MIGRATION_STATE_IDLE = "idle"
MIGRATION_STATE_SEARCHING = "searching"
MIGRATION_STATE_MIGRATING = "migrating"
MIGRATION_STATE_COMPLETED = "completed"
MIGRATION_STATE_FAILED = "failed"

# Service names
SERVICE_MIGRATE_VM = "migrate_vm"

# Attributes
ATTR_VM_NAME = "vm_name"
ATTR_TARGET_HOST = "target_host"
ATTR_TARGET_REMOTE = "target_remote"
ATTR_ONLINE = "online"
ATTR_WITH_LOCAL_DISKS = "with_local_disks"

# Update intervals
SCAN_INTERVAL_SECONDS = 30
