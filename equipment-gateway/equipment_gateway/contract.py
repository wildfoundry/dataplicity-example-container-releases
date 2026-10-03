"""Equipment-neutral semantic contract helpers; adapters supply their vocabulary."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator

def capabilities(contract, *, properties=(), actions=(), provenance="simulated", version="1.0.0"):
    report = {"contract": contract["contract"], "version": contract["version"],
              "failure_certainty": "acknowledged_reject_and_unknown", "reconciliation": "unsupported"}
    for section, supported in (("properties", properties), ("actions", actions)):
        if set(supported) - contract[section].keys():
            raise ValueError("Adapter vocabulary is outside the supplied semantic contract")
        report[section] = {}
        for key in contract[section]:
            item = {"support": "unsupported"}
            if key in supported:
                item = {"support": "supported", "provenance": provenance,
                        "capability_version": version}
                if section == "properties":
                    item["max_age_seconds"] = 30
            report[section][key] = item
    return report


def state_envelope(contract, report, values, *, observed_at, now):
    """Unsupported, unknown, missing, stale and invalid are distinguishable."""
    result = {"contract": contract["contract"], "version": contract["version"], "properties": {}}
    for key, capability in report["properties"].items():
        item = deepcopy(capability)
        support = item["support"]
        item["quality"] = support if support != "supported" else "missing"
        if support == "supported" and key in values:
            value = values[key]
            errors = list(Draft202012Validator(contract["properties"][key]["value_schema"]).iter_errors(value))
            try:
                json.dumps(value, allow_nan=False)
            except ValueError:
                errors.append("non-finite value")
            item.update(value=value, observed_at=observed_at)
            item["quality"] = "invalid" if errors else (
                "stale" if now - observed_at > item["max_age_seconds"] or observed_at > now else "good")
        result["properties"][key] = item
    return result
