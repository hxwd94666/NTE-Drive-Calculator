# System architecture

*English · [简体中文](../architecture.md)*

This document describes the current stable structure and the shared data flow. Mandatory development
gates are in [`AGENTS.md`](../../AGENTS.md); business detail is in [Feature principles](features.md).

## 1. Overall data flow

```text
nte-core full inventory ─┐
                          ├→ completeness and stabilisation → immutable account snapshot → frozen calculation context
vision/gamepad full scan ─┘                                                                  ↓
                                                                                       loadout preview
                                                                                             ↓
                                                                        saved to a chosen character's loadout slot
                                                                                             ↓
                        nte-core fast assembly / in-game automatic assembly / warehouse state write-back
                                                                                             ↓
                                                                          later observable state confirmation

nte-core partial state events → runtime state overlay for UIDs already known in the pinned full snapshot
```

Every downstream operation resolves the current snapshot once at the start and freezes its
`snapshot_id`. Historical plans are read against their own `source_snapshot_id`. A new snapshot
appearing mid-run is used only for side-effect confirmation and never changes the original request's
inputs.

## 2. Layers and dependencies

```text
UI Page / View
      ↓
Controller + frozen dependencies
      ↓
Application Service
   ↙                 ↘
Domain / Optimizer       DAO / Integration
                              ↓
                    SQLite / nte-core / files / OCR / game input
```

| Layer | State it owns | Responsibilities it excludes |
| --- | --- | --- |
| UI | Widget values, selections and discardable display state | SQL, protocols, business algorithms, other pages' state |
| Controller | Workers, cancellation tokens, busy state, result projection | DAO query detail, other controllers |
| Service | Frozen requests, business orchestration, transaction boundaries | Qt pages, MainWindow, global account lookups |
| Domain/Optimizer | Pure rules and solving over immutable inputs | SQLite, Qt, logging, processes |
| DAO | Schema, migrations, SQL, transactions | UI, external protocols |
| Integration | nte-core, OCR, files, mouse/gamepad, plugin and processes | Scoring and saving policy |
| Observability | Sinks, redaction and operation correlation | Features, services, DAOs and UI |

`src/ui/app.py` is the GUI composition root. `AppContext`, `EquipmentPresentation` and
`GlobalHotkeyManager` are created and injected explicitly there. MainWindow only composes navigation,
account switching and lifecycle; features never locate services by scanning fields or indexing pages.

## 3. Application and account context

`src.app.context` provides:

- `ApplicationPaths` — release resources, static database, shared database and global config paths;
- `AccountContext` — the current account's database, config, screenshot and log directories;
- `AppContext.generation` — the account-switch generation counter;
- `AccountLifecycle` — stopping, rebuilding and resuming background capabilities.

A long-running task freezes the account ID, user database path, generation, snapshot, static dataset,
profile/config version, loadout `slot_id`, lock snapshot, token and output directory. It re-checks them
before writing or calling back; stale results never reach a new account's pages or database.

An account switch stops background tasks, replaces the context and increments the generation, rebuilds
narrow services, clears per-account page caches, and then resumes the services allowed to run
automatically. Inventory sync, battle-report capture, scanning, appraisal and game input coordinate
exclusive resources through public lifecycle hooks.

## 4. Data domains

| Data domain | Path | Current schema | Ownership |
| --- | --- | ---: | --- |
| Release static | `data/game_static.sqlite3` | 16 | Official catalogue, growth, skills, damage, recommended weights and graduation templates; read-only at runtime |
| Public shared | `data/app_shared.sqlite3` | 2 | Release baseline and cross-account overrides for official characters' extra shapes |
| Application global | `config/global_ui_preferences.json` | JSON | Cross-account theme |
| Account private | `accounts/<account_id>/user_data.sqlite3` | 21 | Snapshots, weights, custom characters, preferences, loadout slots, locks, jobs and battle reports |

Read priority is: the account's explicit config, then public overrides that permit sharing, then release
defaults. Official extra shapes may cross accounts; custom characters, base weights, calculation
preferences, rewind preferences, plans, locks and jobs belong to the current account only. Schema
migrations are append-only.

## 5. Snapshots and source capabilities

The inventory table structure is uniform; source capabilities are not:

- `nte_core` — real equipment UIDs, character instances, reliable equipment state, warehouse RPC and
  fast assembly;
- `vision` / legacy `gamepad` — temporary UIDs for analysis, usable for the warehouse, calculation,
  historical display, rewind and in-game automatic assembly;
- runtime state deltas — these only overlay the locked, discarded and equipped state of UIDs already
  known in a native full snapshot; they never add items or advance the current pointer.

Source permissions are decided through the public capability helper, never inferred from a non-empty
UID, the table structure or a UI name. Snapshot cleanup protects the current snapshot, active loadout
slots, locked plans and every reference held by unfinished jobs.

## 6. Loadout slots, plans and locks

Each character has at most three visible loadout slots keyed by a stable `slot_id`. `primary` is the
default slot for legacy data and its display name can be changed. A slot points at one current plan;
older plans are kept as history. A plan freezes its source snapshot, assignments, per-item scores,
Cartridge max-level values and source type.

A calculation lock belongs to a specific slot plan in the current account and is not the same as the
in-game equipment lock. Locked real UIDs are excluded together before candidates are built, and checked
again in the DAO save transaction. Bulk assembly selects each character's slot explicitly and requires
a unique character, a valid slot, a native source and no cross-character UID conflicts.

## 7. Side-effect confirmation

An accepted RPC, a finished input action, a button state change and a dispatched job are none of them
final success. Warehouse state write-back and assembly must retain the pre-operation baseline and wait
for an increasing stable snapshot or a scoped official event afterwards, then verify the target UID,
character instance, position and state.

Mouse-driven state management after a vision scan is a special session capability: it uses only this
scan's index and re-checks each item's icon before and after the operation. It does not grant temporary
UIDs any later write-back capability. A timeout produces a pending-confirmation or failure report, never
a fake success.

## 8. Navigation and lifecycle

Top-level navigation is Dashboard, Calculate, Loadout, Characters, Warehouse, Appraisal, Battle report,
Toolbox and Settings. Character blueprints and base weights are character sub-pages that keep the parent
navigation highlighted through `parent_key`. Account switching, page destruction and application exit all
go through public stop entry points, invalidating results from old tokens and old generations.
