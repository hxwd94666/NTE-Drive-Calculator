# External integrations and extensions

*English · [简体中文](../integrations.md)*

This document defines the boundary external capabilities cross when entering the project. Business
ownership and lifecycle are in [Architecture](architecture.md); real-environment steps are in
[Windows acceptance](validation/windows.md).

## 1. nte-core

`src/integrations/nte_core.py` owns the stdio protocol, the process, events and error adaptation. It
does not own scoring, loadouts or UI. The capabilities the product currently uses are:

- `capture.detect`;
- `capture.start(profile="inventory"|"combat")` and `capture.stop`;
- `inventory.get_latest`;
- warehouse state write-back and fast-assembly RPCs;
- `event.battle.summary`, `battle.get_summary`, `battle.reset`.

`inventory.get_latest` reads the most recent capture result — it is not a forced refresh. Full inventory
events feed snapshot stabilisation; partial state events only update UIDs already known in the pinned
full snapshot. An accepted RPC means submitted, nothing more: the final state is confirmed by a later
stable snapshot or an official scoped event.

The nte-core capture diagnostic on the settings page calls only `capture.detect`, then supplements it
with read-only Windows probes summarising the Npcap driver service, enabled adapters, Npcap installation
traces and the usual `wpcap.dll`/`Packet.dll` locations. It does not create a capture session, does not
read or write network configuration, and does not output MAC addresses, IPs, remote addresses, full
local paths or the core capability list. When `capture.detect` returns zero devices, the report gives a
next step based on missing installation, driver service, no active adapter, or "driver/filter/runtime to
be investigated". When the core supplies no libpcap enumeration error text or per-adapter filter reason,
the report must state that boundary explicitly rather than invent a cause.

Per-hit pagination, buff/debuff intervals, complete growth/weapon snapshots, four-player and dual-team
setups, enemy instances, official scene IDs and historical per-hit export are not yet product contracts.
Debug samples do not substitute for a public CLI capability.

## 2. Vision, OCR and game input

`src/integrations/vision` exclusively owns window coordinates, screenshots, cell detection, mouse actions
and post-scan state sync; `src/scanner` and the parsing services own OCR, normalisation and equipment
fields. An integration returns screenshots, indexes, parse results or diagnostics. It does not write
business snapshots and does not decide scoring or retention rules.

Mouse and virtual-gamepad full scans both commit as a unified `vision` snapshot. In-game automatic
assembly and the experimental cloud mode use the same public input contract; pages never create a mouse
backend or a virtual gamepad. Cloud mode is currently forced off, keeping only the mapping and
diagnostic code.

A new input backend must define: window identity, physical pixel coordinates, the prerequisite page, the
action sequence, the visible post-state, cancellation checks, input release, timeout and rollback. When
post-confirmation is missing, stop the current task rather than blindly clicking again.

## 3. Binaries and the plugin

`nte-core.exe`, `dwmapi.dll` and the plugin copy in the root directory are local files. `third_party`
holds only release components that were explicitly promoted; before promotion, record the upstream
commit, version, licence and SHA-256, and complete protocol, packaging and real Windows verification.

The mods plugin's runtime SDK cache lives in a writable workspace and never enters Git or the release
template. Presence, IPC pipe, dynamic SDK and hook troubleshooting after a game update is covered by the
assembly-plugin version adaptation reference.

## 4. Static data and assets

Official data only ever produces a candidate static database through `tools/game_data`. The importer
retains the source file, row keys and digests, and validates schema, foreign keys, business constraints
and the manifest before replacing the release database. The static database and `data/manifest.json` are
reviewed as one atomic change.

Game UI images are generated into `assets/game_ui` by the asset build tool, and `manifest.json` records
the ID mapping, file hashes and `unresolved_assets` explicitly. A missing asset stays explicitly
unresolved — never a lookalike image or a local path placeholder.

An account schema change adds a migration and covers creation, upgrade, failure rollback and retry. DAOs
own SQL exclusively; snapshot cleanup goes through the public DAO and protects every reference.

## 5. New pages and shared components

Implement the Page/View and Controller under `src/features/<feature>/`, register navigation in
`src/ui/navigation.py`, and inject narrow dependencies from the composition root. Shortcuts use the
navigation key, not a stacked-page number. Cross-feature reuse starts with a service, an immutable
contract or a shared component in the composition root — never forwarding through MainWindow fields or
another controller.

## 6. New algorithms, sync methods and assembly methods

- A new algorithm reuses official IDs, the pinned snapshot, candidate objects, loadout slots and the
  saved-plan payload; its output contains real UIDs, target positions, per-item scores, the source
  snapshot and the algorithm/profile version.
- Multi-character competition is handled by the unified allocator, not a per-character loop that mutates
  the inventory.
- A new sync method provides waiting, collection, completeness, stable listening, single-transaction
  commit and explicit error states; it only commits complete snapshots.
- A new assembler consumes frozen saved plans only and never re-optimises during execution. It validates
  source, character, slot, UID and position before executing, and produces a confirmable new state
  afterwards.
- Bulk side effects record progress through a persistent job, character/slot items and events.
