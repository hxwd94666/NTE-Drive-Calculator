# Windows manual verification checklist

*English · [简体中文](../../validation/windows.md)*

This file collects every acceptance step that depends on real Windows, the game, drivers, the plugin or
a human judging what is on screen. Pure rules already covered by automated tests are not repeated here.
Maintainers pick sections by change scope; a release runs every applicable section.

## 1. How to use it

The read-only validator sends no game input:

```powershell
python tools/windows_validation/run_validation.py --profile all `
  --account-db <account database path> --log-dir <account log directory>
```

To check only the startup environment, static database and report output:

```powershell
python tools/windows_validation/run_validation.py --non-interactive --profile startup
```

Reports are written to `build/windows-validation/<timestamp>/`; they never enter the installer and are
never uploaded automatically. When a real operation has side effects, prepare the test account, target
character and plan first. The report records pass, fail or not-applicable — an unexecuted item is never
counted as a pass.

## 2. Environment record

| Item | Record |
| --- | --- |
| App version and commit |  |
| Test date |  |
| Windows version |  |
| Started as administrator | yes / no |
| Resolution, DPI, window mode |  |
| Input device | mouse / virtual gamepad / other |
| nte-core version |  |
| Mods plugin version and SHA-256 |  |
| Npcap status |  |
| Number of test accounts |  |
| Change scope |  |

Result markers: `[x]` pass, `[ ]` not run, `[FAIL]` failed, `[N/A]` not applicable to this environment or
change.

## 3. Startup, logging and account lifecycle

- [ ] Starting as administrator reaches the dashboard with no startup error dialog; title, account,
  environment and current snapshot are readable.
- [ ] Exit normally and restart; the account, global theme and account settings persist, with no leftover
  background worker or nte-core process.
- [ ] Enable verbose runtime logging; a new `nte_runtime_YYYYMMDD_HHMMSS[_N].log` is created immediately.
- [ ] Disable then re-enable; the old file stops being written, the new session creates a new file, and
  the persistent `nte_runtime.log` keeps recording INFO.
- [ ] Switch accounts with logging on; the old account's session ends and new events go only to the new
  account's directory.
- [ ] With no background task, go A → B → A; pages, user database, snapshot, character configuration,
  slots and log paths stay account-isolated.
- [ ] Try switching during a calculation, scan, battle report or assembly job; the job stops or is
  blocked per the public rules, and old-generation results never land.
- [ ] Check the logs: no account display name, absolute user path, complete RPC, full OCR text, UID list,
  CDK, token or authentication URL.

Notes:

```text

```

## 4. nte-core sync and runtime state

- [ ] With nte-core and Npcap healthy, start inventory sync; the state goes through collecting, changed
  and listening, and commits a complete stable snapshot.
- [ ] Stop and restart; there is exactly one capture process and a new stable snapshot can be obtained.
- [ ] Start with nte-core or Npcap missing; a locatable error is shown and no fake snapshot is created.
- [ ] Exit during sync; the process stops and the account database has no half-committed snapshot.
- [ ] After a full snapshot, trigger an event containing only part of the equipment state; warehouse
  state updates while the item count and `snapshot_id` stay unchanged.
- [ ] Send or observe an old sequence or unknown UID; old state is not written back and unknown items do
  not enter the inventory.
- [ ] Enable battle-report capture and inventory capture together; the app coordinates by lifecycle and
  never runs two sessions competing for capture.

Notes:

```text

```

## 5. Vision and gamepad full scans

### 5.1 Report and database cross-check

```powershell
python tools/windows_validation/run_validation.py --profile vision `
  --mouse-scan-report <account screenshot dir\mouse_scan_last_report.json> `
  --account-db <account dir\user_data.sqlite3> --log-dir <account log directory>
```

- [ ] `mouse-visual-runtime`, `mouse-visual-scan-report` and `mouse-visual-current-snapshot` are all
  `passed`.
- [ ] The report has `inventory.expected == inventory.captured`, contiguous pagination indexes, and the
  current account snapshot is `source=vision`, `capture_driver=mouse` with a matching count.
- [ ] The report contains only resolution, profile, pre-check, pagination, wheel, counts, duration and
  the safe termination state.

### 5.2 Mouse scanning

- [ ] Run the same full inventory scan three times at the device's current resolution; the manual count,
  report count and account snapshot count all agree.
- [ ] The first screen scans 4 rows and later pages add 3; a final row with fewer than seven items does
  not click empty cells.
- [ ] Pagination uses profile A, with profile B cumulative compensation every sixth page turn; no
  duplicate, missed or misaligned clicks.
- [ ] When the first frame is on the wrong tab, unloaded or occluded, the pre-check stops before the
  first click.
- [ ] Serial and dual-thread parsing produce the same equipment content fingerprint; the dual-thread
  queue neither reorders nor drops items.
- [ ] Press F12 during clicking, scrolling and parse backlog respectively; the mouse button is released,
  the job stops and no new current snapshot is created.
- [ ] Change window size, focus or account generation mid-scan; the job stops and the previous current
  snapshot is kept.
- [ ] Complete a mouse scan and a gamepad scan one after the other; the account database always has
  exactly one current snapshot and results are not merged.

The project supports coordinate-mapping tests for 1920×1080, 2560×1440 and 3840×2160. Real hardware
records only the resolutions available on that machine — simulated scaling is not real-device input
verification.

### 5.3 Post-scan lock/discard management

- [ ] Generate a plan from this scan's management configuration; execution runs in reverse index order
  and unplanned equipment is never clicked.
- [ ] Direct switches between normal, locked and discarded all recognise the correct icon before and
  after the action.
- [ ] Going locked → discarded shows the confirmation dialog, which disappears once confirmed, and the
  final icon is discarded.
- [ ] Deliberately create a pre-operation state mismatch; execution stops before that target and later
  targets do not continue.
- [ ] Deliberately occlude the detail view, move the window or press F12; input stops and
  `mouse_state_sync_last_report.json` is produced.
- [ ] Open the vision warehouse afterwards; temporary UIDs remain read-only and the warehouse offers no
  deferred state write-back.

### 5.4 Appraisal

- [ ] Appraise via screenshot, clipboard, manual input and the warehouse single-item entry point; all
  reach the same scoring and display flow.
- [ ] Start appraisal while a scan hotkey session is active, and vice versa; the owner-conflict message
  is clear and stopping affects only your own session.

Actual resolution/DPI, inventory count, three report files:

```text

```

## 6. Calculation, custom characters and loadout slots

- [ ] Pin one stable snapshot and run ordered/consistent mode; results, sets, blacklist and CRIT
  constraints match the page's request.
- [ ] Change page inputs mid-calculation; the worker keeps using the frozen request and saving consumes
  only the original preview.
- [ ] When saving, pick an existing slot, a new slot and skip for different characters; the comparison
  baseline matches the save target.
- [ ] Create up to three visible slots per character and rename them; a fourth is blocked, and order and
  current plan persist across restart.
- [ ] Lock an alternative slot; the next calculation excludes its UIDs, and overwrite, archive and
  replacement are all blocked.
- [ ] Introduce invalid input for a later character during a bulk save; the whole batch rolls back with
  no partial slot updates.
- [ ] Import from the game into a chosen slot; source, snapshot, per-item scores and Cartridge max-level
  values are frozen and other slots are untouched.
- [ ] Changing account base weights affects only the current account; after changing a shared extra
  shape, other accounts read the same shared override.
- [ ] Create a custom character and save weights, extra shapes, default set and the 20-cell chassis; it
  can enter vision calculation and automatic assembly but not the official character page, in-game
  loadout import or fast assembly.
- [ ] Return to the character page after generating a blueprint; the parent navigation stays highlighted.

Notes:

```text

```

## 7. Warehouse and appraisal projection

- [ ] Open the warehouse on a native stable snapshot; equipment, character ownership and runtime overlay
  state are correct.
- [ ] Filter Cartridges and Modules by official set/shape/attribute ID; OR within a group, AND across
  groups, and reset restores everything.
- [ ] Plan-reference display includes multiple current loadout slots and does not count historical plans
  as current references.
- [ ] Change lock/discard state; after the RPC is submitted it waits for a new full snapshot to confirm,
  and shows pending on timeout.
- [ ] Open the vision warehouse; state falls under "other" and the general warehouse write-back entry
  stays read-only.
- [ ] Warehouse single-item appraisal and same-category comparison use the shared display, with no
  duplicate appraisal page entry point.

Notes:

```text

```

## 8. Fast assembly and in-game automatic assembly

This section modifies in-game equipment. Record the test character, instance, slot and source snapshot.

- [ ] Run fast assembly for a single character with an explicit slot; pre-check, dispatch, confirmation
  and job events are complete.
- [ ] Choose slots for several characters; a repeated character or a cross-character UID conflict stops
  before dispatch.
- [ ] Observe confirmation by an increasing full snapshot; character, instance, position and the complete
  equipment set match the plan.
- [ ] Simulate a full-snapshot wait timeout that still produces a cursor-bound scoped equipment event;
  the report marks it as scoped confirmation and does not describe it as full position/ownership
  verification.
- [ ] Create a clear scoped-event mismatch; only that character retries, and after at most three attempts
  the specific difference is reported.
- [ ] Start with the plugin, character instance or native source missing; it stops before writing and
  distinguishes the error types.
- [ ] Run in-game automatic assembly with a regular mouse; character recognition, unequipping, filtering,
  dragging and returning all match the slot plan.
- [ ] Press F12 during automatic assembly; the mouse/gamepad is released at a safe checkpoint and later
  characters do not run.
- [ ] The settings page keeps cloud mode in development status and the normal flow passes
  `cloud_nte_mode=False`.

When cloud-mode code changes, additionally run: three rounds of the full single-character path, three
rounds of two-character re-entry, the three stop points (list, assembly page, before drag), and the
regular mouse regression. Do not restore the user-facing switch until the roadmap threshold is met.

Test characters, slots, job IDs and results:

```text

```

## 9. Rewind recommendation and experimental execution

- [ ] Save built characters, push characters, strategy, target grade and the eight-slot plan on account
  A; account B does not inherit those preferences.
- [ ] Generate both "Balanced" and "Focused push" from the active slot plans; per-item scores come from
  the plan payload and equipment resolves against the plan's source snapshot.
- [ ] More than eight shapes with a positive shortfall returns a notice and no partial fake plan.
- [ ] Verify eight different = 80, four shapes twice each = 120, eight identical slots = 360, and the
  12.5% chance per slot.
- [ ] Clear the current candidates and reopen; the account's saved eight slots still restore and
  inventory counts refresh from the current pinned snapshot.
- [ ] Before running the input chain, manually open the rewind home page and record resolution/DPI.
- [ ] Each rarity switches difficulty first and then reads that difficulty's balance; balances are not
  reused across difficulties.
- [ ] Beginner uses the fixed random ten-pull of 600; purple/gold custom reads the ten-pull price area on
  the right and never the single-pull price.
- [ ] Press F12 during difficulty switching, candidate configuration and the ten-pull loop; subsequent
  clicks stop.

The experimental execution gaps remain governed by [the roadmap](../roadmap.md); do not mark it a stable
capability until the real-hardware threshold is met.

## 10. Plugin, environment, updates and release

- [ ] Environment diagnostics separately show Npcap, nte-core, dwmapi, plugin presence, SDK cache and IPC
  pipe status.
- [ ] Perform plugin deployment, backup and restore in a test environment; a failure preserves the
  original DLL and the runtime SDK cache.
- [ ] After a game update, verify the DLL hash, workspace, SDK rebuild, presence, pipe and one controlled
  assembly per the plugin adaptation reference.
- [ ] Run the Mirror update check, cancel, failure retry and installer launch; logs contain no CDK, token
  or authentication URL.
- [ ] Run the packaging tests and confirm the Windows validator, local database, logs, screenshots, SDK
  cache and old installer output do not enter the installer.
- [ ] Check the release static database and manifest SHA-256, and confirm unresolved assets are still
  recorded explicitly.

## 11. Final conclusion

| Conclusion | Selection |
| --- | --- |
| Pass — release/merge may continue |  |
| Non-blocking issues, recorded |  |
| Fail — stop the release/merge |  |

Failures, reproduction steps, reports and log file names:

```text

```
