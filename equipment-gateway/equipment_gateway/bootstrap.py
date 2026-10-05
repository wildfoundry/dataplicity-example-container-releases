"""Materialize site config inside the Softwares container (no host sudo).

Site JSON ships in the OCI image under ``site_templates/``. On start the
container writes a working tree into ``EQUIPMENT_STATE_DIR`` (bind-mounted from
``/var/lib/dataplicity/equipment-gateway`` on dpdata). Serial device nodes are
discovered from already-granted ``--device`` mounts.

Operators do not SSH or ``sudo`` on the device. Site layout is versioned with
the Softwares release; only the durable journal stays as device state.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("equipment-gateway.bootstrap")

TEMPLATES_ROOT = Path(__file__).resolve().parent / "site_templates"
HOST_OVERRIDE = Path("/etc/equipment-gateway/config.json")
SERIAL_CANDIDATES = (
    "/dev/ttyUSB0",
    "/dev/ttyUSB1",
    "/dev/ttyACM0",
    "/dev/ttyAMA0",
)


def discover_serial_device(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        matches = sorted(p for p in by_id.iterdir() if p.is_symlink() or p.exists())
        if matches:
            return str(matches[0])
    for candidate in SERIAL_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def select_site(*, site: str, serial_device: Optional[str]) -> str:
    choice = (site or "auto").strip().lower()
    if choice in {"waveshare", "gpio"}:
        return choice
    if choice != "auto":
        raise ValueError(f"EQUIPMENT_SITE must be auto, waveshare, or gpio (got {site!r})")
    if serial_device and Path(serial_device).exists():
        return "waveshare"
    if any(Path(candidate).exists() for candidate in SERIAL_CANDIDATES):
        return "waveshare"
    if Path("/dev/gpiochip0").exists():
        return "gpio"
    # Board not plugged yet — still stage Waveshare so plug-in is config-ready.
    return "waveshare"


def _patch_waveshare_device(equipment_dir: Path, device: str) -> None:
    for path in sorted(equipment_dir.glob("*.json")):
        if path.name.startswith("."):
            continue
        payload = json.loads(path.read_text())
        credit = payload.setdefault("options", {}).setdefault("credit_pulse", {})
        transport = credit.get("transport")
        if not isinstance(transport, dict):
            continue
        if credit.get("backend") not in {None, "modbus_waveshare_flash"}:
            continue
        transport["device"] = device
        path.write_text(json.dumps(payload, indent=2) + "\n")


def materialize_site(
    *,
    site: str,
    state_dir: Path,
    product_instance_id: Optional[str] = None,
    serial_device: Optional[str] = None,
) -> Path:
    template = TEMPLATES_ROOT / site
    if not (template / "config.json").is_file():
        raise FileNotFoundError(f"Bundled site template missing: {template}")
    state_dir.mkdir(parents=True, exist_ok=True)
    equipment_dest = state_dir / "equipment"
    if equipment_dest.exists():
        shutil.rmtree(equipment_dest)
    shutil.copytree(template / "equipment", equipment_dest)
    config = json.loads((template / "config.json").read_text())
    config["equipment_dir"] = "equipment"
    if product_instance_id:
        config["product_instance_id"] = product_instance_id
    config_path = state_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    if site == "waveshare":
        device = serial_device or "/dev/ttyUSB0"
        _patch_waveshare_device(equipment_dest, device)
        LOG.info(
            "bootstrap_site site=waveshare state_dir=%s modbus_device=%s product_instance_id=%s",
            state_dir,
            device,
            config.get("product_instance_id"),
        )
    else:
        LOG.info(
            "bootstrap_site site=gpio state_dir=%s product_instance_id=%s",
            state_dir,
            config.get("product_instance_id"),
        )
    return config_path


def resolve_config_path() -> Path:
    """Return the config path the gateway should load.

    Managed Softwares (default): rematerialize OCI ``site_templates`` into
    ``EQUIPMENT_STATE_DIR`` every start.

    Lab break-glass: set ``EQUIPMENT_BOOTSTRAP=0`` and supply
    ``EQUIPMENT_CONFIG`` or a host file at ``/etc/equipment-gateway/config.json``.
    """
    mode = str(os.environ.get("EQUIPMENT_BOOTSTRAP", "1")).strip().lower()
    if mode in {"0", "false", "no", "off"}:
        for candidate in (
            os.environ.get("EQUIPMENT_CONFIG"),
            str(HOST_OVERRIDE),
        ):
            if candidate and Path(candidate).is_file():
                LOG.info("bootstrap_disabled using_existing_config path=%s", candidate)
                return Path(candidate)
        return Path(os.environ.get("EQUIPMENT_CONFIG", "/var/lib/equipment-gateway/config.json"))

    state_dir = Path(os.environ.get("EQUIPMENT_STATE_DIR", "/var/lib/equipment-gateway"))
    serial = discover_serial_device(os.environ.get("WAVESHARE_MODBUS_DEVICE"))
    site = select_site(site=os.environ.get("EQUIPMENT_SITE", "auto"), serial_device=serial)
    return materialize_site(
        site=site,
        state_dir=state_dir,
        product_instance_id=os.environ.get("PRODUCT_INSTANCE_ID"),
        serial_device=serial,
    )
