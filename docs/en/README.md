# NTE Drive Calc Developer Documentation

*English · [简体中文](../README.md)*

The repository contract is [`AGENTS.md`](../../AGENTS.md) in the root directory. This directory holds only
system principles, existing features, external boundaries, unfinished work and real-environment
acceptance. Identify your task type first, then open the matching document.

## Document map

| Document | When to read it |
| --- | --- |
| [Architecture](architecture.md) | Understanding layering, the composition root, data domains, snapshots, loadout slots and side-effect confirmation |
| [Feature principles](features.md) | Changing sync, scanning, calculation, loadouts, the warehouse, rewind, assembly, battle reports or settings |
| [External integrations](integrations.md) | Wiring up nte-core, the plugin, OCR, mouse/gamepad, static data or a new algorithm |
| [Current roadmap](roadmap.md) | Checking whether a capability is still in development and what upstream condition blocks it |
| [Damage calculation rules](reference/damage-calculation.md) | Changing direct damage, DOT, Cycle, Break, enemy attributes or skill tiers |
| [Logging event specification](reference/logging-events.md) | Adding structured events, runtime logs or redacted fields |
| [UI localisation](reference/localization.md) | Adding UI copy, switching language or maintaining game-term display names |
| [Windows acceptance](validation/windows.md) | Verifying the real game, Modules, the plugin, scanning, assembly and updates |

## Maintenance rules

- Current capabilities go only in `features.md`; unfinished work goes only in `roadmap.md`.
- Architectural facts go only in `architecture.md`; concrete formulas and fields go in `reference/`.
- Real-device steps all live in `validation/windows.md`; no per-feature acceptance checklists.
- Once a feature ships, fold its stable contract into the architecture/feature docs and delete the
  implementation plan, investigation notes and phase-by-phase logs.
- One fact keeps one authoritative location; every other file references it by relative link.
- All documents are UTF-8. Check relative links and `git diff --check` before committing.

## Translation note

This directory mirrors the Chinese documentation. The Chinese files remain authoritative: the codebase
writes comments and logging text in Chinese by convention, so update the Chinese document first and
mirror the change here.
