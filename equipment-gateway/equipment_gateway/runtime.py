"""Managed Software entrypoint: equipment commands and instrumentation."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
import uuid

from jsonschema import ValidationError

from . import __version__
from .adapters import load_adapter
from .agent import AgentClient
from .journal import Journal
from .modbus import ModbusTransport as ModbusReader, TransportError
from .profiles import observe, validate_bus_assignments
from .physical import PrimitiveError

LOG = logging.getLogger("equipment-gateway")


def load_config(path):
    config = json.loads(Path(path).read_text())
    if config.get("schema_version") != 2 or not isinstance(config.get("product_instance_id"), str) or not config["product_instance_id"]:
        raise ValueError("Gateway config requires schema_version=2 and product_instance_id")
    equipment = config.get("equipment", [])
    if not isinstance(equipment, list) or len(equipment) > 128:
        raise ValueError("Gateway supports up to 128 equipment endpoints")
    identifiers = [entry["equipment_id"] for entry in equipment]
    if len(set(identifiers)) != len(identifiers) or any(not isinstance(i, str) or not i for i in identifiers):
        raise ValueError("Equipment IDs must be unique nonempty strings")
    for entry in equipment:
        load_adapter(entry)
    instruments = config.get("instruments", [])
    if not isinstance(instruments, list) or len(instruments) > 128:
        raise ValueError("Gateway supports up to 128 instruments")
    instrument_ids = [i["instrument_id"] for i in instruments]
    if len(set(instrument_ids)) != len(instrument_ids) or any(not isinstance(i, str) or not i for i in instrument_ids):
        raise ValueError("Instrument IDs must be unique nonempty strings")
    validate_bus_assignments([i["profile"] for i in instruments] + [entry["options"]["profile"] for entry in equipment if "profile" in entry.get("options", {})])
    for instrument in instruments:
        kind = instrument["profile"]["transport"]["kind"]
        if kind != "modbus_rtu" and instrument.get("source") != "simulated":
            raise ValueError("Pulse/digital inputs require an explicit simulator until an isolated input driver is supplied")
    return config


class Gateway:
    def __init__(self, config, agent, journal):
        self.config, self.agent, self.journal = config, agent, journal
        self.instance_id = config["product_instance_id"]
        self.readers = {}
        self.adapters = {entry["equipment_id"]: load_adapter(entry, self.readers) for entry in config.get("equipment", [])}
        self.next_poll = {}
        self.inventory_sent = False
        self.profile_points = [(instrument, point) for instrument in config.get("instruments", [])
                               for point in instrument["profile"]["points"]]
        self.poll_cursor = 0
        self.state_cursor = 0

    def report(self, row):
        command = json.loads(row["payload"])
        result = {"input": command["input"], "adapter_result": json.loads(row["result"]),
                  "outcome": row["certainty"], "reason": row["reason"],
                  "invocation_id": row["invocation_id"],
                  "requires_reconciliation": row["certainty"] == "unknown"}
        self.agent.event("equipment.command_outcome", result,
                         event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"equipment:{self.instance_id}:{row['invocation_id']}")),
                         instance_id=self.instance_id, correlation_id=command.get("correlation_id", ""))
        self.agent.call("ReportCommandResult", invocation_id=row["invocation_id"],
                        success=row["certainty"] == "accepted", rejected=row["certainty"] == "definitely_rejected",
                        result=result, error=row["reason"] if row["certainty"] != "accepted" else "")
        self.journal.mark_reported(row["invocation_id"])

    def handle(self, command):
        # Do not consume unrelated workloads sharing the same agent runtime.
        payload = command.get("input", {})
        equipment_id = payload.get("equipment_id")
        if equipment_id not in self.adapters or command.get("product_instance_id") != self.instance_id:
            return
        invocation_id = command["invocation_id"]
        action = command.get("device_action_key") or command["command_key"]
        if command.get("safety") != "unsafe_to_duplicate":
            self.agent.call("ReportCommandResult", invocation_id=invocation_id, success=False,
                            rejected=True, error="Physical commands must be unsafe_to_duplicate")
            return
        try:
            if command.get("deadline"):
                deadline = datetime.fromisoformat(command["deadline"].replace("Z", "+00:00"))
                if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                    self.agent.call("ReportCommandResult", invocation_id=invocation_id, success=False,
                                    rejected=True, error="Command deadline expired or lacks timezone")
                    return
            # The agent is the authoritative cancellation/deadline gate. Check
            # its returned state before any local dispatch.
            response = self.agent.call("MarkCommandExecuting", invocation_id=invocation_id)
            if response["record"]["status"] != "executing":
                return
            row = self.journal.execute(invocation_id, action, payload, self.adapters[equipment_id], instance_id=self.instance_id, correlation_id=command.get("correlation_id", ""))
        except (ValidationError, KeyError, PrimitiveError) as exc:
            self.agent.call("ReportCommandResult", invocation_id=invocation_id, success=False,
                            rejected=True, error=str(exc))
            return
        except ValueError as exc:
            # Identity conflict can refer to an already-executed effect.
            # Never let a generic rejection trigger automatic compensation.
            self.agent.call("ReportCommandResult", invocation_id=invocation_id, success=False,
                            result={"outcome": "unknown", "requires_reconciliation": True}, error=str(exc))
            return
        self.report(row)

    def poll_instruments(self, now):
        # A disconnected bus must not monopolise equipment command handling.
        budget_deadline = time.monotonic() + 2
        for _ in range(len(self.profile_points)):
            if time.monotonic() >= budget_deadline:
                return
            instrument, point = self.profile_points[self.poll_cursor]
            self.poll_cursor = (self.poll_cursor + 1) % len(self.profile_points)
            profile = instrument["profile"]
            transport = profile["transport"]
            key = (instrument["instrument_id"], point["key"])
            if now < self.next_poll.get(key, 0):
                continue
            raw, error = None, ""
            try:
                if instrument.get("source") == "simulated":
                    raw = instrument.get("raw", {}).get(point["key"])
                elif transport["kind"] == "modbus_rtu":
                    device = transport["device"]
                    if device not in self.readers:
                        self.readers[device] = ModbusReader(transport)
                    raw = self.readers[device].read(point, address=transport["address"])
            except TransportError as exc:
                error = exc.quality
            except OSError:
                error = "disconnected"
                reader = self.readers.pop(transport.get("device"), None)
                if reader:
                    reader.close()
            observed_at = time.time()
            value = observe(profile, point["key"], raw, observed_at, error=error).derive(observed_at)
            value.update(instrument_id=instrument["instrument_id"], simulated=instrument.get("source") == "simulated")
            self.agent.event("equipment.observation", value, instance_id=self.instance_id, durability="durable_batched")
            self.next_poll[key] = observed_at + point["poll_seconds"]

    def tick(self):
        if not self.inventory_sent:
            self.agent.event("equipment.gateway_inventory", {"version": __version__,
                             "equipment": {key: value.capabilities for key, value in self.adapters.items()},
                             "profiles": [{"instrument_id": i["instrument_id"], "profile_id": i["profile"]["profile_id"],
                                           "version": i["profile"]["version"]} for i in self.config.get("instruments", [])]},
                             instance_id=self.instance_id)
            self.inventory_sent = True
        # Replay application outcomes, never physical commands, after a restart.
        for row in self.journal.unreported():
            self.report(row)
        pending = self.agent.call("GetPendingCommands", limit=100)["commands"]
        for command in pending:
            self.handle(command)
        now = time.time()
        equipment = list(self.adapters.items())
        state_deadline = time.monotonic() + 2
        for _ in range(len(equipment)):
            if time.monotonic() >= state_deadline:
                break
            equipment_id, adapter = equipment[self.state_cursor]
            self.state_cursor = (self.state_cursor + 1) % len(equipment)
            self.agent.event("equipment.state", {"equipment_id": equipment_id,
                "state": adapter.state(observed_at=now, now=now)},
                instance_id=self.instance_id, durability="volatile")
        self.poll_instruments(now)

    def close(self):
        for reader in self.readers.values():
            reader.close()
        self.journal.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.environ.get("EQUIPMENT_CONFIG", "/etc/equipment-gateway/config.json"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capabilities", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check:
        print("equipment-gateway configuration valid")
        return
    if args.capabilities:
        print(json.dumps({entry["equipment_id"]: load_adapter(entry).capabilities for entry in config.get("equipment", [])}, indent=2))
        return
    logging.basicConfig(level=logging.INFO)
    state = Path(os.environ.get("EQUIPMENT_STATE_DIR", "/var/lib/equipment-gateway"))
    state.mkdir(parents=True, exist_ok=True)
    gateway = Gateway(config, AgentClient(os.environ.get("PRODUCT_RUNTIME_SOCKET", "/run/dataplicity/product-runtime.sock")),
                      Journal(state / "commands.sqlite3"))
    try:
        while True:
            try:
                gateway.tick()
            except (OSError, RuntimeError, ValueError):
                LOG.exception("Agent or equipment unavailable; retaining durable outcomes")
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
