"""Validate and probe Modbus credit-pulse site wiring (Waveshare and classic)."""
from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger("equipment-gateway.modbus-site")

_MODBUS_BACKENDS = frozenset({"modbus_waveshare_flash", "modbus"})


def _credit_pulse(entry: dict[str, Any]) -> dict[str, Any]:
    options = entry.get("options") or {}
    credit = options.get("credit_pulse")
    return credit if isinstance(credit, dict) else {}


def _resolved_backend(credit: dict[str, Any]) -> str | None:
    backend = credit.get("backend")
    if backend in _MODBUS_BACKENDS:
        return backend
    if backend == "gpio" or "chip" in credit or "line" in credit:
        return "gpio"
    if credit.get("kind") == "waveshare_flash" or ("relay" in credit and "transport" in credit):
        return "modbus_waveshare_flash"
    if "duration_register" in credit and "transport" in credit:
        return "modbus"
    return backend if isinstance(backend, str) else None


def validate_credit_pulse_buses(equipment: list[dict[str, Any]]) -> None:
    """Fail closed on inconsistent baud settings or duplicate Waveshare relays."""
    bus_settings: dict[str, tuple] = {}
    relays: set[tuple] = set()
    classic_timers: set[tuple] = set()
    for entry in equipment:
        credit = _credit_pulse(entry)
        backend = _resolved_backend(credit)
        if backend not in _MODBUS_BACKENDS:
            continue
        transport = credit.get("transport")
        if not isinstance(transport, dict):
            raise ValueError(
                f"{entry.get('equipment_id')}: Modbus credit_pulse requires transport"
            )
        device = transport.get("device")
        if not isinstance(device, str) or not device.startswith("/dev/"):
            raise ValueError(
                f"{entry.get('equipment_id')}: credit_pulse.transport.device must be /dev/*"
            )
        settings = (
            transport.get("baudrate"),
            transport.get("parity"),
            transport.get("stopbits"),
            transport.get("bytesize", 8),
        )
        if device in bus_settings and bus_settings[device] != settings:
            raise ValueError(
                f"Inconsistent Modbus serial settings on {device}: "
                f"{bus_settings[device]} vs {settings}"
            )
        bus_settings[device] = settings
        address = transport.get("address")
        if type(address) is not int or not 1 <= address <= 247:
            raise ValueError(
                f"{entry.get('equipment_id')}: Modbus address must be 1-247"
            )
        if backend == "modbus_waveshare_flash":
            relay = credit.get("relay")
            if type(relay) is not int or not 0 <= relay <= 31:
                raise ValueError(
                    f"{entry.get('equipment_id')}: Waveshare relay must be 0-31"
                )
            key = (device, address, relay)
            if key in relays:
                raise ValueError(
                    f"Duplicate Waveshare relay mapping on {device} "
                    f"address={address} relay={relay}"
                )
            relays.add(key)
        else:
            duration_register = credit.get("duration_register")
            trigger_coil = credit.get("trigger_coil")
            key = (device, address, duration_register, trigger_coil)
            if key in classic_timers:
                raise ValueError(
                    f"Duplicate Modbus timer mapping on {device} address={address}"
                )
            classic_timers.add(key)


def iter_modbus_credit_pulses(equipment: list[dict[str, Any]]):
    for entry in equipment:
        credit = _credit_pulse(entry)
        backend = _resolved_backend(credit)
        if backend not in _MODBUS_BACKENDS:
            continue
        yield entry["equipment_id"], backend, credit


def probe_modbus_credit_pulses(equipment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open each configured Modbus RTU port and confirm the bus answers.

    Does not actuate relays. Safe to run after plugging in the Waveshare board.
    """
    from .modbus import ModbusTransport, TransportError

    opened: dict[str, ModbusTransport] = {}
    results: list[dict[str, Any]] = []
    try:
        for equipment_id, backend, credit in iter_modbus_credit_pulses(equipment):
            transport = credit["transport"]
            device = transport["device"]
            address = transport["address"]
            row: dict[str, Any] = {
                "equipment_id": equipment_id,
                "backend": backend,
                "device": device,
                "address": address,
                "ok": False,
                "detail": "",
            }
            if backend == "modbus_waveshare_flash":
                row["relay"] = credit.get("relay")
            try:
                if device not in opened:
                    opened[device] = ModbusTransport(transport)
                bus = opened[device]
                # Waveshare / generic digital input read of coil/status 0 proves the
                # adapter is on the bus without changing relay state.
                state = bus.read_input(0, function=1, address=address)
                row["ok"] = True
                row["detail"] = f"coil0={'on' if state else 'off'}"
                LOG.info(
                    "modbus_probe_ok equipment_id=%s device=%s address=%s %s",
                    equipment_id,
                    device,
                    address,
                    row["detail"],
                )
            except (OSError, TransportError, ValueError) as exc:
                row["detail"] = str(exc)
                LOG.error(
                    "modbus_probe_failed equipment_id=%s device=%s address=%s error=%s",
                    equipment_id,
                    device,
                    address,
                    exc,
                )
            results.append(row)
    finally:
        for bus in opened.values():
            try:
                bus.close()
            except OSError:
                LOG.exception("modbus_probe_close_failed")
    return results
