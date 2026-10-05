"""Load and track laundry machine-family profiles (actuation families)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROFILE_SCHEMA = json.loads((_PACKAGE_DIR / "contracts/laundry-machine-profile.v1.schema.json").read_text())
_PROFILE_VALIDATOR = Draft202012Validator(_PROFILE_SCHEMA)
_BUNDLED_PROFILES_DIR = _PACKAGE_DIR / "laundry_profiles"
_EXTRA_PROFILE_DIRS: list[Path] = []


def bundled_profiles_dir() -> Path:
    return _BUNDLED_PROFILES_DIR


def set_extra_profile_dirs(directories: Iterable[Path | str] | None) -> None:
    """Attach site-local profile directories for the current process (from config)."""
    global _EXTRA_PROFILE_DIRS
    _EXTRA_PROFILE_DIRS = [Path(item) for item in (directories or [])]


def profile_search_dirs(extra: Iterable[Path | str] | None = None) -> list[Path]:
    dirs: list[Path] = [_BUNDLED_PROFILES_DIR]
    env = os.environ.get("LAUNDRY_PROFILES_DIR", "").strip()
    if env:
        dirs.append(Path(env))
    for item in list(_EXTRA_PROFILE_DIRS) + list(extra or []):
        path = Path(item)
        if path not in dirs:
            dirs.append(path)
    return dirs


def _load_profile_file(path: Path) -> dict[str, Any]:
    profile = json.loads(path.read_text())
    _PROFILE_VALIDATOR.validate(profile)
    json.dumps(profile, allow_nan=False)
    return profile


def load_machine_profiles(*, extra_dirs: Iterable[Path | str] | None = None) -> dict[str, dict[str, Any]]:
    """Return machine_profile name -> profile, including alias keys."""
    by_name: dict[str, dict[str, Any]] = {}
    sources: dict[str, Path] = {}
    for directory in profile_search_dirs(extra_dirs):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name.startswith("._") or path.name.startswith("."):
                continue
            profile = _load_profile_file(path)
            names = [profile["machine_profile"], *list(profile.get("aliases") or [])]
            for name in names:
                existing = sources.get(name)
                if existing is not None and existing != path:
                    raise ValueError(f"Duplicate laundry machine_profile {name!r} from {existing} and {path}")
                by_name[name] = profile
                sources[name] = path
            profile["_source_path"] = str(path)
    return by_name


def get_machine_profile(name: str, *, extra_dirs: Iterable[Path | str] | None = None) -> dict[str, Any]:
    profiles = load_machine_profiles(extra_dirs=extra_dirs)
    if name not in profiles:
        known = ", ".join(sorted({p["machine_profile"] for p in profiles.values()})) or "(none)"
        raise ValueError(f"Unknown laundry machine_profile {name!r}; known: {known}")
    return dict(profiles[name])


def list_machine_profiles(*, extra_dirs: Iterable[Path | str] | None = None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for profile in load_machine_profiles(extra_dirs=extra_dirs).values():
        key = profile["machine_profile"]
        if key in seen:
            continue
        seen.add(key)
        duration = float(profile["default_duration_seconds"])
        gap = float(profile.get("default_gap_seconds") or 0.0)
        rows.append({
            "machine_profile": key,
            "manufacturer": profile["manufacturer"],
            "model": profile["model"],
            "activation": profile["activation"],
            "status": profile["status"],
            "duration_ms": int(round(duration * 1000)),
            "gap_ms": int(round(gap * 1000)),
            "default_duration_seconds": duration,
            "default_gap_seconds": gap,
            "default_pulses_per_vend": profile["default_pulses_per_vend"],
            "default_active_high": profile.get("default_active_high", True),
            "brands": list(profile.get("brands") or []),
            "aliases": list(profile.get("aliases") or []),
            "source": profile.get("_source_path", ""),
            "notes": profile.get("notes", ""),
        })
    rows.sort(key=lambda row: row["machine_profile"].lower())
    return rows
