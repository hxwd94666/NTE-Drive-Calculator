# nte-core CLI protocol

English | [简体中文](CLI_PROTOCOL_ZH.md)

`nte-core` is an English-only headless sidecar. Protocol fields and messages are
stable machine-facing values; clients must branch on numeric JSON-RPC codes and
`error.data.domain_code`, not on human-readable `message` or `detail` text.

The supported distribution is `nte-core-windows-x64.zip`. It contains
`nte-core.exe`, `CLI_PROTOCOL.md`, `CLI_PROTOCOL_ZH.md`, `examples/`,
`THIRD_PARTY_LICENSES.md`, and `licenses/`.
The sidecar is intended for third-party tools running on the same Windows
machine. It never listens on or opens a TCP/HTTP port. The CLI executable embeds
only the core JSON resources required by capture parsing and contains no GUI
images, fonts, application icon, or GUI dependency stack. Distribution and
integration remain subject to the repository's AGPL/commercial dual-license
terms.

## Commands

```text
nte-core serve --stdio [--data-dir <path>] [--log-level <level>]
nte-core version --json
nte-core devices --json
```

Unknown arguments and missing option values write one English line to stderr and
exit with code 2. `devices --json` writes `{"devices":[...]}` and exits 0, or
writes an English Npcap error to stderr and exits 1. `version --json` and a
successful `devices --json` each write exactly one JSON line to stdout.

`--data-dir` is the root used for Core PCAP files. It defaults to `logs`; an
explicit directory is used directly and is created when raw capture starts.

### One-shot examples

```powershell
.\nte-core.exe version --json | ConvertFrom-Json
.\nte-core.exe devices --json | ConvertFrom-Json
```

## Transport

`serve --stdio` uses UTF-8 JSON-RPC 2.0 over NDJSON:

- stdin carries one request object per line;
- stdout carries one response object per line and no other text;
- stderr is reserved for English diagnostics;
- every stdout message is flushed immediately;
- blank input lines are ignored;
- batch arrays and client notifications are not supported;
- request IDs must be strings, integers, or null;
- a line may contain at most 1 MiB excluding its line ending;
- an oversized line receives an Invalid Request response and closes the service;
- stdin EOF and a broken stdout pipe close the service with exit code 0.

Responses and `event.*` notifications share stdout and may be interleaved.
Clients must match responses by `id` and route messages without an `id` by
their `method`. Do not assume the next stdout line belongs to the most recent
request.

### PowerShell stdio example

This sends a complete finite NDJSON session. PowerShell closes stdin after the
last line, while `core.shutdown` asks Core to exit cleanly first.

```powershell
$requests = @(
  '{"jsonrpc":"2.0","id":"1","method":"core.hello","params":{"client_name":"PowerShell example","client_version":"1.0.0","protocol_min":1,"protocol_max":1}}'
  '{"jsonrpc":"2.0","id":"2","method":"capture.detect","params":{}}'
  '{"jsonrpc":"2.0","id":"3","method":"core.status","params":{}}'
  '{"jsonrpc":"2.0","id":"4","method":"core.shutdown","params":{}}'
)
$requests | .\nte-core.exe serve --stdio
```

### Python stdio example

[`examples/nte_core_client.py`](examples/nte_core_client.py) is a
standard-library client with a dedicated stdout reader, response-ID matching,
event routing, timeouts, and graceful shutdown. It requires Python 3.10 or
newer and no third-party packages.

```powershell
# Handshake, status, environment detection, shutdown (does not start capture)
python .\examples\nte_core_client.py .\nte-core.exe

# Optional 15-second combat capture without creating a PCAP file
python .\examples\nte_core_client.py .\nte-core.exe `
  --live-seconds 15 --profile combat --raw-capture disabled
```

The live example prints `event.*` notifications, then queries both
`inventory.get_latest` and `battle.get_summary`. Either query may legitimately
return `INVENTORY_NOT_READY` or a null battle result when the requested data was
not observed during the short capture.

## Errors

The standard JSON-RPC errors are supported:

| Code | Message |
| ---: | --- |
| -32700 | Parse error |
| -32600 | Invalid Request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |

Core domain failures use code `-32000`, message `Core error`, and include stable
`error.data.domain_code`. Protocol version 1 can return:

- `PROTOCOL_VERSION_MISMATCH`
- `HANDSHAKE_REQUIRED`
- `NPCAP_NOT_FOUND`
- `GAME_PROCESS_NOT_FOUND`
- `CAPTURE_DEVICE_NOT_FOUND`
- `SYSTEM_PROBE_FAILED`
- `CAPTURE_ALREADY_RUNNING`
- `CAPTURE_NOT_RUNNING`
- `INVENTORY_NOT_READY`
- `EQUIPMENT_PLUGIN_UNAVAILABLE`
- `EQUIPMENT_PLUGIN_BUSY`
- `EQUIPMENT_REQUEST_REJECTED`

Underlying OS, Npcap, endpoint, payload, and filesystem details are not copied to
stdout.

## Handshake

Except for `core.hello` and `core.shutdown`, methods require a successful
handshake. Repeating a valid handshake is idempotent.

```json
{"jsonrpc":"2.0","id":"hello-1","method":"core.hello","params":{"client_name":"NTE Drive Calc","client_version":"1.3.0","protocol_min":1,"protocol_max":1}}
```

The result contains `core_version`, negotiated `protocol_version`,
`data_version`, `capabilities`, and `raw_capture_default`. Only the methods
documented below are callable. `capabilities` contains `equipment` when the
local equipment-plugin bridge is available in this Core build.

## Method reference

| Method | Params | Handshake | Result |
| --- | --- | --- | --- |
| `core.hello` | handshake object | No | versions, capabilities, raw-capture default |
| `core.status` | `{}` or omitted | Yes | current capture/inventory/battle state |
| `core.shutdown` | `{}` or omitted | No | `shutting_down` |
| `capture.detect` | `{}` or omitted | Yes | game/device detection snapshot |
| `capture.start` | capture options | Yes | process-local `operation_id` |
| `capture.stop` | `{}` or omitted | Yes | stopped `operation_id` |
| `inventory.get_latest` | `{}` or omitted | Yes | latest complete inventory snapshot |
| `equipment.equip_module` | character, equipment, row, column | Yes | plugin dispatch status |
| `equipment.equip_core` | character, equipment | Yes | plugin dispatch status |
| `equipment.unequip_module` | character, equipment | Yes | plugin dispatch status |
| `equipment.unequip_core` | character, equipment | Yes | plugin dispatch status |
| `equipment.unequip_all` | character | Yes | plugin dispatch status |
| `equipment.equip_one_key` | character, placements, core | Yes | plugin dispatch status |
| `equipment.move_module_to_character` | character, equipment, row, column | Yes | plugin dispatch status |
| `equipment.move_core_to_character` | character, equipment | Yes | plugin dispatch status |
| `equipment.set_item_discarded` | equipment, discarded | Yes | plugin dispatch status |
| `equipment.set_item_locked` | equipment, locked | Yes | plugin dispatch status |
| `battle.get_summary` | `subtract_time_stop` | Yes | battle summary or null |
| `battle.reset` | `{}` or omitted | Yes | `reset:true` |

## Core methods

### `core.hello`

Negotiates protocol version 1. Empty client names/versions, missing fields, and
an inverted protocol range are Invalid params. A range not containing version 1
returns `PROTOCOL_VERSION_MISMATCH`.

### `core.status`

Returns the handshake state, whether capture is running, the active profile,
Core state, latest inventory generation, whether battle data exists, and the
publishable raw-capture path. `core_state` is `idle` or `capturing`.

### `core.shutdown`

Allowed before or after handshake. Returns `{"shutting_down":true}`, flushes the
response, and exits 0. stdin EOF performs the same cleanup without a response.

### `capture.detect`

Enumerates Npcap devices and returns:

```json
{
  "game_process_detected": false,
  "recommended_device": null,
  "local_ip_detected": false,
  "devices": [
    {"name":"...","description":"...","ipv4":["192.168.x.x"]}
  ]
}
```

The game being absent, having no active usable TCP connection, or having no
matching Npcap adapter is reported as normal `false`/`null` state. Npcap and
initial process-enumeration failures return domain errors. The current platform
boundary does not distinguish an absent game connection from a TCP-table probe
failure, so both remain best-effort `local_ip_detected:false` results. Remote
server addresses and ports are never returned.

## Capture and inventory methods

### `capture.start`

```json
{
  "jsonrpc":"2.0",
  "id":"start-1",
  "method":"capture.start",
  "params":{
    "profile":"inventory",
    "device":{"mode":"auto"},
    "include_incoming":true,
    "server_damage_calibration":true,
    "raw_capture":"enabled"
  }
}
```

`profile` is `inventory` or `combat`. `device` is `{"mode":"auto"}` or
`{"mode":"name","name":"Npcap device name"}`. `raw_capture` is optional and
defaults to `enabled`; `disabled` performs no PCAP file creation or writes.
Automatic device selection requires a detected game connection. Manual device
selection treats a missing named adapter as a hard error while an unavailable
game connection remains a soft local-IP degradation.

The response contains a process-local `operation_id`. It does not wait for a
packet or inventory snapshot. Reliable `event.capture.status` notifications
report `starting`, `running`, and `stopped`, each with a monotonically increasing
`sequence`, the operation ID, and profile. Starting while active returns
`CAPTURE_ALREADY_RUNNING`.

The raw PCAP path appears in `core.status` only when the caller explicitly sent
`"raw_capture":"enabled"`. Omitting the field still preserves enabled-by-default
capture but does not broadcast the path.

### `capture.stop`

Stops and joins the capture/parser threads, flushes the PCAP writer, drains all
already-produced engine events, and then returns the stopped operation ID.
`event.capture.status` reports `stopped`. Calling without an active capture
returns `CAPTURE_NOT_RUNNING`. A validated inventory snapshot queued before stop
is retained and emitted before shutdown completes.

### `inventory.get_latest`

Returns the latest complete inventory snapshot without starting capture or
writing a business inventory file. Before the first complete snapshot it returns
`INVENTORY_NOT_READY`.

Inventory results and `event.inventory.snapshot` contain `generation`,
`observed_at_unix_ms`, `complete`, `item_count`, and `items`. Event notifications
also contain the global event `sequence`. Internal item IDs are mapped as:

```json
{
  "uid":{"slot":1,"serial":2},
  "locked":true,
  "discarded":false,
  "equipped":true,
  "equipped_character_uid":{"slot":3,"serial":4},
  "equipped_character_id":1020,
  "equipped_placement":{"row":2,"column":3}
}
```

The internal misspelling `solt` is never exposed. Known equipment definitions
include kind, quality, geometry, grid, suit ID, item/suit localized names, level
cap, and localized stat metadata. Unknown item or property definitions preserve
their stable IDs and values while optional metadata remains null.
`equipped_character_id` is the stable character ID used as the key in
`res/data/characters/characters.json`; it is null when the item is unequipped or
the owner has not been resolved. `equipped_character_uid` remains the
account-specific character item instance UID.
`equipped_placement` is the equipped module's 1-based anchor position. It is
null for cores, unequipped modules, or when the module position has not been
resolved yet.

## Equipment methods

Equipment methods call ABI v4 / IPC v3 of `nte-equipment-plugin` through the
local `\\.\pipe\nte-equipment-plugin-v3` named pipe. They do not inject or load
the plugin; the matching plugin build must already be loaded by `HTGame.exe`.
Every character or equipment UID uses the same `{"slot":1,"serial":2}` shape
returned by inventory snapshots. Both components must be nonzero and neither
may be `4294967295` (`u32::MAX`). Module rows and columns are 1-based and must
both be in `1..5`. Each `equip_one_key` placement
is `{"equipment":{"slot":3,"serial":4},"row":1,"column":2}`; its array must
contain 1..64 entries. `discarded` and `locked` must be JSON booleans.

The methods map directly to plugin operations:

| Method | Effect |
| --- | --- |
| `equipment.equip_module` | Equip an unequipped module at `row`,`column` |
| `equipment.equip_core` | Equip an unequipped core |
| `equipment.unequip_module` | Unequip a module from its character |
| `equipment.unequip_core` | Unequip a core from its character |
| `equipment.unequip_all` | Unequip all equipment from one character |
| `equipment.equip_one_key` | Equip 1..64 module placements plus one core |
| `equipment.move_module_to_character` | Move an equipped module to another character and position |
| `equipment.move_core_to_character` | Move an equipped core to another character |
| `equipment.set_item_discarded` | Set or clear the item's discarded flag |
| `equipment.set_item_locked` | Lock or unlock the item |

Example:

```json
{
  "jsonrpc":"2.0",
  "id":"move-1",
  "method":"equipment.move_module_to_character",
  "params":{
    "character":{"slot":1,"serial":2},
    "equipment":{"slot":3,"serial":4},
    "row":2,
    "column":5
  }
}
```

Successful results are `{"status":"rpc_dispatched"}`. The alternate
`{"status":"dry_run_ok"}` value is reserved for a dry-run plugin host. A
successful dispatch only confirms that the RPC was submitted; callers must wait
for a later captured `event.inventory.snapshot` to confirm game/server state.
Missing pipes and timeouts return `EQUIPMENT_PLUGIN_UNAVAILABLE`. Plugin
requests beyond the active call and one queued call return
`EQUIPMENT_PLUGIN_BUSY`. Plugin validation statuses other than dispatched/dry-run return
`EQUIPMENT_REQUEST_REJECTED`.

## Capture and inventory events

- `event.capture.status`: reliable capture lifecycle state.
- `event.inventory.snapshot`: reliable complete enriched inventory snapshot.
- `event.core.warning`: English capture-degradation notice.
- `event.core.error`: English capture-failure notice.

`PacketDebug`, payload previews, payload hex, decoded text, endpoints, and PCAP
content are never sent to stdout.

## Battle methods

### `battle.get_summary`

```json
{
  "jsonrpc":"2.0",
  "id":"battle-1",
  "method":"battle.get_summary",
  "params":{"subtract_time_stop":true}
}
```

`subtract_time_stop` is required and selects the same timing calculation used by
the GUI. The result is null before any combat or abyss data exists. Otherwise it
contains total duration, damage, DPS, damage taken, hit count, character rows,
skill rows, both abyss halves, and the redacted parse-quality counters. Stable
`dps_time_mode` values are `subtract_time_stop` and `wall_clock`; quality source
values are `live`, `pcapng_replay`, `json_replay`, and `unknown`.

The external battle DTO is an explicit field-by-field mapping from the internal
`CombatSessionSummary`; internal Rust serialization is not exposed as the API.
All numeric values originate from validated combat state and are finite JSON
numbers.

### `battle.reset`

Resets only battle hits, aggregates, abyss state, time-stop state, packets, and
parse-quality counters. It does not stop an active capture, remove its PCAP,
clear the most recent inventory snapshot, or change inventory generation. The
result is `{"reset":true}`.

## Battle event

`event.battle.summary` uses `subtract_time_stop=true` and the same DTO as
`battle.get_summary`, plus the global event `sequence`. Normal updates are
published at most every 250 ms (4 Hz). They use a single latest-value slot, so a
new summary replaces an older summary that the stdout writer has not consumed;
Npcap acquisition never waits on this coalescing path. Capture stop and process
shutdown clear any pending summary and enqueue one final summary through the
reliable outbound queue before the stopped/shutdown flow completes.

The event never contains individual hits, PacketDebug, payload data, endpoints,
or PCAP content.

## Typical live integration sequence

1. Start `nte-core serve --stdio` with stdin/stdout pipes.
2. Start a permanent stdout reader before sending requests.
3. Call `core.hello` and verify `protocol_version` and `capabilities`.
4. Call `capture.detect`; choose auto or one returned device name.
5. Call `capture.start` with profile `inventory` or `combat`.
6. Consume `event.capture.status`, inventory, warning/error, and battle events.
7. Query `inventory.get_latest` or `battle.get_summary` when a point-in-time
   response is needed.
8. Call `capture.stop` and wait for its response plus the final reliable events.
9. Call `core.shutdown`, close the pipes, and wait for process exit.

For a manual adapter, replace the auto selector with:

```json
{"mode":"name","name":"Npcap device name from capture.detect"}
```

For privacy-sensitive integrations that only need business DTOs, explicitly
send `"raw_capture":"disabled"`. This disables PCAP file creation without
changing combat or inventory parsing.
