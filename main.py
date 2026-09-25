# main.py - MicroPython single-file GPIO REST API for Raspberry Pi Pico W

import gc
import machine
import micropython
import network
import os
import usocket
import ujson
import utime

try:
	import uerrno
except ImportError:
	import errno as uerrno

try:
	import ssl
	_HAS_SSL = True
except ImportError:
	_HAS_SSL = False

### Constants

FALLBACK_SSID     = "MyNetwork"
FALLBACK_PASSWORD = "MyPassword"
DEFAULT_API_KEY   = "your-secret-key"
VERSION           = "0.9.23"  # agent software version
DEFAULT_LOG_DAYS  = 7
PORT              = 8314
WIFI_DEAD_SECS    = 40    # dead-man switch: hard-reset if WiFi stays lost this long
WIFI_PROBE_SECS   = 300   # zombie-link check: probe gateway after this long with no traffic
WEBHOOK_MIN_MS    = 200   # per-pin watch debounce gap
WEBHOOK_IDLE_MS   = 90000 # close cached webhook socket after idle timeout
WEBHOOK_RETRIES   = 2     # extra delivery attempts
WEBHOOK_RETRY_MS  = 30000 # wait before retry
CONFIG_FILE       = "config.json"
BACKUP_FILE       = "config-backup.json"
STATUS_FILE       = "status.json"
MAIN_FILE         = "main.py"
UPGRADE_URL       = "https://raw.githubusercontent.com/ctrlpi/pico-gpio-api/main/main.py"
LOG_MAX           = 50
LOG_RETURN        = 30
MAX_REQUEST_BYTES = 8192
RESERVED_PINS     = (23, 24, 25, 29) # CYW43 pins (WL_ON, SPI data/CS/CLK)
PICO_GPIO_PINS    = [g for g in range(29) if g not in RESERVED_PINS]

_MODEL_NOISE = ("raspberry", "model")

def _strip_model_words(text):
	parts = [w for w in str(text).split() if w.lower() not in _MODEL_NOISE]
	if len(parts) > 1 and parts[0].lower() == "pi" and parts[1].lower() == "pico":
		parts = parts[1:]
	return " ".join(parts)

try:
	_MACHINE = os.uname().machine
except Exception:
	_MACHINE = "Raspberry Pi Pico W"
IS_PICO2 = "pico 2" in _MACHINE.lower()
PLATFORM = _strip_model_words(_MACHINE) or ("Pico 2 W" if IS_PICO2 else "Pico W")

HTTPS_WEBHOOK_OK    = IS_PICO2
HTTPS_WEBHOOK_ERROR = ("https webhook URLs are not supported on the Pico W: TLS needs more "
                       "contiguous memory than the RP2040 can reliably provide. Use an "
                       "http:// URL, or a Pico 2 W.")

def _is_https(url):
	return str(url or "").strip().lower().startswith("https://")

_STATUS_TEXT = {
	200: "OK", 400: "Bad Request", 403: "Forbidden",
	404: "Not Found", 422: "Unprocessable Entity", 500: "Internal Server Error",
}

SCRIPTS_DIR = "scripts"
_READONLY_KEYS = ("agent",)

_ROOT_ALIASES = {
	"webhook_url":  ("notifications", "webhook"),
	"webhook_key":  ("notifications", "webhook_key"),
	"logs_enabled": ("settings", "logs_enabled"),
	"log_days":     ("settings", "log_days"),
	"matter":       ("bridges", "matter"),
	"matter_key":   ("bridges", "matter_key"),
	"homekit":      ("bridges", "homekit"),
	"homekit_key":  ("bridges", "homekit_key"),
	"homebridge":     ("bridges", "homebridge"),
	"homebridge_key": ("bridges", "homebridge_key"),
}

def _normalize_config(data):
	out = {k: v for k, v in data.items() if k not in _READONLY_KEYS and k not in _ROOT_ALIASES}
	for alias in _ROOT_ALIASES:
		if data.get(alias) is None:
			continue
		group, field = _ROOT_ALIASES[alias]
		target = out.setdefault(group, {})
		if isinstance(target, dict) and field not in target:
			target[field] = data[alias]
	return out

INIT_VALUES = (0, 1, "last")

def _norm_init(v):
	if isinstance(v, str):
		low = v.lower()
		if low == "none":    return None
		if low == "on":      return 1
		if low == "off":     return 0
		if low == "restore": return "last"
		return low
	if v is True:  return 1
	if v is False: return 0
	if isinstance(v, float):
		if v != int(v):
			return v
		v = int(v)
	if isinstance(v, int):
		return 1 if v != 0 else 0
	return v

def _check_init(v):
	if v is not None and v not in INIT_VALUES:
		raise ValueError('init must be 0, 1 or "last" (or "on"/"off"/"restore"/"none"), got ' + repr(v))
	return v

def _is_alnum(s):
	return bool(s) and all(("0" <= c <= "9") or ("a" <= c <= "z") or ("A" <= c <= "Z") for c in s)

def _is_script_name(s):
	if not s or s[0] == ".":
		return False
	return all(_is_alnum(c) or c in "._-" for c in s)

def _sensor_script_parts(script):
	parts = (script or "").split()
	if not parts:
		return "empty script", None, None
	if not _is_script_name(parts[0]):
		return "script must be a bare filename in the scripts folder", None, None
	for a in parts[1:]:
		if not _is_alnum(a):
			return "script args must be letters and digits only", None, None
	return None, SCRIPTS_DIR + "/" + parts[0], parts[1:]

### Onboard LED blinker

class Blinker:
	def __init__(self):
		try:
			self._led = machine.Pin("LED", machine.Pin.OUT, value=0)
		except Exception:
			self._led = None
		self._timer = machine.Timer(-1)
		self._on    = 0
		self._left  = 0
		self._fast  = False

	def _set(self, v):
		self._on = v
		self._led.value(v)

	def start_fast(self):
		if not self._led:
			return
		self._fast = True
		self._timer.init(period=100, mode=machine.Timer.PERIODIC, callback=lambda t: self._set(0 if self._on else 1))

	def stop_fast(self):
		if not self._led or not self._fast:
			return
		self._fast = False
		self._timer.deinit()
		self._set(0)

	def blink(self, count):
		if not self._led or self._fast:
			return
		if self._left > 0:
			if count > self._left:
				self._left = count
			return
		self._left = count
		self._set(1)
		self._timer.init(period=200, mode=machine.Timer.PERIODIC, callback=self._step)

	def _step(self, t):
		if self._on:
			self._set(0)
			self._left -= 1
			if self._left <= 0:
				self._timer.deinit()
		elif self._left > 0:
			self._set(1)

BLINKER = Blinker()

### Webhook sender

class WebhookSender:
	"""Keep-alive HTTP(S) client for webhook POSTs (caches https socket to avoid TLS ENOMEM)."""

	def __init__(self):
		self._sock = None
		self._url  = None
		self._last = 0

	def _close(self):
		if self._sock:
			try:
				self._sock.close()
			except Exception:
				pass
		self._sock = None

	def _connect(self, url):
		proto, rest = url.split("://", 1)
		host_port, _, path = rest.partition("/")
		host, _, port = host_port.partition(":")
		port = int(port) if port else (443 if proto == "https" else 80)
		if proto == "https" and not _HAS_SSL:
			raise OSError("no ssl module")
		gc.collect()
		s = usocket.socket()
		s.settimeout(7)
		s.connect(usocket.getaddrinfo(host, port)[0][-1])
		if proto == "https":
			ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
			ctx.verify_mode = ssl.CERT_NONE
			s = ctx.wrap_socket(s, server_hostname=host)
		return s, host, "/" + path

	def _expired(self):
		return self._sock is not None and utime.ticks_diff(utime.ticks_ms(), self._last) > WEBHOOK_IDLE_MS

	def maybe_expire(self):
		if self._expired():
			self._close()

	@staticmethod
	def _check(status):
		if status >= 300:
			raise OSError(f"HTTP {status}")

	def send(self, url, payload, api_key):
		body = ujson.dumps(payload).encode()
		if not _is_https(url):
			sock, host, path = self._connect(url)
			try:
				status, _ = self._request(sock, host, path, body, api_key)
			finally:
				try:
					sock.close()
				except Exception:
					pass
			self._check(status)
			return
		for attempt in (0, 1):
			if self._sock is None or self._url != url or self._expired():
				self._close()
				self._sock, self._host, self._path = self._connect(url)
				self._url = url
			try:
				status, keep = self._request(self._sock, self._host, self._path, body, api_key)
				if not keep:
					self._close()
				self._last = utime.ticks_ms()
			except Exception:
				self._close()
				if attempt:
					raise
				continue
			self._check(status)
			return

	def _request(self, sock, host, path, body, api_key):
		s = sock
		s.write(("POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\n"
		         "Content-Length: {}\r\n{}Connection: keep-alive\r\n\r\n").format(
		             path, host, len(body),
		             "Api-Key: {}\r\n".format(api_key) if api_key else "").encode())
		s.write(body)
		line = s.readline()
		if not line or not line.startswith(b"HTTP/"):
			raise OSError("bad response")
		try:
			status = int(line.split(b" ")[1])
		except Exception:
			status = 0
		cl, keep = 0, True
		while True:
			line = s.readline()
			if not line or line == b"\r\n":
				break
			ll = line.lower()
			if ll.startswith(b"content-length:"):
				cl = int(line.split(b":", 1)[1])
			elif ll.startswith(b"connection:") and b"close" in ll:
				keep = False
			elif ll.startswith(b"transfer-encoding:"):
				keep = False
		while cl > 0:
			chunk = s.read(min(cl, 256))
			if not chunk:
				break
			cl -= len(chunk)
		return status, keep

### GPIO Manager

class PinNotFound(Exception): pass
class SensorNotFound(PinNotFound): pass

class GPIOManager:
	def __init__(self):
		self.config      = {}
		self.status      = {}
		self.pins        = {}
		self.pin_configs = {}
		self.watches     = {}
		self._webhook_queue   = []
		self._webhook_sender  = WebhookSender()
		self._pending_reverts = []
		self._last_watch_ms   = {}
		self._log        = []
		self._last_log_cleanup = utime.time()
		self._config_dirty = False
		self._status_dirty = False
		self._last_flush   = utime.time()
		self._load_config()
		self._load_status()
		self._initialize_pins()
		self.log("[BOOT] GPIO manager ready")

	def _load_config(self):
		try:
			with open(CONFIG_FILE) as f:
				self.config = ujson.load(f)
		except OSError:
			self.config = {}
		except Exception as e:
			self.log(f"[Config] {CONFIG_FILE} is invalid ({e}); loading backup {BACKUP_FILE}")
			try:
				with open(BACKUP_FILE) as f:
					self.config = ujson.load(f)
			except Exception:
				self.config = {}
		self.config.setdefault("api_key",       DEFAULT_API_KEY)
		self.config.setdefault("gpios",         {})
		self.config.setdefault("notifications", {})
		self.config.setdefault("bridges",       {})
		self.config.setdefault("settings",      {})
		self.config.setdefault("sensors",       {})
		self.config["settings"].setdefault("logs_enabled", True)
		self.config["settings"].setdefault("log_days", DEFAULT_LOG_DAYS)
		try:
			self.config["settings"]["log_days"] = max(0, min(int(self.config["settings"]["log_days"]), 30))
		except (TypeError, ValueError):
			self.config["settings"]["log_days"] = DEFAULT_LOG_DAYS
		for g in list(self.config["gpios"]):
			if g.isdigit() and int(g) in RESERVED_PINS:
				del self.config["gpios"][g]
				self.log(f"[Config] GP{g} dropped (reserved for WiFi chip)")
				continue
			conf = self.config["gpios"][g]
			if conf.get("type") in ("output", "vcc", "gnd"):
				conf.pop("pullup", None)
			if conf.get("watched") is not True:
				conf.pop("watched", None)
			if "init" in conf:
				iv = _norm_init(conf["init"])
				if iv in INIT_VALUES:
					conf["init"] = iv
				else:
					conf.pop("init", None)
		self.pin_configs = self.config["gpios"]

	def _save_config(self):
		self._config_dirty = True

	def _save_status(self):
		for gpio_str, pin in self.pins.items():
			conf = self.pin_configs.get(gpio_str, {})
			if conf.get("type") in ("output", "input"):
				reversed_flag         = conf.get("reversed", False)
				level                 = pin.value()
				logical               = (1 - level) if reversed_flag else level
				self.status[gpio_str] = logical
		self._status_dirty = True

	def _flush_config(self):
		if not self._config_dirty:
			return
		self._config_dirty = False
		try:
			with open(CONFIG_FILE, "w") as f:
				ujson.dump(self.config, f)
		except Exception:
			self._config_dirty = True

	def _flush_status(self):
		if not self._status_dirty:
			return
		self._status_dirty = False
		try:
			with open(STATUS_FILE, "w") as f:
				ujson.dump(self.status, f)
		except Exception:
			self._status_dirty = True

	def _flush_all(self):
		self._flush_config()
		self._flush_status()

	def maybe_flush(self):
		if utime.time() - self._last_flush < 2:
			return
		self._last_flush = utime.time()
		BLINKER.blink(1)
		self._flush_all()

	def _backup_config(self):
		self._flush_config()
		try:
			with open(CONFIG_FILE) as src:
				data = src.read()
		except OSError:
			return
		with open(BACKUP_FILE, "w") as dst:
			dst.write(data)

	def _load_status(self):
		try:
			with open(STATUS_FILE) as f:
				self.status = ujson.load(f)
		except Exception:
			self.status = {}

	def _initialize_pins(self):
		for gpio_str, conf in list(self.pin_configs.items()):
			try:
				self._init_pin(gpio_str, conf)
			except Exception as e:
				self.log(f"[GPIO] Init GP{gpio_str} failed: {type(e).__name__} {repr(e)}")

	def _init_pin(self, gpio_str, conf, was_output=False):
		gpio          = int(gpio_str)
		pin_type      = conf.get("type", "input")
		reversed_flag = conf.get("reversed", False)

		existing = self.pins.get(gpio_str)
		if existing:
			try:
				existing.irq(handler=None)
			except Exception:
				pass
		self.watches.pop(gpio_str, None)

		if pin_type == "vcc":
			p = machine.Pin(gpio, machine.Pin.OUT)
			p.value(1)
		elif pin_type == "gnd":
			p = machine.Pin(gpio, machine.Pin.OUT)
			p.value(0)
		elif pin_type == "output":
			p    = machine.Pin(gpio, machine.Pin.OUT)
			init = conf.get("init")
			if init == "last":
				last_logical = self.status.get(gpio_str)
				if last_logical is not None:
					p.value((1 - last_logical) if reversed_flag else last_logical)
				else:
					p.value(0)
			elif init == 1:
				p.value(0 if reversed_flag else 1)
			elif init == 0:
				p.value(1 if reversed_flag else 0)
			elif was_output and self.status.get(gpio_str) is not None:
				last_logical = self.status.get(gpio_str)
				p.value((1 - last_logical) if reversed_flag else last_logical)
			else:
				p.value(0)
		else:  # input
			pullup = conf.get("pullup", "up")
			pull = machine.Pin.PULL_DOWN if pullup == "down" else (None if pullup == "none" else machine.Pin.PULL_UP)
			if pull is machine.Pin.PULL_DOWN and IS_PICO2:
				# RP2350 erratum E9: discharge latched pad before switching to input
				machine.Pin(gpio, machine.Pin.OUT, value=0)
			p = machine.Pin(gpio, machine.Pin.IN, pull)

			init = conf.get("init")
			if init is not None and conf.get("watched"):
				ref = self.status.get(gpio_str) if init == "last" else init
				if ref is not None:
					logical = (1 - p.value()) if reversed_flag else p.value()
					if logical != ref:
						self._trigger_webhook(gpio_str)

		self.pins[gpio_str] = p
		watched = conf.get("watched")
		if watched and pin_type not in ("vcc", "gnd"):
			if pin_type == "input":
				self._setup_irq(gpio_str, p)
			else:
				self.watches[gpio_str] = True

	def _setup_irq(self, gpio_str, pin):
		def handler(p, _g=gpio_str):
			try:
				micropython.schedule(self._on_watch_trigger, _g)
			except Exception:
				pass
		try:
			pin.irq(trigger=machine.Pin.IRQ_RISING | machine.Pin.IRQ_FALLING, handler=handler)
			self.watches[gpio_str] = True
		except Exception:
			pass

	def _on_watch_trigger(self, gpio_str):
		now  = utime.ticks_ms()
		last = self._last_watch_ms.get(gpio_str)
		if last is not None and utime.ticks_diff(now, last) < WEBHOOK_MIN_MS:
			return
		self._last_watch_ms[gpio_str] = now
		self._trigger_webhook(gpio_str)

	def _clear_watch(self, gpio_str):
		pin = self.pins.get(gpio_str)
		if pin:
			try:
				pin.irq(handler=None)
			except Exception:
				pass
		self.watches.pop(gpio_str, None)

	def resolve_gpio(self, name_or_gpio):
		if name_or_gpio.isdigit():
			if int(name_or_gpio) in RESERVED_PINS:
				raise ValueError(f"GP{name_or_gpio} is reserved for the WiFi chip")
			return name_or_gpio
		lo = name_or_gpio.lower()
		for gpio_str, conf in self.pin_configs.items():
			if (conf.get("name") or "").lower() == lo:
				return gpio_str
		return None

	def _is_static(self, gpio_str):
		return self.pin_configs.get(gpio_str, {}).get("type") in ("vcc", "gnd")

	def get_pin_info(self, gpio_str, show_level=False):
		conf          = self.pin_configs.get(gpio_str, {})
		pin           = self.pins.get(gpio_str)
		pin_type      = conf.get("type", "input")
		reversed_flag = conf.get("reversed", False) if pin_type not in ("vcc", "gnd") else False

		if pin is None:
			try:
				pull = machine.Pin.PULL_UP if conf.get("pullup", "up") != "down" else machine.Pin.PULL_DOWN
				pin  = machine.Pin(int(gpio_str), machine.Pin.IN, pull)
				self.pins[gpio_str] = pin
			except Exception:
				return None

		level = pin.value()
		value = (1 - level) if reversed_flag else level

		info = {"gpio": int(gpio_str), "value": value, "type": pin_type}
		if show_level or reversed_flag:
			info["level"] = level
		if conf.get("name"):
			info["name"]    = conf["name"]
		if conf.get("init") is not None:
			info["init"]    = conf["init"]
		if pin_type == "input" and conf.get("pullup"):
			info["pullup"]  = conf["pullup"]
		if conf.get("max"):
			info["max"]     = conf["max"]
		if pin_type not in ("vcc", "gnd"):
			info["reversed"] = reversed_flag
		if self.watches.get(gpio_str):
			info["watched"] = True
		return info

	def read_all(self):
		return {g: self.get_pin_info(g) for g in self.pin_configs}

	def read_pin(self, name_or_gpio):
		if name_or_gpio.lower() == "all":
			return self.read_all()
		gpio = self.resolve_gpio(name_or_gpio)
		if gpio is None:
			raise PinNotFound(f"Pin not found: {name_or_gpio}")
		info = self.get_pin_info(gpio)
		if info is None:
			raise ValueError(f"Could not read GP{name_or_gpio}")
		return info

	def write_pin(self, name_or_gpio, value, duration=None):
		gpio = self.resolve_gpio(name_or_gpio)
		if gpio is None:
			raise PinNotFound(f"Pin not found: {name_or_gpio}")
		if self._is_static(gpio):
			raise ValueError(f"GP{gpio} is vcc/gnd and cannot be written")

		conf          = self.pin_configs.get(gpio, {})
		reversed_flag = conf.get("reversed", False)
		pin           = self.pins.get(gpio)

		if pin is None or conf.get("type") != "output":
			if conf.get("type") not in (None, "output"):
				raise ValueError(f"GP{gpio} is configured as {conf.get('type')} and cannot be written")
			self._clear_watch(gpio)
			pin = machine.Pin(int(gpio), machine.Pin.OUT)
			self.pins[gpio] = pin
			self.pin_configs.setdefault(gpio, {})["type"] = "output"
			self._save_config()
			self.log(f"[GPIO] GP{gpio} auto-configured as output")

		prev = pin.value()
		self._cancel_revert(gpio)

		sval = str(value).lower()
		if sval == "toggle":
			pin.value(1 - pin.value())
		else:
			if sval in ("on", "off"):
				logical = 1 if sval == "on" else 0
			else:
				logical = 1 if int(value) != 0 else 0
			physical = (1 - logical) if reversed_flag else logical
			pin.value(physical)

		self.log(f"[GPIO] GP{gpio} written (physical={pin.value()})")
		self._save_status()

		if self.watches.get(gpio):
			self._trigger_webhook(gpio)

		max_dur   = conf.get("max")
		effective = (min(duration, max_dur) if duration is not None else max_dur) if max_dur else duration

		info = self.get_pin_info(gpio)
		if effective:
			info["duration"] = effective
			self._pending_reverts.append(
				(utime.ticks_add(utime.ticks_ms(), int(effective * 1000)), gpio, pin, prev))
		return info

	def _cancel_revert(self, gpio):
		self._pending_reverts = [r for r in self._pending_reverts if r[1] != gpio]

	def process_reverts(self):
		if not self._pending_reverts:
			return
		now  = utime.ticks_ms()
		keep = []
		for item in self._pending_reverts:
			expire, gpio_str, pin, restore = item
			if utime.ticks_diff(now, expire) < 0:
				keep.append(item)
				continue
			if self.pins.get(gpio_str) is pin:
				try:
					pin.value(restore)
					self.log(f"[GPIO] GP{gpio_str} reverted (duration expired)")
					self._save_status()
					if self.watches.get(gpio_str):
						self._trigger_webhook(gpio_str)
				except Exception:
					pass
		self._pending_reverts = keep

	def config_pin(self, name_or_gpio, update):
		gpio_str = self.resolve_gpio(name_or_gpio)
		if gpio_str is None:
			raise PinNotFound(f"Pin not found: {name_or_gpio}")

		self._cancel_revert(gpio_str)

		if update.get("type") == "remove":
			self._clear_watch(gpio_str)
			try:
				machine.Pin(int(gpio_str), machine.Pin.IN, None)
			except Exception:
				pass
			self.pins.pop(gpio_str, None)
			self.pin_configs.pop(gpio_str, None)
			self.status.pop(gpio_str, None)
			self._save_config()
			self.log(f"[GPIO] GP{gpio_str} config removed")
			return {"status": "removed"}

		prev_type      = (self.pin_configs.get(gpio_str) or {}).get("type")
		current        = dict(self.pin_configs.get(gpio_str, {}))
		effective_type = update.get("type") or current.get("type")

		for k, v in update.items():
			if k == "max"      and effective_type == "input":           continue
			if k == "reversed" and effective_type in ("vcc", "gnd"):    continue
			if k == "max" and v is not None:
				try: v = float(v)
				except Exception: pass
			if k == "init":
				v = _check_init(_norm_init(v))
			current[k] = v

		if effective_type in ("vcc", "gnd"):
			current.pop("pullup", None)
			current["reversed"] = False
			current.pop("watched", None)
			self._clear_watch(gpio_str)
		elif effective_type == "output":
			current.pop("pullup", None)
			if not current.get("max"):
				current.pop("max", None)
		elif effective_type == "input":
			current.pop("max", None)

		if current.get("watched") is not True:
			current.pop("watched", None)

		if prev_type and effective_type != prev_type and "init" not in update:
			current.pop("init", None)

		self.pin_configs[gpio_str] = current
		self._save_config()
		self._init_pin(gpio_str, current, was_output=(prev_type == "output"))
		self.log(f"[GPIO] GP{gpio_str} configured: {update}")
		return self.get_pin_info(gpio_str)

	def scan_all(self):
		result = []
		for gpio in PICO_GPIO_PINS:
			gpio_str = str(gpio)
			try:
				conf          = self.pin_configs.get(gpio_str, {})
				pin_type      = conf.get("type", "input")
				reversed_flag = conf.get("reversed", False) if pin_type not in ("vcc", "gnd") else False

				pin  = self.pins.get(gpio_str)
				probe = pin is None
				if probe:
					pin = machine.Pin(gpio, machine.Pin.IN, machine.Pin.PULL_UP)

				level = pin.value()
				value = (1 - level) if reversed_flag else level

				if probe:
					machine.Pin(gpio, machine.Pin.IN, None)

				entry = {"gpio": gpio, "level": level, "value": value, "type": pin_type}
				if conf.get("name"):
					entry["name"] = conf["name"]
				if pin_type == "input" and conf.get("pullup"):
					entry["pullup"] = conf["pullup"]
				if pin_type not in ("vcc", "gnd"):
					entry["reversed"] = reversed_flag
				result.append(entry)
			except Exception:
				pass
		return {"agent": self._agent_info(), "gpios": result}

	def get_watched_pins(self):
		watched = {g: self.get_pin_info(g) for g, w in self.watches.items() if w}
		bridges = self.config.get("bridges", {})
		return {"watched": watched,
		        "notifications": {"webhook": self.config.get("notifications", {}).get("webhook") or ""},
		        "bridges": {"matter":     bridges.get("matter") or "",
		                    "homekit":    bridges.get("homekit") or "",
		                    "homebridge": bridges.get("homebridge") or ""}}

	def _cpu_temperature(self):
		try:
			reading = machine.ADC(4).read_u16() * 3.3 / 65535
			return round(27 - (reading - 0.706) / 0.001721, 1)
		except Exception:
			return None

	def _get_os(self):
		try:
			u = os.uname()
			return f"{u.sysname} {u.release} MicroPython"
		except Exception:
			return "MicroPython"

	def _serial(self):
		try:
			return "".join("{:02x}".format(b) for b in machine.unique_id())
		except Exception:
			return ""

	def _default_name(self):
		serial = self._serial()
		return ("pico-" + serial[-4:]) if serial else "pico"

	def _agent_info(self):
		wlan = network.WLAN(network.STA_IF)
		connected = wlan.isconnected()
		ip = wlan.ifconfig()[0] if connected else "unknown"
		ssid, signal = "", None
		if connected:
			for field in ("essid", "ssid"):
				try:
					ssid = wlan.config(field) or ""
					if isinstance(ssid, bytes):
						ssid = ssid.decode()
					break
				except Exception:
					continue
			try:
				signal = wlan.status("rssi")
			except Exception:
				signal = None
		try:
			mac = ":".join("{:02x}".format(b) for b in wlan.config("mac"))
		except Exception:
			mac = ""
		return {"platform": PLATFORM,
		        "os": self._get_os(),
		        "host": "pico",
		        "serial": self._serial(),
		        "ip": ip,
		        "mac": mac,
		        "wifi": ssid,
		        "signal": signal,
		        "cpu_temperature": self._cpu_temperature(),
		        "uptime": _format_uptime(utime.time() - _BOOT_TIME),
		        "version": VERSION,
		        "config_updated": self._config_updated()}

	def _config_updated(self):
		try:
			t = utime.localtime(os.stat(CONFIG_FILE)[8])
			if t[0] < 2023:
				return ""
			return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*t[:6])
		except Exception:
			return ""

	def get_config(self):
		notif    = self.config.get("notifications", {})
		bridges  = self.config.get("bridges", {})
		settings = self.config.get("settings", {})
		cfg = {
			"name":          self.config.get("name") or self._default_name(),
			"agent":         self._agent_info(),
			"notifications": {
				"webhook":     notif.get("webhook") or "",
				"webhook_key": notif.get("webhook_key") or "",
			},
			"bridges": {
				"matter":       bridges.get("matter") or "",
				"matter_key":   bridges.get("matter_key") or "",
				"homekit":      bridges.get("homekit") or "",
				"homekit_key":  bridges.get("homekit_key") or "",
				"homebridge":     bridges.get("homebridge") or "",
				"homebridge_key": bridges.get("homebridge_key") or "",
			},
			"settings": {
				"logs_enabled": settings.get("logs_enabled", True),
				"log_days":     settings.get("log_days", DEFAULT_LOG_DAYS),
			},
			"sensors":       self.config.get("sensors", {}),
		}
		cfg["gpios"] = self.pin_configs
		return cfg

	def config_sensor(self, name, update):
		if update.get("remove"):
			existed = self.config["sensors"].pop(name, None) is not None
			self._save_config()
			self.log(f"[Sensor] {name} removed")
			return {"status": "removed" if existed else "not found"}
		script = update.get("script")
		if not script:
			raise ValueError("Configure 'script', or 'remove': true to delete the sensor")
		err = _sensor_script_parts(script)[0]
		if err:
			raise ValueError(err)
		self.config["sensors"][name] = {"script": script}
		self._save_config()
		self.log(f"[Sensor] {name} configured: {script}")
		return {"name": name, "script": script}

	def _read_sensor_value(self, name, conf):
		err, script_file, args = _sensor_script_parts(conf.get("script"))
		if err:
			return {name: "blocked: " + err}
		try:
			src = open(script_file).read()
		except OSError:
			return {name: "script not found"}
		try:
			ns = {"args": args}
			exec(src, ns)
			if callable(ns.get("read")):
				val = ns["read"](*args)
			elif "result" in ns:
				val = ns["result"]
			else:
				val = None
		except Exception:
			return {name: "script failed or did not produce output"}
		if val is None:
			return {name: "script failed or did not produce output"}
		if isinstance(val, dict) and len(val) == 1:
			val = list(val.values())[0]
		return {name: val}

	def read_sensor(self, name):
		conf = self.config["sensors"].get(name)
		if conf is None:
			raise SensorNotFound(f"Sensor not found: {name}")
		return self._read_sensor_value(name, conf)

	def read_sensors(self):
		result = {}
		for name, conf in self.config["sensors"].items():
			try:
				result.update(self._read_sensor_value(name, conf))
			except Exception as e:
				result[name] = f"error: {e}"
		return result

	def update_config(self, updates):
		updates = _normalize_config(updates)
		if not HTTPS_WEBHOOK_OK and _is_https((updates.get("notifications") or {}).get("webhook")):
			raise ValueError(HTTPS_WEBHOOK_ERROR)
		self._backup_config()
		for k, v in updates.items():
			if k == "api_key":
				if v:
					self.config["api_key"] = v
			elif k in ("notifications", "bridges"):
				allowed = (("webhook", "webhook_key") if k == "notifications"
				           else ("matter", "matter_key", "homekit", "homekit_key",
				                 "homebridge", "homebridge_key"))
				group = self.config.setdefault(k, {})
				for gk, gv in (v or {}).items():
					if gk in allowed and gv is not None:
						group[gk] = None if gv == "" else gv
			elif k == "settings":
				settings = self.config.setdefault("settings", {})
				for sk, sv in (v or {}).items():
					if sv is None:
						continue
					if sk == "log_days":
						days = max(0, min(int(sv), 30))
						settings["log_days"] = days
						if days == 0:
							self.clear_logs()
					elif sk == "logs_enabled":
						settings[sk] = sv
			elif k == "name":
				self.config[k] = v
		self._save_config()
		return self.get_config()

	def load_config(self, new_config):
		self._backup_config()
		new_config = _normalize_config(new_config)
		notif = new_config.get("notifications")
		if not HTTPS_WEBHOOK_OK and isinstance(notif, dict) and _is_https(notif.get("webhook")):
			self.log(f"[Config] Dropped https webhook URL (not supported on this board): {notif.get('webhook')}")
			notif["webhook"] = None
		new_config["api_key"] = new_config.get("api_key", self.config.get("api_key", DEFAULT_API_KEY))
		new_config.setdefault("gpios", {})
		new_config.setdefault("sensors", {})
		new_config.setdefault("settings", {})
		new_config["settings"].setdefault("log_days", DEFAULT_LOG_DAYS)
		with open(CONFIG_FILE, "w") as f:
			ujson.dump(new_config, f)
		self._reload()
		return self.get_config()

	def _reload(self):
		for gpio_str in list(self.watches):
			self._clear_watch(gpio_str)
		self.pins.clear()
		self._load_config()
		self._load_status()
		self._initialize_pins()

	def log(self, msg):
		if not self.config.get("settings", {}).get("logs_enabled", True):
			return
		try:
			t     = utime.localtime()
			entry = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} {}".format(*t[:6], msg)
		except Exception:
			entry = msg
		self._log.append(entry)
		if len(self._log) > LOG_MAX:
			self._log.pop(0)

	def get_logs(self):
		return self._log[-LOG_RETURN:]

	def clear_logs(self):
		self._log.clear()

	def maybe_cleanup_logs(self):
		now = utime.time()
		if now - self._last_log_cleanup < 3600:
			return
		self._last_log_cleanup = now
		days   = self.config.get("settings", {}).get("log_days", DEFAULT_LOG_DAYS)
		offset = 3600 if days == 0 else days * 86400
		cutoff = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*utime.localtime(now - offset)[:6])
		self._log = [e for e in self._log if len(e) <= 19 or e[:19] >= cutoff]

	def _notify_targets(self):
		targets = []
		notif = self.config.get("notifications", {})
		if notif.get("webhook"):
			targets.append((notif.get("webhook"), notif.get("webhook_key")))
		bridges = self.config.get("bridges", {})
		for field in ("matter", "homekit", "homebridge"):
			url = bridges.get(field)
			if url:
				targets.append((url, bridges.get(field + "_key") or None))
		return targets

	def _trigger_webhook(self, gpio_str):
		targets = self._notify_targets()
		if targets:
			for url, api_key in targets:
				self._webhook_queue.append((gpio_str, 0, url, api_key, utime.ticks_ms()))
		else:
			self.log("[Webhook] Not sent (no notifications.webhook configured), GPIO: " + gpio_str)

	def queue_boot_webhook(self):
		for url, api_key in self._notify_targets():
			self._webhook_queue.append((None, 0, url, api_key, utime.ticks_ms()))

	def process_webhooks(self):
		if not self._webhook_queue:
			return
		now = utime.ticks_ms()
		for i, entry in enumerate(self._webhook_queue):
			if utime.ticks_diff(now, entry[4]) >= 0:
				gpio_str, attempts, url, api_key, _ = self._webhook_queue.pop(i)
				break
		else:
			return
		if not url:
			return
		try:
			payload = {"agent": self.config.get("name") or self._default_name()}
			if gpio_str is None:
				payload["note"] = "Up and running"
			else:
				info = self.get_pin_info(gpio_str)
				if info:
					payload.update(info)
			if attempts == 0:
				self.log(f"[Webhook] {'boot' if gpio_str is None else 'GPIO ' + gpio_str} → {url}")
			self._webhook_sender.send(url, payload, api_key)
		except Exception as e:
			self.log(f"[WEBHOOK] GP{gpio_str} failed: {type(e).__name__} {repr(e)}")
			if attempts < WEBHOOK_RETRIES:
				self._webhook_queue.append((gpio_str, attempts + 1, url, api_key,
				                            utime.ticks_add(utime.ticks_ms(), WEBHOOK_RETRY_MS)))

### WiFi

def _read_wifi(path="wifi.txt"):
	settings = {}
	try:
		with open(path) as f:
			for line in f:
				line = line.strip()
				if not line or line.startswith("#") or "=" not in line:
					continue
				key, _, val = line.partition("=")
				settings[key.strip()] = val.strip().strip('"').strip("'")
	except OSError:
		pass
	return settings

def connect_wifi():
	settings = _read_wifi()
	ssid     = settings.get("SSID")     or FALLBACK_SSID
	password = settings.get("PASSWORD") or FALLBACK_PASSWORD

	wlan = network.WLAN(network.STA_IF)
	wlan.active(True)
	if not wlan.isconnected():
		wlan.connect(ssid, password)
		for _ in range(60):
			if wlan.isconnected():
				break
			utime.sleep(0.5)

	if not wlan.isconnected():
		machine.reset()

	return wlan.ifconfig()[0]

def sync_time():
	try:
		import ntptime
		ntptime.settime()
	except Exception:
		pass

_BOOT_TIME = utime.time()

def _format_uptime(seconds):
	s = int(seconds)
	d, rem = divmod(s, 86400)
	h, rem = divmod(rem, 3600)
	m, sec = divmod(rem, 60)
	if d:
		return f"{d}d {h}h"
	if h:
		return f"{h}h {m}m"
	if m:
		return f"{m}m"
	return f"{sec}s"

def _gateway_alive(wlan):
	try:
		gw = wlan.ifconfig()[2]
		s  = usocket.socket()
		s.settimeout(2)
		t0 = utime.ticks_ms()
		try:
			s.connect((gw, 80))
			return True
		except OSError:
			return utime.ticks_diff(utime.ticks_ms(), t0) < 1500
		finally:
			s.close()
	except Exception:
		return True

### HTTP helpers

def _drain(conn, data, budget=64 * 1024, quiet_ms=1000, max_ms=3000):
	remaining = -1
	sep = data.find(b"\r\n\r\n")
	if sep != -1:
		for line in data[:sep].decode("utf-8", "replace").split("\r\n")[1:]:
			if line.lower().startswith("content-length:"):
				try:
					remaining = int(line.split(":", 1)[1].strip()) - (len(data) - sep - 4)
				except Exception:
					remaining = -1
				break
	if remaining == 0:
		return

	conn.settimeout(quiet_ms / 1000)
	hard = utime.ticks_add(utime.ticks_ms(), max_ms)
	try:
		while budget > 0 and remaining != 0 and utime.ticks_diff(hard, utime.ticks_ms()) > 0:
			try:
				chunk = conn.recv(1024)
			except Exception:
				return
			if not chunk:
				return
			budget -= len(chunk)
			if remaining > 0:
				remaining = max(0, remaining - len(chunk))
	finally:
		try: conn.settimeout(2)
		except Exception: pass

def _recv_request(conn):
	data = b""
	conn.settimeout(2)
	deadline = utime.ticks_add(utime.ticks_ms(), 6000)
	while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
		try:
			chunk = conn.recv(1024)
		except OSError as e:
			if e.args and e.args[0] in (uerrno.ETIMEDOUT, uerrno.EAGAIN):
				continue
			break
		except Exception:
			break
		if not chunk:
			break
		data += chunk
		if len(data) > MAX_REQUEST_BYTES:
			_drain(conn, data)
			return b""
		sep = data.find(b"\r\n\r\n")
		if sep != -1:
			cl = 0
			for line in data[:sep].decode("utf-8", "replace").split("\r\n")[1:]:
				if line.lower().startswith("content-length:"):
					try: cl = int(line.split(":", 1)[1].strip())
					except Exception: pass
					break
			if cl > MAX_REQUEST_BYTES:
				_drain(conn, data)
				return b""
			if len(data) - sep - 4 >= cl:
				return data
	return b""

def _parse_request(raw):
	if not raw:
		return None
	try:
		sep = raw.find(b"\r\n\r\n")
		if sep == -1:
			return None
		lines = raw[:sep].decode("utf-8", "replace").split("\r\n")
		parts = lines[0].split(" ", 2)
		if len(parts) < 2:
			return None
		method = parts[0].upper()
		path   = parts[1].split("?")[0]
		hdrs   = {}
		for line in lines[1:]:
			if ":" in line:
				k, v = line.split(":", 1)
				hdrs[k.strip().lower()] = v.strip()
		body = {}
		tail = raw[sep + 4:]
		if tail:
			try:
				parsed = ujson.loads(tail)
				body = parsed if isinstance(parsed, dict) else None
			except Exception:
				body = None
		return method, path, hdrs, body
	except Exception:
		return None

def _send_empty(conn, status=404):
	header = (
		f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'Error')}\r\n"
		f"Content-Length: 0\r\n"
		f"Connection: close\r\n\r\n"
	).encode()
	try:
		conn.sendall(header)
	except Exception:
		pass

def _send_json(conn, data, status=200):
	body = ujson.dumps(data).encode()
	header = (
		f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'Error')}\r\n"
		f"Content-Type: application/json\r\n"
		f"Content-Length: {len(body)}\r\n"
		f"Connection: close\r\n\r\n"
	).encode()
	try:
		conn.sendall(header + body)
	except Exception:
		pass

### Self-upgrade (POST /upgrade)

def _rm(path):
	try:
		os.remove(path)
	except Exception:
		pass

def _verify_download(path, size):
	try:
		with open(path, "rb") as f:
			head = f.read(256)
			if b"main.py" not in head or b"import" not in head:
				return "head banner/imports missing"
			f.seek(0)
			total = 0
			tail = b""
			while True:
				chunk = f.read(512)
				if not chunk:
					break
				if b"\x00" in chunk:
					return "binary data in file"
				total += len(chunk)
				tail = (tail + chunk)[-64:]
	except Exception as e:
		return "unreadable: " + str(e)
	if total != size:
		return f"size mismatch (wrote {size}, read back {total})"
	if not tail.rstrip().endswith(b"main()"):
		return "file does not end with main() (truncated)"
	return None

def _http_download(url, dest):
	proto, rest = url.split("://", 1)
	host_port, _, path = rest.partition("/")
	host, _, port = host_port.partition(":")
	port = int(port) if port else (443 if proto == "https" else 80)
	if proto == "https" and not _HAS_SSL:
		raise OSError("no ssl module")
	gc.collect()
	s = usocket.socket()
	s.settimeout(15)
	try:
		s.connect(usocket.getaddrinfo(host, port)[0][-1])
		if proto == "https":
			ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
			ctx.verify_mode = ssl.CERT_NONE
			s = ctx.wrap_socket(s, server_hostname=host)
		s.write(("GET /{} HTTP/1.1\r\nHost: {}\r\nUser-Agent: ctrlpi-pico\r\n"
		         "Accept: */*\r\nConnection: close\r\n\r\n").format(path, host).encode())
		line = s.readline()
		if not line or not line.startswith(b"HTTP/"):
			raise OSError("bad response")
		status = int(line.split(b" ")[1])
		if status != 200:
			raise OSError(f"HTTP {status}")
		cl, chunked = None, False
		while True:
			line = s.readline()
			if not line or line == b"\r\n":
				break
			ll = line.lower()
			if ll.startswith(b"content-length:"):
				cl = int(line.split(b":", 1)[1])
			elif ll.startswith(b"transfer-encoding:") and b"chunked" in ll:
				chunked = True
		total = 0
		with open(dest, "wb") as f:
			if chunked:
				complete = False
				while True:
					size_line = s.readline()
					if not size_line:
						break
					size = int(size_line.strip().split(b";")[0], 16)
					if size == 0:
						complete = True
						break
					left = size
					while left > 0:
						chunk = s.read(min(left, 512))
						if not chunk:
							raise OSError("short read")
						f.write(chunk); total += len(chunk); left -= len(chunk)
					s.readline()
				if not complete:
					raise OSError("truncated chunked body")
			elif cl is not None:
				left = cl
				while left > 0:
					chunk = s.read(min(left, 512))
					if not chunk:
						break
					f.write(chunk); total += len(chunk); left -= len(chunk)
				if left > 0:
					raise OSError("short read")
			else:
				while True:
					chunk = s.read(512)
					if not chunk:
						break
					f.write(chunk); total += len(chunk)
		return total
	finally:
		try:
			s.close()
		except Exception:
			pass

def _do_upgrade(manager):
	tmp = MAIN_FILE + ".new"
	bak = MAIN_FILE + ".backup"
	manager.log("[UPGRADE] downloading " + UPGRADE_URL)
	try:
		n = _http_download(UPGRADE_URL, tmp)
	except Exception as e:
		_rm(tmp)
		return (False, {"error": "download failed: " + str(e)}, 502)
	if n < 2000:
		_rm(tmp)
		return (False, {"error": f"downloaded file too small ({n} bytes)"}, 502)
	bad = _verify_download(tmp, n)
	if bad:
		_rm(tmp)
		return (False, {"error": "downloaded file failed sanity check: " + bad}, 502)
	try:
		_rm(bak)
		os.rename(MAIN_FILE, bak)
		os.rename(tmp, MAIN_FILE)
	except Exception as e:
		_rm(tmp)
		return (False, {"error": "install failed: " + str(e)}, 500)
	bad = _verify_download(MAIN_FILE, n)
	if bad:
		try:
			try:
				os.rename(bak, MAIN_FILE)
			except Exception:
				_rm(MAIN_FILE)
				os.rename(bak, MAIN_FILE)
			recovery = "restored previous main.py"
		except Exception as e:
			recovery = "COULD NOT restore backup: " + str(e)
		manager.log(f"[UPGRADE] post-swap check failed ({bad}); {recovery}")
		return (False, {"error": "installed file failed verification: " + bad,
		                "recovery": recovery}, 500)
	manager.log(f"[UPGRADE] main.py replaced and verified ({n} bytes); resetting")
	return (True, {"status": "upgrading"}, 200)

### Request handler

def handle_request(conn, manager):
	BLINKER.blink(2)
	raw    = _recv_request(conn)
	parsed = _parse_request(raw)

	if not parsed:
		_send_json(conn, {"error": "Bad request"}, 400)
		conn.close()
		return

	method, path, hdrs, body = parsed
	if manager.config.get("settings", {}).get("logs_enabled", True) and not (method == "GET" and path == "/hello"):
		manager.log(f"[API] Received {method} {path}")
	if body is None:
		_send_json(conn, {"error": "Request body failed validation"}, 422)
		conn.close()
		return
	try:
		if method == "GET" and path == "/hello":
			_send_json(conn, {"name": manager.config.get("name") or manager._default_name()})
			conn.close()
			return

		if hdrs.get("api-key") != manager.config.get("api_key", DEFAULT_API_KEY):
			_send_json(conn, {"error": "Invalid or missing Api-Key header"}, 403)
			conn.close()
			return

		if   method == "GET"  and path == "/gpio/read":
			_send_json(conn, manager.read_all())
		elif method == "GET"  and path.startswith("/gpio/read/"):
			_send_json(conn, manager.read_pin(path[len("/gpio/read/"):]))
		elif method == "POST" and path.startswith("/gpio/write/"):
			if body.get("value") is None:
				_send_json(conn, {"error": "Request body failed validation"}, 422)
				conn.close()
				return
			_send_json(conn, manager.write_pin(path[len("/gpio/write/"):], body.get("value"), body.get("duration")))
		elif method == "POST" and path.startswith("/gpio/config/"):
			_send_json(conn, manager.config_pin(path[len("/gpio/config/"):], body))
		elif method == "GET"  and path == "/gpio/watched":
			_send_json(conn, manager.get_watched_pins())
		elif method == "POST" and path.startswith("/sensor/config/"):
			_send_json(conn, manager.config_sensor(path[len("/sensor/config/"):], body))
		elif method == "GET"  and path == "/sensor/read":
			_send_json(conn, manager.read_sensors())
		elif method == "GET"  and path.startswith("/sensor/read/"):
			_send_json(conn, manager.read_sensor(path[len("/sensor/read/"):]))
		elif method == "GET"  and path == "/gpio/scan":
			_send_json(conn, manager.scan_all())
		elif method == "GET"  and path == "/config/read":
			_send_json(conn, manager.get_config())
		elif method == "POST" and path == "/config/update":
			_send_json(conn, manager.update_config(body))
		elif method == "POST" and path == "/config/load":
			_send_json(conn, manager.load_config(body))
		elif method == "POST" and path == "/upgrade":
			ok, payload, status = _do_upgrade(manager)
			_send_json(conn, payload, status)
			if ok:
				conn.close()
				manager._flush_all()
				utime.sleep_ms(500)
				machine.reset()
		elif method == "POST" and path == "/restart":
			reboot = bool(body.get("reboot", False))
			_send_json(conn, {"status": "rebooting" if reboot else "restarting"})
			conn.close()
			manager._flush_all()
			if reboot:
				utime.sleep_ms(500)
				machine.reset()
			else:
				manager._reload()
				manager.log("[BOOT] In-process restart via /restart")
		elif method == "GET"  and path == "/logs":
			_send_json(conn, {"logs": manager.get_logs()})
		else:
			_send_empty(conn, 404)

	except PinNotFound:
		_send_empty(conn, 404)
	except Exception as e:
		try:
			_send_json(conn, {"error": str(e)}, 400)
		except Exception:
			pass

	conn.close()

### Boot

def main():
	BLINKER.start_fast()
	ip      = connect_wifi()
	sync_time()
	global _BOOT_TIME
	_BOOT_TIME = utime.time()
	manager = GPIOManager()
	manager.queue_boot_webhook()

	addr   = usocket.getaddrinfo("0.0.0.0", PORT)[0][-1]
	server = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
	server.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
	for attempt in range(3):
		try:
			server.bind(addr)
			break
		except OSError as e:
			if e.args[0] == 98:
				if attempt == 2:
					manager.log("[BOOT] Port 8314 in use, hard resetting...")
					utime.sleep(0.5)
					machine.reset()
				manager.log(f"[BOOT] Port in use, retrying ({attempt+1}/2)...")
				utime.sleep(2)
			else:
				raise
	server.listen(3)
	server.settimeout(1)

	manager.log(f"[BOOT] Listening on {ip}:{PORT}")

	wlan         = network.WLAN(network.STA_IF)
	wifi_last_ok = utime.time()
	net_last_ok  = utime.time()
	last_probe   = 0
	boot_blink   = True

	while True:
		try:
			manager.maybe_cleanup_logs()
			manager.maybe_flush()
			manager.process_reverts()
			manager.process_webhooks()
			manager._webhook_sender.maybe_expire()
			if boot_blink and not any(e[0] is None for e in manager._webhook_queue):
				boot_blink = False
				BLINKER.stop_fast()
			server.settimeout(0.05 if manager._pending_reverts else 1)
			now = utime.time()
			if wlan.isconnected():
				wifi_last_ok = now
				if now - net_last_ok > WIFI_PROBE_SECS and now - last_probe >= 15:
					last_probe = now
					if _gateway_alive(wlan):
						net_last_ok = now
					elif now - net_last_ok > WIFI_PROBE_SECS + 60:
						manager._flush_all()
						machine.reset()
			elif now - wifi_last_ok > WIFI_DEAD_SECS:
				manager._flush_all()
				machine.reset()
			conn, _ = server.accept()
			net_last_ok = utime.time()
			handle_request(conn, manager)
		except OSError:
			pass
		except Exception as e:
			manager.log(f"[ERROR] Server: {type(e).__name__} {repr(e)}")

main()
