"""Site equipment inventory helpers: per-machine files and readable summaries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EQUIPMENT_ENTRY_KEYS = frozenset({"equipment_id", "adapter", "options", "label", "bay", "notes"})


def _validate_equipment_entry(entry: dict[str, Any], *, source: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"Equipment entry from {source} must be an object")
    unknown = set(entry) - EQUIPMENT_ENTRY_KEYS
    if unknown:
        raise ValueError(f"Unsupported equipment keys in {source}: {sorted(unknown)}")
    equipment_id = entry.get("equipment_id")
    adapter = entry.get("adapter")
    options = entry.get("options")
    if not isinstance(equipment_id, str) or not 0 < len(equipment_id) <= 128:
        raise ValueError(f"equipment_id missing/invalid in {source}")
    if not isinstance(adapter, str) or not adapter:
        raise ValueError(f"adapter missing/invalid in {source}")
    if not isinstance(options, dict):
        raise ValueError(f"options must be an object in {source}")
    for optional in ("label", "bay", "notes"):
        value = entry.get(optional)
        if value is not None and (not isinstance(value, str) or len(value) > 256):
            raise ValueError(f"{optional} must be a short string in {source}")
    return {
        "equipment_id": equipment_id,
        "adapter": adapter,
        "options": options,
        **({key: entry[key] for key in ("label", "bay", "notes") if key in entry and entry[key] is not None}),
        "_source_path": source,
    }


def load_equipment_dir(directory: Path | str) -> list[dict[str, Any]]:
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"equipment_dir does not exist: {root}")
    entries: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("._") or path.name.startswith("."):
            continue
        payload = json.loads(path.read_text())
        if isinstance(payload, list):
            for index, item in enumerate(payload):
                entries.append(_validate_equipment_entry(item, source=f"{path}[{index}]"))
        else:
            entries.append(_validate_equipment_entry(payload, source=str(path)))
    return entries


def merge_equipment(
    inline: list[dict[str, Any]] | None,
    *,
    equipment_dir: Path | str | None = None,
    config_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for index, entry in enumerate(inline or []):
        merged.append(_validate_equipment_entry(entry, source=f"config.equipment[{index}]"))
    if equipment_dir:
        directory = Path(equipment_dir)
        if not directory.is_absolute() and config_path is not None:
            directory = Path(config_path).resolve().parent / directory
        merged.extend(load_equipment_dir(directory))
    identifiers = [entry["equipment_id"] for entry in merged]
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    if duplicates:
        raise ValueError(f"Duplicate equipment_id values: {duplicates}")
    return merged


def _credit_backend(credit: dict[str, Any]) -> str:
    backend = credit.get("backend")
    if isinstance(backend, str) and backend:
        return backend
    if "chip" in credit or "line" in credit:
        return "gpio"
    if credit.get("kind") == "waveshare_flash" or "relay" in credit:
        return "modbus_waveshare_flash"
    if "duration_register" in credit:
        return "modbus"
    return ""


def equipment_summary(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for entry in entries:
        options = entry.get("options") or {}
        credit = options.get("credit_pulse") or {}
        transport = credit.get("transport") if isinstance(credit.get("transport"), dict) else {}
        backend = _credit_backend(credit)
        rows.append({
            "equipment_id": entry["equipment_id"],
            "label": entry.get("label") or "",
            "bay": entry.get("bay") or "",
            "adapter": entry.get("adapter", "").rsplit(":", 1)[-1],
            "machine_profile": options.get("machine_profile") or options.get("mode") or "",
            "backend": backend,
            "device": transport.get("device") or credit.get("chip") or "",
            "address": transport.get("address") if "address" in transport else "",
            "relay": credit.get("relay") if "relay" in credit else "",
            "chip": credit.get("chip") or "",
            "line": credit.get("line") if "line" in credit else "",
            "source": entry.get("_source_path") or "",
        })
    return rows


def format_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(none)"
    widths = {column: len(column) for column in columns}
    rendered = []
    for row in rows:
        values = {column: "" if row.get(column) in (None, "") else str(row.get(column)) for column in columns}
        rendered.append(values)
        for column in columns:
            widths[column] = max(widths[column], len(values[column]))
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    separator = "  ".join("-" * widths[column] for column in columns)
    body = ["  ".join(values[column].ljust(widths[column]) for column in columns) for values in rendered]
    return "\n".join([header, separator, *body])
