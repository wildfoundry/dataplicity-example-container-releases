"""Minimal Linux GPIO character-device access for lab pulse output.

Uses the GPIO uAPI v1 line-handle ioctls (widely available on Raspberry Pi OS).
Host-timed pulses are for bench verification on a controller GPIO. They are not
a fail-safe remote I/O timer; production laundry actuation should use a
controller-owned timer when the target hardware provides one.
"""
from __future__ import annotations

import array
import fcntl
import logging
import os
import struct
import time
from dataclasses import dataclass
from typing import Callable, Optional

LOG = logging.getLogger("equipment-gateway.gpio")

GPIO_MAX_NAME_SIZE = 32
GPIOHANDLES_MAX = 64

GPIOHANDLE_REQUEST_OUTPUT = 1 << 1
GPIOHANDLE_REQUEST_ACTIVE_LOW = 1 << 2

# linux/gpio.h
_GPIO_GET_LINEHANDLE_IOCTL = 0xC16CB403
_GPIOHANDLE_SET_LINE_VALUES_IOCTL = 0xC040B409


@dataclass
class LineHandle:
    fd: int
    offset: int
    chip: str
    consumer: str
    active_high: bool

    def set_active(self, active: bool) -> None:
        # With ACTIVE_LOW requested, the kernel inverts electrical level.
        values = array.array("B", [0] * GPIOHANDLES_MAX)
        values[0] = 1 if active else 0
        fcntl.ioctl(self.fd, _GPIOHANDLE_SET_LINE_VALUES_IOCTL, values)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


OpenLine = Callable[[str, int, str, bool], LineHandle]


def open_output_line(chip: str, offset: int, consumer: str, active_high: bool) -> LineHandle:
    if not isinstance(chip, str) or not chip.startswith("/dev/gpiochip"):
        raise ValueError("GPIO chip must be an explicit /dev/gpiochip* path")
    if type(offset) is not int or not 0 <= offset < 512:
        raise ValueError("GPIO line offset is out of bounds")
    if not isinstance(consumer, str) or not 0 < len(consumer) <= 31:
        raise ValueError("GPIO consumer label must be 1-31 characters")

    flags = GPIOHANDLE_REQUEST_OUTPUT
    if not active_high:
        flags |= GPIOHANDLE_REQUEST_ACTIVE_LOW

    offsets = [0] * GPIOHANDLES_MAX
    offsets[0] = offset
    defaults = [0] * GPIOHANDLES_MAX  # idle / inactive
    consumer_bytes = consumer.encode("utf-8")[: GPIO_MAX_NAME_SIZE - 1].ljust(GPIO_MAX_NAME_SIZE, b"\0")

    # struct gpiohandle_request
    request = struct.pack(
        f"<{GPIOHANDLES_MAX}II{GPIOHANDLES_MAX}B{GPIO_MAX_NAME_SIZE}sIi",
        *offsets,
        flags,
        *defaults,
        consumer_bytes,
        1,  # lines
        -1,  # fd filled by kernel
    )
    chip_fd = os.open(chip, os.O_RDONLY | os.O_CLOEXEC)
    try:
        buf = array.array("B", request)
        fcntl.ioctl(chip_fd, _GPIO_GET_LINEHANDLE_IOCTL, buf)
        line_fd = struct.unpack_from("<i", buf, len(buf) - 4)[0]
        if line_fd < 0:
            raise OSError(f"GPIO line request failed for {chip} offset {offset}")
        handle = LineHandle(
            fd=line_fd,
            offset=offset,
            chip=chip,
            consumer=consumer,
            active_high=active_high,
        )
        handle.set_active(False)
        return handle
    finally:
        os.close(chip_fd)


def hybrid_wait(
    seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    spin_tail_seconds: float = 0.002,
) -> None:
    """Sleep most of the interval, then spin the last few milliseconds.

    Host-timed lab pulses on CM5/RP1 cannot match a controller timer, but a
    short busy-wait tail reduces scheduler jitter versus a bare ``time.sleep``.
    """
    if seconds <= 0:
        return
    deadline = monotonic() + float(seconds)
    remaining = deadline - monotonic()
    if remaining > spin_tail_seconds:
        sleep(remaining - spin_tail_seconds)
    while monotonic() < deadline:
        pass


def pulse_line(
    handle: LineHandle,
    *,
    duration_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    wait: Optional[Callable[[float], None]] = None,
) -> None:
    """Drive an active pulse for ``duration_seconds``.

    Prefer injecting ``wait`` in tests. Production callers may omit it to use
    ``hybrid_wait`` (sleep + short busy-wait tail) for better edge timing.
    """
    wait_fn = wait if wait is not None else (lambda seconds: hybrid_wait(seconds, sleep=sleep))
    handle.set_active(True)
    try:
        wait_fn(duration_seconds)
    finally:
        handle.set_active(False)


class GpioLineSession:
    """Cache requested output lines for repeated credit pulses."""

    def __init__(self, open_line: Optional[OpenLine] = None):
        self._open_line = open_line or open_output_line
        self._handles: dict[tuple[str, int, str, bool], LineHandle] = {}

    def handle(self, *, chip: str, line: int, consumer: str, active_high: bool) -> LineHandle:
        key = (chip, line, consumer, active_high)
        handle = self._handles.get(key)
        if handle is None or handle.fd < 0:
            handle = self._open_line(chip, line, consumer, active_high)
            self._handles[key] = handle
            LOG.info(
                "gpio_line_opened chip=%s line=%s consumer=%s active_high=%s",
                chip,
                line,
                consumer,
                active_high,
            )
        return handle

    def close(self) -> None:
        for handle in self._handles.values():
            try:
                handle.set_active(False)
            except OSError:
                LOG.exception(
                    "failed to idle GPIO line before close chip=%s line=%s",
                    handle.chip,
                    handle.offset,
                )
            handle.close()
        self._handles.clear()
