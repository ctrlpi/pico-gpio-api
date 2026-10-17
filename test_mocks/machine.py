import sys
import os

try:
    class _MockUname:
        sysname = "rp2"
        nodename = "rp2"
        release = "1.24.0"
        version = "v1.24.0"
        machine = "Raspberry Pi Pico W with RP2040"
    os.uname = lambda: _MockUname()
except Exception:
    pass

class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    _pins = {}

    def __init__(self, id, mode=IN, pull=None, value=None):
        self.id = id
        self.mode = mode
        self.pull = pull
        self.irq_handler = None
        if id not in Pin._pins:
            Pin._pins[id] = {"value": 0 if value is None else int(value), "mode": mode}
        elif value is not None:
            Pin._pins[id]["value"] = int(value)

    def value(self, v=None):
        if v is not None:
            Pin._pins[self.id]["value"] = int(bool(v))
        return Pin._pins[self.id]["value"]

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def toggle(self):
        self.value(1 - self.value())

    def irq(self, trigger=0, handler=None):
        self.irq_handler = handler

class Timer:
    PERIODIC = 1
    ONE_SHOT = 2

    def __init__(self, id=-1):
        self.id = id

    def init(self, period=1000, mode=PERIODIC, callback=None):
        self.callback = callback

    def deinit(self):
        pass

class ADC:
    def __init__(self, pin):
        self.pin = pin

    def read_u16(self):
        return 14000

def unique_id():
    return b"\xe6\x60\x58\x38\x83\x27\x91\x28"

def reset():
    sys.exit(0)

def soft_reset():
    sys.exit(0)
