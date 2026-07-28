# NTE Mods Plugin workspace

`nte-dps-tool.exe` reads the optional in-game NTE Mods Plugin and its restricted
script templates from this directory:

```text
plugins/
  dwmapi.dll
  nte-mods.enabled
  nte-mods/
    equipment.nte
    combat-clock.nte
  examples/
    character-telemetry.nte
    query_mod_events.py
```

Building `native/nte-mods-plugin/nte-mods-plugin.sln` stages the
compiled DLL here automatically. Windows GUI release archives include this
directory beside `nte-dps-tool.exe`. Only `dwmapi.dll` is a Windows module;
the `.nte` files are validated data programs interpreted by that DLL.
Keep the complete `plugins` directory beside the executable when replacing a
local build. The desktop program rejects an older DLL that lacks any host API
required by the bundled scripts instead of installing mismatched components.
Only `dwmapi.dll` is copied beside `HTGame.exe`. The desktop program registers
this software-side directory as the active workspace, and the DLL watches
saved script and enable-set changes while the game is running.

`nte-mods.enabled` lists the active scripts. Each `.nte` v4 file uses NTE
Script v4 with variables, per-mod persistent state, arithmetic and bit
operations, nested `if/elif/else`, bounded `for range`, typed read-only memory,
whitelisted SDK reads, stable `game.*` session values, and custom IPC events.
The bundled default enables only `equipment.nte`; `combat-clock.nte` remains
available as an optional example and is not needed for fast equipment apply.
The DLL compiles the source to a fixed-size VM program before installing its
shared hook. Client offsets stay behind `requires("game.session")`; normal Mod
source uses `game.player_state`, `game.player_controller`, or
`game.player_character` instead of reproducing pointer chains.

The two built-ins contain their own documented control flow rather than thin
calls into hard-coded features. `equipment.nte` owns cache retry/readiness and
IPC activation. `combat-clock.nte` owns state transition detection and
forwarding. The DLL host APIs remain bounded primitives for stable session
objects, checked reads, one cache preparation attempt, authoritative pause
fields, one transition record, and IPC transport.

During live capture, the desktop program consumes `ipc.emit` records through
its shared engine pipeline. Names prefixed with `pre.` and `post.` become typed
preprocess and postprocess messages; other names remain ordinary Mod events.
This keeps script-to-program interaction ordered and independent from the Mod
Workshop UI.

Remove either `load equipment` or `load combat-clock` to run only the other
built-in mod. Keep only the `nte_mod_set 1` header to leave the proxy loaded
with every game hook and IPC feature inactive. These changes are applied at
runtime; disabling the last Mod removes the shared hook and closes IPC. The complete language and
host-API reference is in `native/nte-mods-plugin/README.md`.

`python plugins/examples/query_mod_events.py --follow` prints the generic v4
event ring as newline-delimited JSON while the game and an IPC-capable Mod are
running. `character-telemetry.nte` is a complete custom-Mod example: copy it
into the software-side `plugins/nte-mods` directory, add `load character-telemetry` to
`nte-mods.enabled`, then use the query client to observe the raw
`pre.character.health` fields and derived `post.character.health` value. The
query client uses only the Python standard library.
