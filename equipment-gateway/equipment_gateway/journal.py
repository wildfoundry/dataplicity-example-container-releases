"""Bounded physical-effect journal, complementary to the agent command journal.

The agent owns transport/retry/auth. This journal reserves the physical effect
identity before dispatch so even a new invocation cannot duplicate an effect.
Issued commands survive restarts as unknown and are never automatically retried.
Capacity exhaustion fails closed; unresolved evidence is never pruned.
"""
import hashlib
import fcntl
import json
import sqlite3
import threading


class Journal:
    def __init__(self, path, *, max_commands=100000):
        self.process_lock = open(str(path) + ".lock", "a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            raise ValueError("Another gateway owns this physical command journal")
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        legacy = self.db.execute("PRAGMA table_info(commands)").fetchall()
        if any(row[1] == "service_key" for row in legacy):
            self.db.close()
            self.process_lock.close()
            raise ValueError("Legacy development journal requires a reviewed migration; retained evidence must not be cleared")
        self.db.execute("""CREATE TABLE IF NOT EXISTS commands (
            invocation_id TEXT PRIMARY KEY, effect_key TEXT UNIQUE NOT NULL,
            fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
            certainty TEXT NOT NULL, reason TEXT NOT NULL, reported INTEGER NOT NULL DEFAULT 0)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS aliases (
            invocation_id TEXT PRIMARY KEY, original_id TEXT NOT NULL,
            reported INTEGER NOT NULL DEFAULT 0)""")
        if not any(row[1] == "result" for row in self.db.execute("PRAGMA table_info(commands)")):
            self.db.execute("ALTER TABLE commands ADD COLUMN result TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()
        self.max_commands = max_commands
        self.lock = threading.Lock()

    def close(self):
        self.db.close()
        self.process_lock.close()

    def lookup(self, invocation_id):
        row = self.db.execute("SELECT * FROM commands WHERE invocation_id=?", (invocation_id,)).fetchone()
        if row is None:
            row = self.db.execute("""SELECT a.invocation_id,c.effect_key,c.fingerprint,c.payload,
                c.certainty,c.reason,a.reported,c.result FROM aliases a JOIN commands c
                ON c.invocation_id=a.original_id WHERE a.invocation_id=?""", (invocation_id,)).fetchone()
        return dict(row) if row else None

    def execute(self, invocation_id, action, payload, adapter, *, instance_id, correlation_id=""):
        if any(not isinstance(payload.get(key), str) or not payload[key] or len(payload[key]) > 128
               for key in ("equipment_id", "effect_id")):
            raise ValueError("Equipment and immutable effect IDs are required")
        if not invocation_id or not instance_id:
            raise ValueError("Agent invocation and product instance IDs are required")
        # The application supplies an immutable effect key; no business meaning
        # is inferred from adapter parameters or correlation metadata.
        identity = [instance_id, payload["equipment_id"], payload["effect_id"], action]
        effect_key = json.dumps(identity, separators=(",", ":"))
        encoded = json.dumps({"action": action, "input": payload, "instance_id": instance_id, "correlation_id": correlation_id}, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("Physical command payload exceeds journal limit")
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.lookup(invocation_id)
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise ValueError("Immutable invocation identity reused with different input")
                    self.db.commit()
                    return existing
                row = self.db.execute("SELECT * FROM commands WHERE invocation_id=? OR effect_key=?",
                                      (invocation_id, effect_key)).fetchone()
                if row:
                    if row["fingerprint"] != fingerprint:
                        raise ValueError("Immutable command/effect identity reused with different input")
                    if self.db.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] >= self.max_commands:
                        raise ValueError("Command alias journal full")
                    self.db.execute("INSERT INTO aliases VALUES (?,?,0)", (invocation_id, row["invocation_id"]))
                    self.db.commit()
                    return self.lookup(invocation_id)
                if self.db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] >= self.max_commands:
                    raise ValueError("Physical command journal full; archive evidence before commissioning more commands")
                adapter.validate(action, payload)
                supported = adapter.capabilities["actions"].get(action, {}).get("support") == "supported"
                certainty = "unknown" if supported else "definitely_rejected"
                reason = "dispatch_reserved_outcome_unknown" if supported else "unsupported_action"
                self.db.execute("INSERT INTO commands (invocation_id,effect_key,fingerprint,payload,certainty,reason,reported) VALUES (?,?,?,?,?,?,0)",
                                (invocation_id, effect_key, fingerprint, encoded, certainty, reason))
                self.db.commit()  # Must be durable before the physical side effect.
            except BaseException:
                self.db.rollback()
                raise
            if supported:
                result = {}
                try:
                    outcome = adapter.execute(action, payload)
                    certainty, reason = outcome.certainty, outcome.reason
                    result = outcome.result
                except Exception:
                    certainty, reason = "unknown", "adapter_error_after_dispatch"
                with self.db:
                    self.db.execute("UPDATE commands SET certainty=?,reason=?,result=? WHERE invocation_id=?",
                                    (certainty, reason, json.dumps(result, allow_nan=False), invocation_id))
            return self.lookup(invocation_id)

    def unreported(self):
        originals = [dict(row) for row in self.db.execute("SELECT * FROM commands WHERE reported=0 ORDER BY rowid LIMIT 100")]
        aliases = [self.lookup(row[0]) for row in self.db.execute("SELECT invocation_id FROM aliases WHERE reported=0 LIMIT 100")]
        return originals + aliases

    def mark_reported(self, invocation_id):
        with self.db:
            self.db.execute("UPDATE commands SET reported=1 WHERE invocation_id=?", (invocation_id,))
            self.db.execute("UPDATE aliases SET reported=1 WHERE invocation_id=?", (invocation_id,))
