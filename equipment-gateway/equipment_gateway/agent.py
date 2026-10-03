"""Use the existing credential-free agent Unix-socket API only."""
import json
import socket

MAX_EVENT_PAYLOAD_BYTES = 49152


class AgentClient:
    def __init__(self, path="/run/dataplicity/product-runtime.sock"):
        self.path = path

    def call(self, method, **params):
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(self.path)
            connection.sendall(json.dumps(request, allow_nan=False).encode() + b"\n")
            with connection.makefile("rb") as stream:
                raw = stream.readline(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024 or not raw.endswith(b"\n"):
            raise ValueError("Invalid agent response frame")
        response = json.loads(raw)
        if "error" in response:
            raise RuntimeError(response["error"].get("message", "Agent RPC failed"))
        return response["result"]

    def event(self, event_type, payload, *, event_id=None, instance_id="", correlation_id="", durability="critical"):
        if len(json.dumps(payload, allow_nan=False).encode()) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("Equipment event exceeds its byte budget")
        return self.call("PublishProductEvent", event_type=event_type, payload=payload,
                         event_id=event_id, product_instance_id=instance_id,
                         correlation_id=correlation_id, durability=durability)
