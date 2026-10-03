"""Simulation is explicit. No inferred manufacturer protocols or wiring."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ..adapters import Outcome
from ..physical import SimulatedIO
from ..contract import capabilities as contract_capabilities, state_envelope

CONTRACT = json.loads((Path(__file__).parents[1] / "contracts/laundry-machine.v1.json").read_text())

def capabilities(**kwargs):
    return contract_capabilities(CONTRACT, **kwargs)

def validate_action(action, payload):
    if action not in CONTRACT["actions"]:
        raise ValueError("Unknown LaundryMachine action")
    Draft202012Validator(CONTRACT["actions"][action]["input_schema"]).validate(payload)


class Simulator:
    """Two capability subsets bind to the same contract; never a compatibility claim."""
    def __init__(self, *, mode="native", outcome="accepted", service_delivered=None):
        if mode not in {"native", "pulse"}:
            raise ValueError("Simulator mode must be native or pulse")
        self.mode = mode
        self.outcome = Outcome(outcome)
        if service_delivered is not None and type(service_delivered) is not bool:
            raise ValueError("Simulated delivery evidence must be boolean or absent")
        self.service_delivered = service_delivered
        self.calls = 0
        self.io = SimulatedIO()
        self.capabilities = capabilities(
            properties=("availability", "operating_state", "remaining_seconds", "door_locked")
            if mode == "native" else ("availability",),
            actions=("vend", "start", "add_time", "free_vend", "reset", "set_out_of_service", "set_price", "set_programme")
            if mode == "native" else ("vend",),
        )

    def validate(self, action, payload):
        validate_action(action, payload)
        if payload["machine_id"] != payload["equipment_id"]:
            raise ValueError("Semantic machine target differs from configured equipment")

    def state(self, *, observed_at, now):
        return state_envelope(CONTRACT, self.capabilities, self.observe(), observed_at=observed_at, now=now)

    def execute(self, action, payload):
        self.calls += 1
        if self.outcome.certainty == "accepted":
            # Fictional simulator mappings, never an OEM protocol/wiring claim.
            if self.mode == "pulse":
                self.io.execute("pulse_output", {"channel": "simulated-credit", "duration_seconds": 0.1})
            else:
                self.io.execute("serial_transfer", {"request_hex": ("simulated:" + action).encode().hex(), "response_bytes": 0})
        if self.outcome.certainty == "accepted" and self.service_delivered is not None:
            return Outcome("accepted", result={"service_delivered": self.service_delivered,
                "evidence": {"kind": "simulated", "action": action}})
        return self.outcome

    def observe(self):
        # Simulated process restarts cannot prove a real machine's cycle state.
        return {"availability": "unknown"} if self.mode == "pulse" else {
            "availability": "available", "operating_state": "idle",
            "remaining_seconds": 0, "door_locked": False,
        }


def manufacturer_adapter(manufacturer):
    raise ValueError(f"{manufacturer}: no verified protocol/profile is bundled; select an explicit simulator")
