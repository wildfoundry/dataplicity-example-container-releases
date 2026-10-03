"""Isolated serial Modbus RTU instrumentation, read-only functions 03/04."""
import struct
import time

from .profiles import register_count


class TransportError(OSError):
    def __init__(self, quality, message):
        super().__init__(message)
        self.quality = quality


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def request_frame(address, function, register, count):
    if not 1 <= address <= 247 or function not in {3, 4} or not 1 <= count <= 125 or not 0 <= register <= 65536 - count:
        raise ValueError("Invalid read-only Modbus request")
    body = struct.pack(">BBHH", address, function, register, count)
    return body + struct.pack("<H", crc16(body))


def parse_response(data, *, address, function, count):
    if len(data) < 5 or crc16(data[:-2]) != int.from_bytes(data[-2:], "little"):
        raise TransportError("crc_error", "Invalid Modbus frame CRC/length")
    if data[0] != address or data[1] not in {function, function | 0x80}:
        raise TransportError("invalid", "Unexpected Modbus responder/function")
    if data[1] & 0x80:
        raise TransportError("device_error", f"Modbus exception {data[2]}")
    if data[2] != count * 2 or len(data) != count * 2 + 5:
        raise TransportError("invalid", "Unexpected Modbus byte count")
    return list(struct.unpack(f">{count}H", data[3:-2]))


class ModbusReader:
    def __init__(self, transport):
        import serial
        self.transport = transport
        self.port = serial.Serial(transport["device"], baudrate=transport["baudrate"],
                                  parity=transport["parity"], stopbits=transport["stopbits"],
                                  timeout=transport.get("timeout_seconds", 1),
                                  write_timeout=transport.get("timeout_seconds", 1), exclusive=True)

    def read(self, point, *, address=None):
        count = register_count(point)
        address, function = address or self.transport["address"], point["function"]
        # RTU silence between requests; one sequential reader per bus.
        time.sleep(max(0.002, 3.5 * 11 / self.transport["baudrate"]))
        self.port.reset_input_buffer()
        self.port.write(request_frame(address, function, point["register"], count))
        header = self._read_exact(3)
        remaining = 2 if header[1] & 0x80 else count * 2 + 2
        return parse_response(header + self._read_exact(remaining), address=address, function=function, count=count)

    def _read_exact(self, count):
        data = bytearray()
        deadline = time.monotonic() + self.transport.get("timeout_seconds", 1)
        while len(data) < count and time.monotonic() < deadline:
            part = self.port.read(count - len(data))
            if not part:
                break
            data.extend(part)
        if len(data) != count:
            raise TransportError("timeout", "Incomplete Modbus response")
        return bytes(data)

    def close(self):
        self.port.close()


def write_frame(address, register, values):
    if type(address) is not int or not 1 <= address <= 247 or not isinstance(values, list) or not 1 <= len(values) <= 123:
        raise ValueError("Invalid Modbus write request")
    if type(register) is not int or not 0 <= register <= 65536 - len(values) or any(type(value) is not int or not 0 <= value <= 65535 for value in values):
        raise ValueError("Invalid Modbus register values")
    if len(values) == 1:
        body = struct.pack(">BBHH", address, 6, register, values[0])
    else:
        body = struct.pack(">BBHHB", address, 16, register, len(values), len(values) * 2) + struct.pack(f">{len(values)}H", *values)
    return body + struct.pack("<H", crc16(body))


def parse_write_response(data, *, request):
    if len(data) < 5 or crc16(data[:-2]) != int.from_bytes(data[-2:], "little"):
        raise TransportError("crc_error", "Invalid Modbus write acknowledgement")
    if data[0] != request[0] or data[1] not in {request[1], request[1] | 0x80}:
        raise TransportError("invalid", "Unexpected Modbus write responder/function")
    if data[1] & 0x80:
        raise TransportError("device_error", f"Modbus write exception {data[2]}")
    if len(data) != 8 or data[:6] != request[:6]:
        raise TransportError("invalid", "Unexpected Modbus write echo")
    return True


class ModbusTransport(ModbusReader):
    """One sequential RTU bus supports both observation and declared actuation."""
    def _write_request(self, request):
        time.sleep(max(0.002, 3.5 * 11 / self.transport["baudrate"]))
        self.port.reset_input_buffer()
        self.port.write(request)
        header = self._read_exact(3)
        reply = header + self._read_exact(2 if header[1] & 0x80 else 5)
        parse_write_response(reply, request=request)

    def write_registers(self, register, values, *, address=None):
        self._write_request(write_frame(address or self.transport["address"], register, values))

    def write_output(self, register, value, *, address=None):
        if type(value) is not bool or type(register) is not int or not 0 <= register <= 65535:
            raise ValueError("Invalid Modbus coil output")
        body = struct.pack(">BBHH", address or self.transport["address"], 5, register, 0xFF00 if value else 0)
        self._write_request(body + struct.pack("<H", crc16(body)))

    def read_input(self, register, *, function=2, address=None):
        address = address or self.transport["address"]
        if type(register) is not int or not 0 <= register <= 65535 or function not in {1, 2}:
            raise ValueError("Invalid Modbus digital input")
        body = struct.pack(">BBHH", address, function, register, 1)
        time.sleep(max(0.002, 3.5 * 11 / self.transport["baudrate"]))
        self.port.reset_input_buffer()
        self.port.write(body + struct.pack("<H", crc16(body)))
        header = self._read_exact(3)
        data = header + self._read_exact(2 if header[1] & 0x80 else 3)
        if crc16(data[:-2]) != int.from_bytes(data[-2:], "little"):
            raise TransportError("crc_error", "Invalid Modbus input acknowledgement")
        if data[0] != address or data[1] not in {function, function | 0x80}:
            raise TransportError("invalid", "Unexpected digital input responder/function")
        if data[1] & 0x80:
            raise TransportError("device_error", "Modbus digital input exception")
        if len(data) != 6 or data[2] != 1:
            raise TransportError("invalid", "Invalid digital input byte count")
        return bool(data[3] & 1)
