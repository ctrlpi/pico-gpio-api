# CLAUDE.md - pico-gpio-api

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A standalone **MicroPython** port of `pi-gpio-api` for the Raspberry Pi Pico W and Pico 2 W. It implements the exact same REST API and wire format as the main Raspberry Pi version.
It runs on **Pico hardware only** (no mock mode).

## Lockstep with `pi-gpio-api`

The two agents are a matched pair. **A change to a route, a field name, a status code, or a JSON shape here almost always needs the same change in `pi-gpio-api`, and vice versa.** 
Divergence is only correct where the MicroPython hardware forces it (e.g., HTTPS limits, CYW43 pins, `.py` vs `.sh` scripts).

## The README is locked

`README.md` is done. Do not edit it without the user's explicit confirmation, even for a small wording or formatting fix, and even to keep it in sync with another project's README.

## Files on the board

| File | Role |
|---|---|
| `main.py` | Imports `app`, or `upgrader` while `upgrade.lock` exists |
| `app.mpy` | `app.py` precompiled by `build.sh` (`mpy-cross` 1.29). The board loads bytecode instead of compiling ~1900 lines: a Pico W has no RAM to compile the source (`MemoryError`, with or without comments). One `app.mpy` serves the Pico W and Pico 2 W (`.mpy` format 6, bytecode only) |
| `upgrader.mpy` | `upgrader.py` precompiled the same way: the over-the-air upgrade, run in its own boot (see Upgrade logic) |
| `config.json`, `config-backup.json`, `status.json`, `wifi.txt`, `scripts/` | Device state |

**`app.py` and `upgrader.py` are the source, the `.mpy` files are what runs.** After any change, run `./build.sh` (it builds both) and commit the sources and the `.mpy` files: they are tracked, and `POST /upgrade` downloads them.

## Architecture (`app.py`)

1. **Constants/Helpers**: Versioning, configuration defaults, board string normalizers.
2. **`Blinker`**: Non-blocking onboard LED status driven by a `machine.Timer`.
3. **`WebhookSender`**: A keep-alive HTTP(S) client that queues POSTs. 
4. **`GPIOManager`**: Manages config/status persistence, hardware pins, IRQs, sensors, and logs.
5. **WiFi/Time**: `connect_wifi`, `sync_time`, `_retry_ntp`, `_gateway_alive`. NTP is tried at boot, then by `_retry_ntp()` every `_NTP_RETRY_MS` (2 min) from the main loop while the clock reads before 2023: one lost answer on a weak link used to leave the clock at 2000 until the next reboot, with every log line stamped 2000-01-01 and `config_updated` empty (`api-tester` test 67 then skips). On success the clock jumps, so `_BOOT_TIME` moves by the same amount and `uptime` stays right; it logs `[BOOT] Clock set over NTP`. `TestNtpRetry` covers it.
6. **HTTP Plumbing**: Parsing, JSON response helpers, routing.
7. **Upgrade logic**, in two boots, the same code as `esp32-gpio-api`: `POST /upgrade` (`_do_upgrade()`) writes `upgrade.lock` (JSON: `url` = `UPGRADE_BASE_URL`, `files` = `UPGRADE_FILES`, `attempt`), answers `200 {"status": "upgrading"}` and soft-resets; `main.py` sees the lock and imports `upgrader` instead of `app`, so the TLS download runs on a fresh heap that a running agent no longer has (a Pico W has 185 KB free after a soft reset). `upgrader.mpy` downloads `app.mpy`, `upgrader.mpy` and `main.py` as `<name>.new`, verifies each, swaps them in with `.backup` copies (rolled back on failure), writes `upgrade-result.json`, removes the lock and soft-resets into the agent, which logs `[UPGRADE] Done, now <version>: ...` (or `Failed, still ...`) once (`_log_upgrade_result()`). A failed attempt keeps the lock and hard-resets (a fresh radio) for the next one; the third failure gives up and the old agent starts. Soft resets keep the Wi-Fi link: with hard resets the Pico 2 W test board was unreachable for ~90 s after the second quick reassociation, and an upgrade took 43-123 s instead of ~10 s. A board without `upgrader.mpy` answers `500` and moves over once by USB (`install.sh`, which also deletes a leftover `upgrade.lock`). The upgrader takes the URL and file list from the lock, so both are set in `app.py` only; `TestUpgrade` runs `upgrader.py` against a local release folder. A board on 0.9.27 or earlier ran a single `main.py`, and its `/upgrade` refuses the new `main.py` (too small, no trailing `main()`), so it moves over once by USB (`install.sh` or Pico Bay).

### Routes
Matches `pi-gpio-api` exactly. `GET /hello` bypasses the `Api-Key` check; all others require it. A usable key is non-empty text (`_valid_key()`; a number counts, as text). An empty, null or non-text `api_key` counts as no key: `config.json` then falls back to the default, `/config/load` keeps the current key, and `/config/update` ignores it. An empty or missing `Api-Key` header is always refused. `test_bad_api_key_*` cover it.

### Config
Stored as `gpios`, `notifications`, `bridges`, `settings`. Root-level aliases provided in requests are folded into these groups before storage. 
Three files are persisted: `config.json`, `config-backup.json`, and `status.json`.

## Key Design Logic (MicroPython specifics)

- **Text fields stay text**: a number sent for a key, name, URL or script (`api_key`, `name`, `notifications.*`, `bridges.*`, a pin's `name`, a sensor's `script`) is stored as text, so `4242` becomes `"4242"` and a key typed as digits never locks the agent out. `_as_text()` / `_text_fields()` run in `_normalize_config()` (every `/config/update` and `/config/load`) and in `config_pin()` / `config_sensor()`. `test_numbers_stored_as_text` covers it.
- **Single-threaded**: One accept loop serves one connection. Nothing sleeps: timers, queues, and polled lists are used instead.
- **Heap fragmentation**: `WebhookSender` caches `https` sockets to avoid fragmentation since a TLS handshake requires significant contiguous memory. Plain `http` uses throwaway sockets.
- **Flash optimization**: Config and status writes are deferred and coalesced (`_flush_all`) to minimize flash wear. Reboots trigger a flush first.
- **WiFi robustness**: A dead-man switch hard-resets after `_WIFI_DEAD_SECS` (40 s) with the link reported down. A link that reports connected but passes nothing is `WifiWatchdog`'s job (ported from `esp32-gpio-api` 5aee401): only while idle (no request for `_WIFI_PROBE_SECS`, 120 s, and no pulse revert pending) it probes the gateway with `_gateway_alive()`, then every 120 s; two failed probes trigger a non-blocking soft reconnect (`_wifi_reconnect`, GPIO untouched), and only if the link is still dead `_WIFI_RECONNECT_SECS` (20 s) later does it flush and reset. Every accepted request calls `traffic()`, which counts as proof the link works. Each step logs a `[WIFI]` line. It used to reset after 360 s, and only ever by reboot. `TestWifiWatchdog` in `test.py` covers it on a fake clock.
- **Agent block** (`_agent_info()`, in `/config/read` and `/gpio/scan`): what can't change while the agent runs is read once: `os`, `serial` (also used by `_default_name()` on every `/hello`) and `mac` in `GPIOManager.__init__`, the SSID once per connection (read again after a read finds the link down). `ip`, `signal`, `cpu_temperature` and `uptime` stay live. `config_updated` is the file's time once at start-up, then set by `_config_stamp()` whenever the agent writes `config.json` (`_flush_config()`, `load_config()`), so a read stats no file. Two fewer Wi-Fi chip calls per read. `TestAgentBlockCache` covers it.
- **Reserved Pins**: GP23/24/25/29 are reserved for the CYW43 wireless chip and are excluded from the API to prevent link drops.
- **Sensors**: Scripts are `.py` files executed in-process (unlike the Pi's shell scripts). They must reside in `scripts/`.
- **Scripts (`/script/list`, `/script/read`, `/script/write`, `/script/run`)**: `GPIOManager.list_scripts()` / `read_script()` / `write_script()` / `run_script()`, raising `ScriptError(status, message)`; same routes, status codes and JSON as `pi-gpio-api`. Names use `_is_script_name()` plus no leading `-` and `_SCRIPT_NAME_MAX` (32) in `_script_path()`; a subfolder is `400`. A run execs the file in-process like a sensor: `output` is `read(*args)` or `result` as text (JSON for a non-string), `exit_code` `1` with `"<Error>: <message>"` when it raises (`SystemExit` included, so a script can't stop the agent). No timeout, unlike the Pi's `504`. Writes go through a hidden `.<name>.tmp` and `os.rename` (remove-then-rename if the filesystem won't replace), and create `scripts/` when it's missing. `write_script()` answers `403` while `api_key` is the default and while `scripts/write.lock` exists. A write may create `write.lock` (nothing else ever does, install included), and from then on every write, the lock's own included, is refused, so `/script/write` can't lift it. USB can: `./install.sh --unlock` deletes it from every Pico on USB, then resets each one, since `mpremote` stops the agent; it installs nothing. So can a script run through `/script/run`, since runs still work while locked (api-tester test 59 unlocks that way). `write.lock` in any case is never run. The `test_script_*` tests cover it.
- **HTTPS Webhooks**: Restricted to Pico 2 only (`HTTPS_WEBHOOK_OK`). Pico W lacks memory for the handshake.
- **Request bodies**: `_recv_request` reads `Content-Length` only, capped at `_MAX_REQUEST_BYTES` (8 KB): an oversized body is refused as `400` and the socket closed with the rest unread (it used to be drained, up to 64 KB, which exhausted the network buffers; a client may see a reset instead of the `400`, which `api-tester` accepts). A chunked body (no `Content-Length`) is refused the same way rather than decoded, since without this its unread body would reach `/config/load` as `{}`, a full config reset. This differs from the Pi (`413`, chunked decoded) on purpose; `api-tester` test 53 accepts both. Replies go out through `_send_json()` as one `ujson.dumps` and one write, header and body together. It used to send ~512-byte writes built by `_json_pieces()` (as `esp32-gpio-api` still does, for the classic ESP32's short system RAM); on a Pico W that took a big reply ~3x longer (`/gpio/scan` 360 ms against 135 ms, `/config/read` 345 against 118, medians of 3 runs of 40 requests), most likely waiting on the client's delayed ACK, while the largest reply (~2.3 KB) is no strain on a Pico's heap. `test_big_replies_exact_length` covers the `Content-Length`.
- **Request memory**: by late in a full tester run the heap is fragmented, so `_recv_request` reads the body into **one `bytearray` sized from `Content-Length`** (never a bytes object regrown per 1 KB chunk) and returns `(head, body)`, and `_parse_request` runs `gc.collect()` before `ujson.loads()` on any body over 1 KB. A `MemoryError` is never reported as bad JSON: it propagates to `handle_request()`, which answers `500 {"error": "Out of memory"}`. That wrapper also closes the socket on every path. Before this, a 4 KB body late in a run came back `422` (the `MemoryError` inside `ujson.loads` was swallowed as a parse failure), which is how test 53 failed intermittently on a Pico W. Ported from `esp32-gpio-api` (f581554), which hit the same thing.
- **Sending**: replies go out through `_send_all()`, a non-blocking send with a 3 s deadline. A blocking `sendall` can wait forever when lwIP runs out of buffers, which wedges the single-threaded server.

## Working on a board

Tools run on the **Mac**, communicating via USB serial `mpremote`.

```bash
./build.sh                # app.py -> app.mpy, upgrader.py -> upgrader.mpy (install.sh runs it when a source is newer)
./install.sh              # deploy app.mpy + upgrader.mpy + main.py: only prompts for what the DEVICE is missing
./install.sh --config     # force re-prompting for the API key / agent name
./install.sh --wifi       # force re-prompting for SSID / password
./install.sh --upgrade    # re-download the release files first
./install.sh --unlock     # install nothing: delete scripts/write.lock on every Pico, then reset it
```

`install.sh` asks each `/dev/cu.usbmodem*` port for `os.uname()` and installs only on a Pico W or Pico 2 W (`sysname` `rp2`, machine `Pico W` / `Pico 2 W`); any other board that answered is reset, since the question stops its program.

For anything else on the board use `mpremote` directly (from this project's `venv`), e.g. `mpremote fs ls`, `mpremote fs cat config.json`. There is no `pico.sh` helper.

## Testing
Tested via `api-tester` running the same REST suite used for `pi-gpio-api`.
`cd ../api-tester && python run_tests.py <pico-host-addr>`

`TestRequestReading` in `test.py` loads `app.py` in-process (everything above its last line, `main()`, via `_load_main()`) and drives `_recv_request`/`handle_request` with a fake socket. The rest of `test.py` runs `app.py` on the desktop instead, under CPython with the stand-in modules in `test_mocks/` (`machine`, `network`, `usocket`, `utime`, ...) on `PYTHONPATH` and `PICO_PORT` picking the port, in a temp working directory. That is a test harness only, not a mock mode for running the agent; WiFi timing and memory limits are not reproduced there.

## LED blink codes
- **Fast 0.1s**: Booting: until WiFi is up and the server is listening (it used to wait for the boot webhook, which kept it blinking for a minute or more when a target was unreachable).
- **1 blink**: Idle main loop checking in.
- **2 blinks**: HTTP request received.
