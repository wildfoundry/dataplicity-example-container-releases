"""Equipment-neutral adapter interface and explicit local plugin loading."""
from dataclasses import dataclass, field
import importlib
import json


@dataclass(frozen=True)
class Outcome:
    certainty: str
    reason: str = ""
    result: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.certainty not in {"accepted", "definitely_rejected", "unknown"}:
            raise ValueError("Invalid physical operation certainty")
        if not isinstance(self.result, dict) or len(json.dumps(self.result, allow_nan=False).encode()) > 65536:
            raise ValueError("Adapter result must be a bounded JSON object")


def load_adapter(config, resources=None):
    path = config.get("adapter", "")
    module, separator, name = path.partition(":")
    # Configuration selects installed integrations, never downloads executable code.
    if not separator or not module.startswith("equipment_gateway.integrations.") or not name.isidentifier():
        raise ValueError("Adapter must name an installed equipment_gateway.integrations plugin")
    factory = getattr(importlib.import_module(module), name)
    adapter = factory(**config.get("options", {}))
    for method in ("validate", "execute", "state"):
        if not callable(getattr(adapter, method, None)):
            raise ValueError(f"Adapter lacks {method}")
    if not isinstance(adapter.capabilities, dict):
        raise ValueError("Adapter must declare capabilities")
    if resources is not None and callable(getattr(adapter, "bind_resources", None)):
        adapter.bind_resources(resources)
    return adapter
