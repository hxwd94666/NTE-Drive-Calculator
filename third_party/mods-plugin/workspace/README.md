# NTE Mods Plugin workspace

`nte-dps-tool.exe` reads the optional in-game NTE Mods Plugin and its restricted
script templates from this directory:

```text
plugins/
  dwmapi.dll
  mods-plugin.version
  nte-mods.enabled
  nte-mods/
    equipment.nte
    combat-clock.nte
  examples/
    character-telemetry.nte
    reflection-events.nte
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
saved script and enable-set changes every 250 ms while the game is running.
Each update compiles the complete candidate set before swapping it in. Invalid
source keeps the last working programs, and a trapped runtime fault pauses only
the affected Mod until the next successful hot update. The minimizable Mod
Workshop runtime console shows these lifecycle messages and output from
`nte::log::info("message")`.

`nte-mods.enabled` lists the active scripts. Each `.nte` file is restricted
NTE C++ v5: normal C++ declarations, braces, semicolons, namespace-qualified
host APIs, persistent global integers, arithmetic and bit operations, nested
`if/else if/else`, bounded `for`, typed checked memory reads and writes,
generic UFunction reflection, bounded ProcessEvent subscriptions, whitelisted
SDK reads, stable `nte::game::*` session values, and custom IPC events.
The DLL compiles this C++ subset to a fixed-size VM program before installing its
shared hook. Shared session entry points stay behind
`NTE_REQUIRES("game.session")`;
feature-specific offsets, UFunction parameter layouts, sampling rules, cache
keys, and event formats remain in Mod source instead of requiring a new DLL
service or DLL build. After the runtime ABI is installed, a new Mod consists
only of its `.nte` and resources.

The two built-ins contain their own documented control flow rather than thin
calls into hard-coded features. `equipment.nte` owns cache retry/readiness and
IPC activation. `combat-clock.nte` owns state transition detection and
forwarding. The DLL host APIs remain bounded primitives for stable session
objects, checked reads, one cache preparation attempt, authoritative pause
fields, one transition record, and IPC transport.

`enemy-telemetry.nte` is a research script listed by the research branch's
default `nte-mods.enabled`. Starting from the generic
`nte::game::player_controller`, it uses bounded `nte::memory::*` primitives to read each
attack target and its HP. `nte::cache::remember` locks the first stable lowercase
ASCII FNV-1a character-config hash for every target key, while generic
`nte::ipc::emit` records publish identity and vitals for multiple targets. The DLL
has no enemy-specific service, IPC operation, or response structure. The
desktop resolves hashes through `res/data/enemies/enemies.json` and only
projects the localized name and portrait when exactly one captured HP stream
matches; ambiguous identical streams stay unidentified. Remove `load
enemy-telemetry` from `nte-mods.enabled` to stop it.

During live capture, the desktop program consumes `nte::ipc::emit` records through
its shared engine pipeline. Names prefixed with `pre.` and `post.` become typed
preprocess and postprocess messages; other names remain ordinary Mod events.
This keeps script-to-program interaction ordered and independent from the Mod
Workshop UI.

Remove either `load equipment` or `load combat-clock` to run only the other
built-in mod. Keep only the `nte_mod_set 1` header to leave the proxy loaded
with every game hook and IPC feature inactive. These changes are applied at
runtime; disabling the last Mod removes the shared hook and closes IPC. The complete language and
host-API reference is in `native/nte-mods-plugin/README.md`.

`python plugins/examples/query_mod_events.py --follow` prints the generic
event ring as newline-delimited JSON while the game and an IPC-capable Mod are
running. `character-telemetry.nte` is a complete custom-Mod example: copy it
into the software-side `plugins/nte-mods` directory, add `load character-telemetry` to
`nte-mods.enabled`, then use the query client to observe the raw
`pre.character.health` fields and derived `post.character.health` value. The
query client uses only the Python standard library.

`reflection-events.nte` is the generic-host example. It resolves
`IsGamePausedByType` by class and function name, constructs the UFunction
parameter buffer, calls it through ProcessEvent, subscribes to the same
function, reads the captured parameters, and publishes a Mod event. No
function-specific C++ service or IPC operation is involved.
