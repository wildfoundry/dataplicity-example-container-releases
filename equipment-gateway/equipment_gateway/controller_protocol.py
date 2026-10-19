"""Laundry controller protocols for the equipment-gateway container.

The supervisor starts this container with CONTROLLER_PROTOCOL set to the
customer's install choice. The agent does not interpret that value. A vend
command cannot override it.
"""

from __future__ import annotations

import os
import time

LOCAL_SIMULATOR = "local-simulator"
MODBUS_RELAY_PULSE = "modbus-relay-pulse"
HOST_TIMED_COIN_PULSE = "host-timed-coin-pulse"

_BACKEND = {
    MODBUS_RELAY_PULSE: "modbus_waveshare_flash",
    HOST_TIMED_COIN_PULSE: "gpio",
}


def configured_protocol(gateway) -> str:
    """Protocol from container config. Env wins over the site config file."""
    env = os.environ.get("CONTROLLER_PROTOCOL", "").strip()
    if env:
        return env
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
