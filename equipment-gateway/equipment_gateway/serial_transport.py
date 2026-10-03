"""Bounded raw serial exchanges; equipment adapters own protocol interpretation."""
import time
from .modbus import TransportError


class SerialTransport:
    def __init__(self, transport):
        import serial
        self.transport = transport
        self.port = serial.Serial(transport["device"], baudrate=transport["baudrate"],
            parity=transport["parity"], stopbits=transport["stopbits"],
            timeout=transport["timeout_seconds"], write_timeout=transport["timeout_seconds"], exclusive=True)

    def transfer(self, request, response_bytes):
        if not request or len(request) > 256 or type(response_bytes) is not int or not 0 <= response_bytes <= 256:
            raise ValueError("Serial transfer exceeds bounds")
        self.port.reset_input_buffer()
        if self.port.write(request) != len(request):
            raise TransportError("timeout", "Incomplete serial write; outcome unknown")
        response = bytearray()
        deadline = time.monotonic() + self.transport["timeout_seconds"]
        while len(response) < response_bytes and time.monotonic() < deadline:
            part = self.port.read(response_bytes - len(response))
            if not part: break
            response.extend(part)
        if len(response) != response_bytes:
            raise TransportError("timeout", "Incomplete serial response; outcome unknown")
        return bytes(response)

    def close(self):
        self.port.close()
