# Raspberry Pi Pico GPIO API

[![Platform: Raspberry Pi Pico W](https://img.shields.io/badge/platform-Raspberry%20Pi%20Pico-006400.svg)](#install-on-your-pico-w)
[![Version 0.9.23](https://img.shields.io/badge/version-0.9.23-blue.svg)](https://github.com/ctrlpi/pico-gpio-api/tags)
[![MicroPython](https://img.shields.io/badge/runtime-MicroPython-blue.svg)](#install-on-your-pico-w)
[![Auth: Api-Key](https://img.shields.io/badge/auth-Api--Key-orange.svg)](#authentication)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Status: Beta](https://img.shields.io/badge/status-Beta-red.svg)

A MicroPython server that exposes Raspberry Pi Pico W GPIO control through a REST API, allowing HTTP clients to trigger outputs and read inputs remotely.

Runs on a Raspberry Pi Pico W / 2 W, secured with API key authentication, GPIO via `machine.Pin`.

It is the Pico port of [pi-gpio-api](https://github.com/ctrlpi/pi-gpio-api): same endpoints, same wire format, same API-key authentication, so clients, testers, and integrations target a Pi or a Pico interchangeably, and the two projects release and version in lockstep.

> Looking for AI-agent / MCP control, Apple HomeKit or Google Home? See the [Related projects](#related-projects) below that drive one or more of these API servers (Pi or Pico) over their REST endpoints.

## Install on your Pico W

**Requirements:** MicroPython firmware flashed on a Raspberry Pi Pico W or Pico 2 W ([micropython.org/download/RPI_PICO_W](https://micropython.org/download/RPI_PICO_W/)), and a Mac/PC to run Pico Bay or `install.sh` from.

Pico Bay or `install.sh` always runs on your Mac/PC, never on the Pico itself (MicroPython has no shell to run it in). Plug the Pico in over USB and run it.

### Option 1: Pico Bay (easiest)

```bash
npx @ctrlpi/pico-bay
```

Connect your board and choose **Install ctrlPi API** from the top right menu. You will be prompted to fill in your API key and WiFi information.

You can also install MicroPython with Pico Bay by connecting a Pico in BOOTSEL mode, or choosing **Reboot to BOOTSEL** from the top right menu.

### Option 2: curl

One-liner, no git required - downloads the release files into `./pico-gpio-api`:

```bash
curl -fsSL https://raw.githubusercontent.com/ctrlpi/pico-gpio-api/main/install.sh | bash
```


`install.sh` checks the *connected device* for an existing `config.json`/`wifi.txt` and asks only for what's missing: an API key + agent name (a blank name keeps the device's own unique `pico-<serial4>` default), and/or WiFi SSID/password. It then copies everything onto every Pico on USB via `mpremote` and resets it. The local `config.json`/`wifi.txt` staged for that copy are deleted right after, so credentials don't linger on disk.

Re-running the downloaded copy directly (`./install.sh`) is safe any time: it redeploys `main.py`, skips both prompts once the device has them, and never overwrites the device's own `config.json` (see [Files](#files) below) - it won't re-download the files unless run with `./install.sh --upgrade`.

- `./install.sh --config`: re-ask for the API key/agent name and push it, even if the device already has `config.json`.
- `./install.sh --wifi`: re-ask for WiFi SSID/password and push it, even if the device already has `wifi.txt`.
- `./install.sh --upgrade`: re-download the release files even when run directly (not piped).

### Option 3: git clone

```bash
git clone https://github.com/ctrlpi/pico-gpio-api.git
cd pico-gpio-api
./install.sh
```

Or skip `install.sh` entirely and copy the files by hand to the Pico: `main.py` (via Thonny or `mpremote`), and a `wifi.txt` (see `wifi.txt.example`):

```
SSID = YourNetwork
PASSWORD = YourPassword
```

`main.py` parses this file directly (one `KEY = value` per line). If absent, the `FALLBACK_SSID` / `FALLBACK_PASSWORD` constants in `main.py` are used. The API key defaults to `"your-secret-key"`; change it via `POST /config/update` or by editing `config.json`.

### Run automatically at boot

Nothing to set up: the firmware auto-runs `main.py` at power-on, so once the files are on the device the server starts by itself every boot.

The server binds **`0.0.0.0`** (all interfaces) and port **8314**, so it is reachable on your local network; keep it behind a trusted LAN (see the No-TLS note under Authentication).

## Managing files on the Pico (`pico.sh`)

A small utility to help inspect and manage files on a connected Pico over USB (via `mpremote`, same venv `install.sh` sets up). It's not part of the deploy flow (use `install.sh` for that); this is for poking around a device you already set up: checking what's on it, pulling a file off for a look, or wiping it for a clean start.

```bash
./pico.sh                 # show the command list
./pico.sh list            # list files on the Pico (size, date, name)
./pico.sh model           # print the Pico model and MicroPython version
./pico.sh <file>          # print a file's contents, e.g. ./pico.sh config.json
./pico.sh get <file>      # copy <file> from the Pico into the current directory
./pico.sh put <file>      # copy a local <file> onto the Pico
./pico.sh rm <file>       # delete <file> on the Pico
./pico.sh clean           # delete ALL files on the Pico (asks to confirm)
```

It interrupts the running `main.py` to talk to the board over USB, so it resets the Pico on exit to resume the agent.

## Try the API

Replace `<pico-host-addr>` with the device address and `your-secret-key` with your API key.

#### 1. Health check

```bash
curl http://<pico-host-addr>:8314/hello
```

#### 2. Read all configured pins

```bash
curl http://<pico-host-addr>:8314/gpio/read -H "Api-Key: your-secret-key"
```

#### 3. Write to a pin

```bash
curl -X POST http://<pico-host-addr>:8314/gpio/write/26 \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"value": 1, "duration": 2}'
```

#### 4. Configure a pin (relay, reversed, max 1 hour)

```bash
curl -X POST http://<pico-host-addr>:8314/gpio/config/26 \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"name": "relay", "type": "output", "reversed": true, "max": 3600}'
```

#### 5. Scan all usable hardware pins (GP0–GP28, minus the WiFi-reserved ones)

```bash
curl http://<pico-host-addr>:8314/gpio/scan -H "Api-Key: your-secret-key"
```

#### 6. Configure a watched input (fires a webhook on change)

```bash
curl -X POST http://<pico-host-addr>:8314/gpio/config/6 \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"type": "input", "watched": true}'
```

#### 7. Change the webhook URL

```bash
curl -X POST http://<pico-host-addr>:8314/config/update \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://example.com/webhook"}'
```

#### 8. Configure a sensor

```bash
# cpu-temp.py is the example script install.sh seeds into scripts/
curl -X POST http://<pico-host-addr>:8314/sensor/config/cpu_temp \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"script": "cpu-temp.py"}'
```

#### 9. Read a sensor

```bash
curl -H "Api-Key: your-secret-key" http://<pico-host-addr>:8314/sensor/read/cpu_temp
```

## Authentication

All endpoints require an `Api-Key` header except `/hello`.

```
Api-Key: your-secret-key
```

> **No TLS.** The server speaks plain HTTP, so the key travels as plaintext on the wire. That's fine on a trusted LAN, but don't expose port 8314 directly to the internet. If you need remote access, tunnel it.

## REST API

Identical wire format and behavior to [pi-gpio-api](https://github.com/ctrlpi/pi-gpio-api): a matching `version` in `/config/read` means matching API behavior. The Pico has no FastAPI layer, so there are no interactive `/docs` or `/redoc` (see [Differences from the Pi version](#differences-from-the-pi-version)).

## Health

A single unauthenticated endpoint for discovery and liveness checks; it reports the agent's name.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/hello` | none | Returns `{"name": "agent-name"}`, used for discovery and health checks. If name is not configured, it will return `pico-<serial4>` (e.g. `pico-1a2b`), built from the last 4 of the chip UID. |

## GPIO

The core of the API: read, write, configure, and watch GPIO pins. Every pin is addressed by its GP number or a configured name alias.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/gpio/read` | Read all configured pins |
| `GET` | `/gpio/read/{name_or_gpio}` | Read a single pin by name alias or GP number |
| `POST` | `/gpio/write/{name_or_gpio}` | Write a value to a pin |
| `POST` | `/gpio/config/{name_or_gpio}` | Configure a pin (name, type, init, pullup, max, reversed, watched) |
| `GET` | `/gpio/watched` | List all currently watched pins, the active webhook URL, and the `bridges` callback URLs (URLs only, never the keys) |
| `GET` | `/gpio/scan` | Scan all usable pins: GP0–GP28 minus GP23/24/25/29, which are wired to the CYW43 WiFi chip (touching them drops the wireless link; they're also rejected with a `400` on read/write/config and stripped from loaded configs). Returns `level` (the physical state) and `value` (logical). Also returns the live `agent` block; the pin map is keyed `gpios`. |

### Request Bodies

#### `POST /gpio/write/{name_or_gpio}`

```json
{
  "value": 1,
  "duration": 2.5
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `value` | `0`, `1`, `"on"`, `"off"`, `"toggle"` | Value to write. `"on"`/`"off"` are word spellings of `1`/`0`, `"toggle"` flips the current state. Strings are case-insensitive. A JSON boolean (`true`/`false`) also works, coerced to `1`/`0`. Any other integer counts as `1` if nonzero, `0` only if exactly `0` (so a negative value like `-1`, used by some systems for true, also means "on"). |
| `duration` | float (seconds) | Optional. Pulse the pin for this many seconds, then revert. If the pin has `max` configured, a larger `duration` is clamped to `max`, and a write **without** `duration` uses `max` as the duration (it pulses and reverts, it does not latch). |

Writing to an **unconfigured** pin auto-configures it as an output first. A pin already configured as anything else (`input`, `vcc`, `gnd`) is never silently flipped; the write returns `400`. Reconfigure it as `output` via `/gpio/config` first.

#### `POST /gpio/config/{name_or_gpio}`

```json
{
  "name": "relay",
  "type": "output",
  "init": 0,
  "pullup": "up",
  "reversed": true,
  "max": 5.0,
  "watched": true
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `name` | string | Name alias for the pin, usable in place of the GP number in all endpoints. |
| `type` | `input`, `output`, `vcc`, `gnd`, `remove` | Pin direction. `vcc`/`gnd` are permanently driven and cannot be written or watched. `remove` deletes the pin config. Changing type clears fields that no longer apply (see below). |
| `init` | `0`, `1`, `"on"`, `"off"`, `"last"`, `"restore"` | Startup behavior, also applied on every live `/gpio/config` call. **Outputs**: drive to value. Inputs: compare to reference and fire on mismatch. `"on"`/`"off"` are word spellings of `1`/`0`, and `"restore"` is a synonym for `"last"`, which uses the value from the last run. Strings are case-insensitive. A JSON boolean (`true`/`false`) also works, coerced to `1`/`0`. Any other integer counts as `1` if nonzero, `0` only if exactly `0` (so `2` and `-1` both mean "on", matching `/gpio/write`). All of this is stored canonically, so `/config/read` and `/gpio/read` always answer `0`, `1` or `"last"`. Unknown words are **rejected**, not stored and ignored. On an input the reference is compared against the pin's reported (logical) value, so `pullup` and `reversed` are already accounted for. If omitted on an output: a pin that's already configured as output keeps its current value unchanged; a pin that's *newly* becoming an output (was `input`, `vcc`, `gnd` or unconfigured) starts at `0`. Cleared automatically when the pin's `type` changes. |
| `pullup` | `"up"`, `"down"`, `"none"` | Pull resistor (inputs only). Cleared automatically when type is set to `output`, `vcc`, or `gnd`. |
| `max` | float (seconds) | Maximum pulse duration (outputs only). Writes with a larger `duration` are clamped to it, and writes with **no** `duration` use it as the duration (see `POST /gpio/write` above). Cleared automatically when type is set to `input` or when type is set to `output` if the value is `0` or unset. |
| `reversed` | boolean | Inverts the logical value. **Output**: writes the boolean flip to the physical GP pin (physical HIGH = logical 0). **Input**: flips the reported value. Cleared automatically when type is set to `vcc` or `gnd`. |
| `watched` | boolean | `true` to send a webhook on every write (outputs) or state change (inputs); `false` to stop. Cleared automatically when type is set to `vcc` or `gnd`. Webhooks are only sent when a `webhook_url` is configured. With none set, each watched event is recorded in the agent log instead (see [Webhooks](#webhooks)). |

## Sensors

A **sensor** is a named reading the agent computes on demand by running a script, a way to surface values GPIO pins can't carry (I2C sensors, 1-Wire probes, an on-chip metric, the output of any MicroPython module).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sensor/read` | Read every configured sensor, merged into one JSON object |
| `GET` | `/sensor/read/{name}` | Read one sensor by name |
| `POST` | `/sensor/config/{name}` | Configure a named sensor |

### Request Bodies

#### `POST /sensor/config/{name}`

```json
{
  "script": "cpu-temp.py"
}
```

Or, to delete the sensor:

```json
{
  "remove": true
}
```

Sensors are pure config plus on-demand reads: no pins, no background polling, no extra persisted state beyond `config.json`.

### Configure

Scripts live in the **`scripts/` folder** on the Pico's filesystem. `install.sh` creates it and seeds it with a short `cpu-temp.py` example; upload your own with `./pico.sh put scripts/weather.py` (the agent itself never creates the folder - with no folder, every sensor just reads `script not found`).

A Pico has **no shell**, so unlike the Pi a `script` sensor is a MicroPython `.py` file the agent execs in-process. Scripts can have arguments as `"<file.py> [args...]"`, where the file is a **bare filename** in `scripts/`: no paths, no `..`, no leading dot, no subfolders. The file should either define a `read()` function (called as `read(*args)`) or set a module-level `result` (with the arg list also available as `args` in the namespace), so one script can be reused with different params. Arguments may contain letters and digits only. The returned value can be any JSON-serializable object: a dict, number, or string.

```bash
# The same script with arguments (letters and digits only)
curl -X POST http://<pico-host-addr>:8314/sensor/config/zone1 \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"script": "weather.py zone1"}'
```

`scripts/` is the **only** folder a sensor can run anything from. That folder is hardcoded (no config field or API call can widen it), so a holder of the API key can never point a sensor at an arbitrary file on the device: not the agent's own credential or state files (`wifi.txt`, `config.json`, etc.), and not `main.py` itself. Scripts run with the server's privileges; keep the agent on a trusted LAN, as with the rest of the API.

Reading returns a clear message instead of failing when a script is missing or broken: `{"<name>": "script not found"}`, `{"<name>": "script failed or did not produce output"}` when the script errors or its `read()`/`result` yields nothing, and `{"<name>": "blocked: <reason>"}` when a sensor violates the name/argument rules (possible when sensors arrive via `/config/load` or an edited `config.json`; every read re-validates).

### Read

`GET /sensor/read/{name}` reads one sensor; `GET /sensor/read` reads them all, merged into one object. The output is always JSON, and a reading is **always nested under the sensor's own name** - one key per sensor, whatever the script returned:

- A JSON object with **several fields** nests whole: `{ "<name>": { ... } }`.
- A JSON object with a **single field** contributes just that field's value: `{ "<name>": <value> }` (the field's own key is dropped - a script that returns `{"temp": 47.8}` for a sensor named `cpu_temp` reads back as `{"cpu_temp": 47.8}`).
- Anything else (a non-JSON answer such as a bare number or string) is wrapped the same way; numbers stay numbers - a script returning `47.8` for a sensor named `cpu_temp` reads back as `{ "cpu_temp": 47.8 }` (a number, not a string), and a script returning `ok` for a sensor named `status` reads back as `{ "status": "ok" }`.

Because every sensor owns exactly one key in that merge, two sensors emitting the same field name can never overwrite each other. An unknown sensor name returns `404`.

```bash
curl -H "Api-Key: your-secret-key" http://<pico-host-addr>:8314/sensor/read
# → {"weather": {"pressure": 1013, "humidity": 62}, "probe": 21.4}
```

## Agent Setup

Read and change the agent's own configuration, and drive process and device lifecycle (restart, upgrade, logs).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/config/read` | Read the agent identity + full config (see response fields below) |
| `POST` | `/config/update` | Update one or more config fields (backs up the current config first) |
| `POST` | `/config/load` | Load a full config from a JSON dict (backs up the current config first; pass `{}` to reset) |
| `POST` | `/upgrade` | Upgrade in place. Takes no body. Downloads the latest `main.py`, moves the current one to `main.py.backup`, installs the new one, then resets (see below) |
| `POST` | `/restart` | Restart the device. Body is optional; pass `{"reboot": true}` to request a full hardware reset instead (see below) |
| `GET` | `/logs` | Last 30 log lines as a JSON array (in-memory ring buffer, cleared on reboot) |

**Restart/reboot (`/restart`)**: a microcontroller has no OS layer: only `machine.reset()` exists (a full hardware reset), so restart and reboot are the same operation. Both reply `{"status": "restarting"}` or `{"status": "rebooting"}` (matching the request) and then call `machine.reset()`.

**Self-upgrade (`/upgrade`)**: downloads the canonical `main.py` (from `raw.githubusercontent.com/ctrlpi/pico-gpio-api/main/main.py`) over TLS straight to flash. The download is verified before anything is swapped: a completed transfer (a short or truncated body is an error, never a partial file), non-trivial size, the expected banner and imports at the head, no NUL bytes anywhere, a byte count matching what was written, and the trailing `main()` call that any truncation would lose. Only then is the current `main.py` renamed to `main.py.backup` and the new file put in its place, and the same check is run again on the installed file: if it fails there, `main.py.backup` is restored and the board is left running the code it already had. If the download or either check fails, nothing is swapped and the board keeps running the current code. Config, `wifi.txt`, and status files are left untouched.

The response is sent and the connection closed before the device resets, so the client sees the reply before the Pico drops off WiFi and reconnects.

### `GET /config/read` response

Returns the agent's identity plus its full config:

| Field | Description |
|-------|-------------|
| `name` | Agent name: configured name, else `pico-<serial4>` (last 4 of the chip UID). Matches `/hello`. |
| `agent` | Live machine facts, computed on every read and **never stored**, in this order:<br>`platform`: the board model with the marketing words dropped (the firmware's `Raspberry Pi Pico 2 W with RP2350` is reported as `Pico 2 W with RP2350`)<br>`os`: the firmware string<br>`host`: always the literal `"pico"` - a Pico has no hostname<br>`serial`: the chip UID as lowercase hex, the same value the default `pico-<serial4>` name is built from<br>`ip`: this device's local network address<br>`mac`: the CYW43 station interface's address, the one behind that `ip`<br>`wifi`: the joined SSID<br>`signal`: dBm, from `WLAN.status("rssi")`<br>`cpu_temperature`: the on-chip sensor, °C<br>`uptime`: a short human string of at most two units, largest first (`"3d 4h"`, `"4h 12m"`, `"12m"`, `"38s"`)<br>`version`: the agent's software version<br>`config_updated`: when `config.json` itself was last written, as UTC `"YYYY-MM-DD HH:MM:SS"` (the file's own mtime, so it answers "when did this agent last change"); `""` if it has never been written, and also `""` when the recorded date predates 2023 - that means the RTC had not been set from NTP at write time, not that the config is 25 years old<br><br>Sending it back on `/config/update` or `/config/load` is ignored. `version` is kept in lockstep with `pi-gpio-api`, so a matching `version` means matching API behavior. |
| `notifications` | The general-purpose webhook target: `webhook` and `webhook_key`. |
| `bridges` | The three bridge callbacks: `homekit`, `homekit_key`, `matter`, `matter_key`, `homebridge`, `homebridge_key` (see [Bridge callbacks](#bridge-callbacks-bridgeshomekit--bridgesmatter--bridgeshomebridge)). |
| `settings` | Operational toggles: `logs_enabled`, `log_days`. |
| `sensors` | Named-sensor configuration map (see [Sensors](#sensors)). |
| `gpios` | Per-pin configuration map (see [GPIO](#gpio); always the last field). |

### Config fields (`/config/update`)

Send fields **ungrouped, at the top level** - the agent routes each one into its group,
so a later `/config/read` shows them nested:

```json
{
  "name": "my-pico",
  "webhook_url": "https://example.com/webhook",
  "webhook_key": "",
  "matter": "",
  "matter_key": "",
  "homekit": "",
  "homekit_key": "",
  "homebridge": "",
  "homebridge_key": "",
  "logs_enabled": true,
  "log_days": 7
}
```

| Sent at the root | Lands in |
|---|---|
| `webhook_url`, `webhook_key` | `notifications.webhook`, `notifications.webhook_key` |
| `matter`, `matter_key`, `homekit`, `homekit_key`, `homebridge`, `homebridge_key` | `bridges.*` |
| `logs_enabled`, `log_days` | `settings.*` |
| `name`, `api_key` | top level, ungrouped |

The grouped spelling works too (`{"settings": {"log_days": 5}}`), and either way each group
**merges key by key** - setting `log_days` never resets `logs_enabled`, and a bridge pushing
its own URL never clears the other bridge's. `/config/load` accepts both forms as well, and
always writes `config.json` in the grouped shape.

`webhook_key` is a dedicated key for the webhook *target* (see Webhooks below); it is not the agent's own `api_key`. An empty string clears it, same as `webhook_url`.

The live `agent` block is read-only: if it is present in an update or a whole-config load it is dropped rather than stored, so a read-modify-load round trip can post `/config/read` straight back.

Log retention is controlled by the `log_days` config field (default `7`, range `0`-`30`, clamped). An hourly background pass trims the in-memory log buffer down to that many days; setting `log_days` to `0` clears the buffer immediately and then keeps only the last hour on each pass.

## Errors

Error responses are JSON `{"error": ...}`, except 404 which has an empty body:

| Status | When | Body |
|--------|------|------|
| `400` | Bad write/config (e.g. writing to a pin configured as `input`/`vcc`/`gnd`, invalid value), or a request over **8 KB** | `{"error": "<reason>"}`, and `{"error": "Bad request"}` for the oversized case - a board with 264 KB of RAM stops reading rather than buffering the whole thing to classify it, so it is refused as unparseable rather than with a `413` |
| `403` | Missing or wrong `Api-Key` header | `{"error": "Invalid or missing Api-Key header"}` |
| `404` | Unknown pin/name, or unknown route | empty |
| `422` | Request body fails validation (malformed JSON, or a required field missing such as `value` on `POST /gpio/write/{pin}`) | `{"error": "<reason>"}`. Unlike the Pi, `GET /gpio/scan` never returns 422: the Pico's GPIOs are always available. |

## Webhooks

### Payload

When a pin has `watched: true`, the server POSTs the pin's state to `webhook_url` on every write (outputs) or state change (inputs: every edge, the primary use case):

```json
{ "agent": "pico-1a2b", "gpio": 17, "value": 1, "type": "output", "name": "relay" }
```

For reversed pins the payload also includes `level` (the physical GP state).

No webhook URL is configured by default. When a watched event occurs with **no target at all** configured (no `webhook_url` and no bridge callback), nothing is sent; the event is recorded in the agent log instead.

### Https targets: Pico W vs Pico 2 W

A TLS handshake needs roughly 35-40 KB of *contiguous* heap. The RP2040 frequently cannot produce a block that size once its heap has fragmented, so an https webhook on a Pico W delivers for a while and then starts failing, in a way no amount of garbage collection fixes.

As a result, **on the original Pico W (RP2040), `webhook_url` must be `http://`. An `https://` URL is rejected with a `400`, and is not stored.**

The **Pico 2 W (RP2350)** has the memory headroom and is unaffected: `https://` works there.

This applies to `webhook_url` only. The `bridges.homekit` / `bridges.matter` / `bridges.homebridge` callbacks are LAN targets that are already plain http, and plain http is unaffected on both boards.

For the same reason, a remote `POST /upgrade` on a Pico W may fail at the TLS handshake, since it downloads `main.py` over https. Retry, or install over USB with `install.sh` (or push the file with `pico.sh`).

### Delivery and retries

A failed delivery is retried **twice more, 30 seconds apart** (3 attempts in total, per target). A
non-2xx answer counts as a failure just like a refused connection - which is what carries an event
across a bridge restart: the bridge answers `401` until it has re-pushed its key onto this agent
(it does that off the first rejected event), so the retry 30s later is the one that lands. Each
attempt is logged, with the reason and whether another is coming.

### Bridge callbacks (`bridges.homekit` / `bridges.matter` / `bridges.homebridge`)

Three extra callback URLs, one owned by each smart-home bridge, sit alongside `webhook_url`:
`homekit` for the standalone `homekit-bridge`, `matter` for `matter-bridge`, and `homebridge`
for the `homebridge-ctrlpi` plugin. They are **additive**: a watched event is POSTed to every
configured target, with the same payload, so a bridge no longer has to take over `webhook_url`.

`homebridge` exists so the Homebridge plugin and the standalone `homekit-bridge` can drive the
same agent at once. Both put pins in the Home app, and while they shared the `homekit` slot,
whichever configured itself last silently stopped the other receiving events.

```bash
curl -X POST http://<pico-host-addr>:8314/config/update \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"matter": "http://192.168.1.50:8317/webhook?agent=pico"}'
```

`homekit-bridge`, `matter-bridge` and `homebridge-ctrlpi` set these themselves at startup, so
you normally never touch them by hand. An empty string clears one. All three are reported by `/config/read` and
`/gpio/watched` under `bridges` (the latter carries the URLs only, never the keys). The group
**merges key by key**, so setting one bridge's fields never disturbs another's.

Each callback has its own key field, `homekit_key` / `matter_key` / `homebridge_key`, set in
the same call:

```bash
curl -X POST http://<pico-host-addr>:8314/config/update \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"matter": "http://192.168.1.50:8317/webhook?agent=pico", "matter_key": "Kp3xR9tLmQ7z"}'
```

When set, that key is sent as the `Api-Key` header on that bridge's callbacks (and nowhere
else) so the bridge can tell a real event from one anybody on the LAN made up - it answers
`401` and logs the attempt otherwise. The bridges generate a random 12-character key per
agent on every start and push it here themselves, so a key left behind in this file is
worthless the moment the bridge restarts. As with `webhook_key`, this is never the
agent's own `api_key`: that key can never leak to a bridge. An empty string clears one, which
goes back to unauthenticated callbacks.

### Boot notification

On startup, the server also POSTs a one-off boot notification to every configured target:

```json
{ "agent": "pico-1a2b", "note": "Up and running" }
```

It carries no pin fields; consumers of the webhook feed should treat entries with a `note` field as status messages, not pin events. The same `webhook_key` rule below applies.

### Authentication

If a dedicated `webhook_key` is configured, it is sent as the `Api-Key` header on every webhook POST; if not, no `Api-Key` header is sent at all. The agent's own `api_key` is never sent to the webhook target. The public `https://ctrlpi.com/webhook/test` test endpoint requires no key, it will display whatever key was sent for debugging.

### Testing webhooks

For development and testing, ctrlPi provides a public test endpoint: point `webhook_url` at `https://ctrlpi.com/webhook/test` and monitor the incoming events live at [https://ctrlpi.com/webhook/watch](https://ctrlpi.com/webhook/watch). The feed is public and events are retained only briefly, so use it for verification during setup, not as a production target. (On an original Pico W this target is unreachable - see [https targets](#https-targets-pico-w-vs-pico-2-w) above.)

### Changing the URL

Change it by setting `webhook_url` (and optionally `webhook_key`) via `POST /config/update`:

```bash
curl -X POST http://<pico-host-addr>:8314/config/update \
  -H "Api-Key: your-secret-key" -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://example.com/webhook", "webhook_key": "target-key"}'
```

## Files

The state the agent persists to disk, all on the Pico's filesystem alongside `main.py`. Each is created on first run as needed, so there's nothing to set up by hand.

| File | Contents |
|------|----------|
| `config.json` | API key, pin configs, feature flags, webhook URL. Config backup is written automatically to `config-backup.json` and read on boot if `config.json` exists but fails to parse/validate |
| `status.json` | Last known pin values, restored on boot when `init: "last"`. Inputs are recorded too, so a watched input's `init: "last"` reference has a previous reading to compare against |
| `wifi.txt` | WiFi credentials (not committed; see `wifi.txt.example`) |

## Differences from the Pi version

| Feature | Pi (`pi-gpio-api`) | Pico (`pico-gpio-api`) |
|---------|-----|------|
| GPIO range | BCM 2–27 | GP0–GP28 except GP23/24/25/29 (reserved for the CYW43 WiFi chip) |
| GPIO | `gpiozero` | `machine.Pin` |
| Sensor scripts | `.sh`, run as `sh scripts/<file>` | `.py`, exec'd in-process |
| Interactive API docs (`/docs`, `/redoc`) | Yes (`settings.docs_enabled`) | No |
| Logs | File (`agent.log`), last 50 lines via `/logs` | In-memory ring buffer, last 30 lines via `/logs`, cleared on reboot |
| https webhook targets | Always available | Pico 2 W only (see [https targets](#https-targets-pico-w-vs-pico-2-w)) |

## Pico 2 W (RP2350) note

The firmware runs unchanged on the Pico 2 W and auto-detects the board; `platform`
reports `"Pico 2 W with RP2350"` instead of `"Pico W with RP2040"` in `/config/read` and
`/gpio/scan`. The board test that drives the RP2350 erratum workaround below keys off its
own `IS_PICO2` flag, read straight from the firmware string, so it does not depend on how
`platform` happens to be formatted.

**RP2350 erratum E9 workaround:** on the Pico 2 W, a GPIO pad that has been driven
high latches at ~2.2 V once released; the internal pull-down alone can never bring it
back LOW, so an `input` + `pullup: "down"` pin would read `1` forever. When configuring
such a pin on a Pico 2 W, the firmware briefly drives the pad low to discharge it before
switching to input. Side effect to be aware of: if an external circuit is actively
driving that line HIGH at the moment the pin is configured, the discharge causes a
microsecond-scale, pad-current-limited contention pulse (this is the standard accepted
E9 workaround). Original Pico W (RP2040) boards are unaffected and skip the discharge.

## Related projects

- **[`pi-gpio-api`](https://github.com/ctrlpi/pi-gpio-api)**: Raspberry Pi GPIO REST server that reads inputs and drives outputs (and named sensors) over an HTTP API.
- **[`mcp-bridge`](https://github.com/ctrlpi/mcp-bridge)**: MCP server that exposes GPIO control as tools for AI agents (e.g. Claude), executing them over the agents' REST API.
- **`homekit-bridge`** *(coming soon)*: native Apple HomeKit bridge that exposes GPIO pins and sensors as HomeKit accessories, driven over the agents' REST API.
- **`matter-bridge`** *(coming soon)*: Matter bridge that exposes GPIO pins and sensors to any Matter platform (Apple Home, Google Home, Alexa, Home Assistant), driven over the agents' REST API.
- **`gpio-lab`** *(coming soon)*: Web dashboard for monitoring and controlling agents over REST API and MCP.
- **[`pico-bay`](https://github.com/ctrlpi/pico-bay)**: An App and MCP Server for managing Raspberry Pi Pico and ESP32 boards with MicroPython and CircuitPython over USB.

## License

[MIT](LICENSE)
