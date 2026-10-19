"""Laundry controller protocols for the equipment-gateway container.

The protocol is a device-class parameter. The customer sets its value for each
controller in the portal. The agent writes desired parameters to
``$DP_CONFIG_DIR/parameters.json`` and mounts it read-only into this container.
The platform does not interpret the value; this module does. A vend command
cannot override it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

PARAMETER_KEY = "controller_protocol"
DEFAULT_CONFIG_DIR = "/etc/dataplicity/config"

LOCAL_SIMULATOR = "local-simulator"
MODBUS_RELAY_PULSE = "modbus-relay-pulse"
HOST_TIMED_COIN_PULSE = "host-timed-coin-pulse"

_BACKEND = {
    MODBUS_RELAY_PULSE: "modbus_waveshare_flash",
    HOST_TIMED_COIN_PULSE: "gpio",
}


def _parameter_value() -> str:
    root = Path(os.environ.get("DP_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    try:
        data = json.loads((root / "parameters.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    entry = (data.get("settings") or {}).get(PARAMETER_KEY) if isinstance(data, dict) else None
    value = entry.get("value") if isinstance(entry, dict) else None
    return value.strip() if isinstance(value, str) else ""


def configured_protocol(gateway) -> str:
    """Device-class parameter first, then the site config file."""
    value = _parameter_value()
    if value:
        return value
    config = getattr(gateway, "config", None) or {}
    return str(config.get("controller_protocol") or "").strip()


def select_adapter(gateway, equipment_id: str):
    """Return (adapter, refusal, publish_simulated_cycle)."""
    protocol = configured_protocol(gateway)
    configured = gateway.adapters[equipment_id]
    if not protocol:
        return configured, "", False
    if protocol == LOCAL_SIMULATOR:
        adapter = gateway._simulators.get(equipment_id)
        if adapter is None:
            from .integrations.laundry import LocalSimulator

            adapter = LocalSimulator()
            gateway._simulators[equipment_id] = adapter
        return adapter, "", True
    expected = _BACKEND.get(protocol)
    if expected is None:
        return None, f"Controller protocol {protocol!r} is not implemented", False
    if getattr(configured, "backend", None) != expected:
        if protocol == MODBUS_RELAY_PULSE:
            return None, "Modbus relay pulse is not configured for this equipment", False
        return None, "Host-timed coin pulse line is not configured for this equipment", False
    return configured, "", False


def publish_simulated_cycle(gateway, equipment_id: str) -> None:
    adapter = gateway._simulators.get(equipment_id)
    if adapter is None or not hasattr(adapter, "cycle_snapshots"):
        return
    now = time.time()
    for snapshot in adapter.cycle_snapshots(equipment_id, observed_at=now, now=now):
        gateway.agent.event(
            "equipment.state",
            snapshot,
            instance_id=gateway.instance_id,
            durability="critical",
        )
