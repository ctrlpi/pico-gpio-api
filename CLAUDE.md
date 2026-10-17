# CLAUDE.md - pico-gpio-api

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A standalone **MicroPython** port of `pi-gpio-api` for the Raspberry Pi Pico W and Pico 2 W. It implements the exact same REST API and wire format as the main Raspberry Pi version.
It runs on **Pico hardware only** (no mock mode).

## Lockstep with `pi-gpio-api`

The two agents are a matched pair. **A change to a route, a field name, a status code, or a JSON shape here almost always needs the same change in `pi-gpio-api`, and vice versa.** 
Divergence is only correct where the MicroPython hardware forces it (e.g., HTTPS limits, CYW43 pins, `.py` vs `.sh` scripts).

## The README is locked

`README.md` is finished. **Never edit it without the user's explicit confirmation first**, even for small fixes.

## Architecture (`main.py`)

1. **Constants/Helpers**: Versioning, configuration defaults, board string normalizers.
2. **`Blinker`**: Non-blocking onboard LED status driven by a `machine.Timer`.
3. **`WebhookSender`**: A keep-alive HTTP(S) client that queues POSTs. 
4. **`GPIOManager`**: Manages config/status persistence, hardware pins, IRQs, sensors, and logs.
5. **WiFi/Time**: `connect_wifi`, `sync_time`, `_gateway_alive`.
6. **HTTP Plumbing**: Parsing, JSON response helpers, routing.
7. **Upgrade logic**: OTA downloads.

### Routes
Matches `pi-gpio-api` exactly. `GET /hello` bypasses the `Api-Key` check; all others require it.

### Config
Stored as `gpios`, `notifications`, `bridges`, `settings`. Root-level aliases provided in requests are folded into these groups before storage. 
Three files are persisted: `config.json`, `config-backup.json`, and `status.json`.

## Key Design Logic (MicroPython specifics)

- **Text fields stay text**: a number sent for a key, name, URL or script (`api_key`, `name`, `notifications.*`, `bridges.*`, a pin's `name`, a sensor's `script`) is stored as text, so `4242` becomes `"4242"` and a key typed as digits never locks the agent out. `_as_text()` / `_text_fields()` run in `_normalize_config()` (every `/config/update` and `/config/load`) and in `config_pin()` / `config_sensor()`. `test_numbers_stored_as_text` covers it.
- **Comments stay few and short**: `main.py` runs on the Pico as source, so every comment byte costs flash and the RAM needed to compile it. Comment only what is non-obvious, in one line where possible (about 3% of lines today, against 11-18% in the other agents), and don't raise it to their level.
- **Single-threaded**: One accept loop serves one connection. Nothing sleeps: timers, queues, and polled lists are used instead.
- **Heap fragmentation**: `WebhookSender` caches `https` sockets to avoid fragmentation since a TLS handshake requires significant contiguous memory. Plain `http` uses throwaway sockets.
- **Flash optimization**: Config and status writes are deferred and coalesced (`_flush_all`) to minimize flash wear. Reboots trigger a flush first.
- **WiFi robustness**: A dead-man switch hard-resets after `WIFI_DEAD_SECS` (40 s) with the link reported down. A link that reports connected but passes nothing is `WifiWatchdog`'s job (ported from `esp32-gpio-api` 5aee401): only while idle (no request for `WIFI_PROBE_SECS`, 120 s, and no pulse revert pending) it probes the gateway with `_gateway_alive()`, then every 120 s; two failed probes trigger a non-blocking soft reconnect (`_wifi_reconnect`, GPIO untouched), and only if the link is still dead `WIFI_RECONNECT_SECS` (20 s) later does it flush and reset. Every accepted request calls `traffic()`, which counts as proof the link works. Each step logs a `[WIFI]` line. It used to reset after 360 s, and only ever by reboot. `TestWifiWatchdog` in `test.py` covers it on a fake clock.
- **Reserved Pins**: GP23/24/25/29 are reserved for the CYW43 wireless chip and are excluded from the API to prevent link drops.
- **Sensors**: Scripts are `.py` files executed in-process (unlike the Pi's shell scripts). They must reside in `scripts/`.
- **HTTPS Webhooks**: Restricted to Pico 2 only (`HTTPS_WEBHOOK_OK`). Pico W lacks memory for the handshake.
- **Request bodies**: `_recv_request` reads `Content-Length` only, capped at `MAX_REQUEST_BYTES` (8 KB): an oversized body is drained (`_drain`) and refused as `400`. A chunked body (no `Content-Length`) is refused the same way rather than decoded, since without this its unread body would reach `/config/load` as `{}`, a full config reset. This differs from the Pi (`413`, chunked decoded) on purpose; `api-tester` test 54 accepts both.
- **Request memory**: by late in a full tester run the heap is fragmented, so `_recv_request` reads the body into **one `bytearray` sized from `Content-Length`** (never a bytes object regrown per 1 KB chunk) and returns `(head, body)`, and `_parse_request` runs `gc.collect()` before `ujson.loads()` on any body over 1 KB. A `MemoryError` is never reported as bad JSON: it propagates to `handle_request()`, which answers `500 {"error": "Out of memory"}`. That wrapper also closes the socket on every path. Before this, a 4 KB body late in a run came back `422` (the `MemoryError` inside `ujson.loads` was swallowed as a parse failure), which is how test 54 failed intermittently on a Pico W. Ported from `esp32-gpio-api` (f581554), which hit the same thing.
- **Sending**: replies go out through `_send_all()`, a non-blocking send with a 3 s deadline, header and body sent separately (no concatenated copy). A blocking `sendall` can wait forever when lwIP runs out of buffers, which wedges the single-threaded server.

## Working on a board

Tools run on the **Mac**, communicating via USB serial `mpremote`.

```bash
./install.sh              # deploy: only prompts for what the DEVICE is missing
./install.sh --config     # force re-prompting for the API key / agent name
./install.sh --wifi       # force re-prompting for SSID / password
./install.sh --upgrade    # re-download the release files first
```

For anything else on the board use `mpremote` directly (from this project's `venv`), e.g. `mpremote fs ls`, `mpremote fs cat config.json`. There is no `pico.sh` helper.

## Testing
Tested via `api-tester` running the same REST suite used for `pi-gpio-api`.
`cd ../api-tester && python run_tests.py <pico-host-addr>`

`TestRequestReading` in `test.py` loads `main.py` in-process (everything above its last line, `main()`, via `_load_main()`) and drives `_recv_request`/`handle_request` with a fake socket. The rest of `test.py` runs `main.py` on the desktop instead, under CPython with the stand-in modules in `test_mocks/` (`machine`, `network`, `usocket`, `utime`, ...) on `PYTHONPATH` and `PICO_PORT` picking the port, in a temp working directory. That is a test harness only, not a mock mode for running the agent; WiFi timing and memory limits are not reproduced there.

## LED blink codes
- **Fast 0.1s**: Booting/connecting to WiFi.
- **1 blink**: Idle main loop checking in.
- **2 blinks**: HTTP request received.
