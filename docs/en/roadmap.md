# Current roadmap

*English · [简体中文](../roadmap.md)*

This file holds only unfinished work that affects data structures, external capabilities or product
boundaries. Once something is finished and covered by public behaviour tests, fold the stable facts into
`features.md` or `architecture.md` and delete the entry here.

## 1. Battle reports and real-combat gains

The current battle-report input is the nte-core aggregate summary. These capabilities wait on an
official upstream capability:

- stable per-hit event IDs and pagination;
- buff/debuff state intervals;
- character, weapon, team, target-instance and scene snapshots;
- publicly reproducible historical battle export.

Coverage, single-character combined margin, team gains and combat profiling are designed only once those
official inputs exist. Debug samples do not enter the product data model.

## 2. Team gains and global loadouts

The long-term goal is to unify direct-damage margin, Cycle, Break, DOT, team buffs and equipment
competition into one explainable objective function, then extend the search to four-player teams and
eight-player dual teams. A new solver still uses the immutable snapshot, official IDs, unique UIDs,
loadout slots and the frozen-plan contract; it does not establish a second source of scoring truth.

## 3. Cloud-gaming input chain

Cloud mode in automatic assembly retains the gamepad mapping and input diagnostics for the character
list and assembly page. The controller currently pins `cloud_nte_mode=False` and the settings page shows
development status only. The blocker is that cloud-side mouse press/release, the first mouse action
after a gamepad switch, scrolling and dragging behave inconsistently within one session.

Before the entry point returns, all of the following must be done:

1. define "prerequisite screen → input → post screen → stop on timeout" for the character page,
   character list, Console page and assembly page;
2. pass three consecutive rounds each of a full single-character assembly and a re-entry with at least
   two characters;
3. release both the virtual gamepad and the mouse correctly after F12 is pressed in the list, on the
   assembly page and mid-drag;
4. pass the regular mouse-path regression, with account generation and job records auditable;
5. record real Windows results including resolution, DPI, window mode, input device, version and date.

When the entry point returns it defaults to off and is marked experimental. A partially successful
action never implies the whole input chain works.

## 4. Rewind execution on real hardware

Rewind analysis, eight-slot saving and the input chain are connected; in-game execution is still
experimental:

- the app does not yet recognise and confirm that it is on the rewind home page before starting;
- coordinate mapping, balance/price OCR and candidate outlines need to cover different resolutions,
  DPI settings, languages and graphics settings;
- the balance, ten-pull price and planned count are not yet echoed back for final confirmation before
  spending;
- in-page stop, stage resumption and job records after an interruption do not exist yet;
- account generation, the execution snapshot and window identity are not yet persisted as an auditable
  job.

The only public strategies are "Balanced" and "Focused push". The cost-plan entry point left in the
domain layer does not reach the UI; settle the business rules and public behaviour tests before adding a
strategy.

## 5. Test-server asset wrap-up

Static schema v16 already contains the catalogue, growth, skills, awakening, recommended weights and
graduation templates for characters 1036 and 1072. Icons for the new weapons `fork_DemonBlade` and
`fork_GoldRecord` are still marked explicitly as `unresolved_assets` in
`assets/game_ui/manifest.json`. Once an official export is available, re-run the asset build and
`tests.test_game_ui_assets` — do not substitute a lookalike image.

## 6. Acceptance principles for new capabilities

A new capability settles its payload, sequence/generation, completeness, error codes, privacy fields,
failure states and rollback method before it reaches an integration or the account database. A roadmap
entry becomes a current capability only once the implementation, migration, caller consolidation and
public behaviour tests are all complete.
