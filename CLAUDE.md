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

- **Single-threaded**: One accept loop serves one connection. Nothing sleeps: timers, queues, and polled lists are used instead.
- **Heap fragmentation**: `WebhookSender` caches `https` sockets to avoid fragmentation since a TLS handshake requires significant contiguous memory. Plain `http` uses throwaway sockets.
- **Flash optimization**: Config and status writes are deferred and coalesced (`_flush_all`) to minimize flash wear. Reboots trigger a flush first.
- **WiFi robustness**: A dead-man switch hard-resets after `WIFI_DEAD_SECS` without a link. `_gateway_alive()` probes for zombie links.
- **Reserved Pins**: GP23/24/25/29 are reserved for the CYW43 wireless chip and are excluded from the API to prevent link drops.
- **Sensors**: Scripts are `.py` files executed in-process (unlike the Pi's shell scripts). They must reside in `scripts/`.
- **HTTPS Webhooks**: Restricted to Pico 2 only (`HTTPS_WEBHOOK_OK`). Pico W lacks memory for the handshake.

## Working on a board

Tools run on the **Mac**, communicating via USB serial `mpremote`.

```bash
./install.sh              # deploy: only prompts for what the DEVICE is missing
./install.sh --config     # force re-prompting for the API key / agent name
./install.sh --wifi       # force re-prompting for SSID / password
./install.sh --upgrade    # re-download the release files first

./pico.sh list            # what is on the board
./pico.sh <file>          # print a file from the board
./pico.sh get|put|rm <f>  # move one file
./pico.sh clean           # wipe the board (asks first)
```

## Testing
Tested via `dev-api-tester` running the same REST suite used for `pi-gpio-api`.
`cd ../dev-api-tester && python run_tests.py <pico-host-addr>`

## LED blink codes
- **Fast 0.1s**: Booting/connecting to WiFi.
- **1 blink**: Idle main loop checking in.
- **2 blinks**: HTTP request received.
