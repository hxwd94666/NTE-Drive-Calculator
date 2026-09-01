# Damage calculation rules (project gold standard)

*English · [简体中文](../../reference/damage-calculation.md)*

This file records the damage calculation rules the project currently treats as confirmed. The official
static game data supplies skill multipliers, level curves, damage attributes, fixed CRIT rates, enemy
attributes and Cycle constants; these rules define how those inputs enter each multiplier zone. The
formulas follow the user-confirmed project gold standard. Official files are used to fill in traceable
values, and unconfirmed runtime behaviour never rewrites the project rules in reverse.

Percentages in the Python API are decimals — `20%` is written `0.20`.

## Data trust levels and the SQLite boundary

Data falls into three categories that must not be conflated:

1. **Confirmed by official files** — readable directly from the game's official static files, with the
   source file, row key and SHA-256 retained in SQLite.
2. **Project gold standard** — the damage formulas, multiplier-zone classification and defaults recorded
   here, implemented by the project according to confirmed rules.
3. **Unconfirmed mappings** — the official files contain the raw arrays, but there is not enough evidence
   to interpret an array tier as a specific character or skill level. Such data keeps only its
   `source_tier`; the level is never guessed.

The release database is `data/game_static.sqlite3`, currently at overall schema v16. The combat base
tables were introduced by static migration v3. The current calculation reads:

- `combat_level_curve`, `combat_level_curve_point` — the exact Break level curve and official Cycle
  tiers.
- `reaction_definition`, `combat_effect_constant` — Cycle element combinations, default damage effects
  and fixed constants.
- `skill_damage`, `skill_damage_rate` — skill damage attributes, fixed CRIT rate, Break values and
  ATK/HP/DEF multipliers.
- `enemy_combat_profile`, `enemy_element_resistance` — enemy defence, Break cap and per-element
  resistances.

The calculation service stays a pure function and never queries SQLite directly; a higher data-assembly
service or DAO converts static data into calculation inputs.

## Overall direct-damage formula

```text
final direct DMG = skill DMG multiplier
                 × scaling stat
                 × DMG bonus zone
                 × crit zone
                 × defence zone
                 × resistance zone
                 × vulnerability zone
                 × product of all independent zones
```

The skill DMG multiplier comes from the recorded skill damage data. The scaling stat is specified by the
skill record and defaults to ATK, but may be HP or DEF. The damage attribute for that attack is also
specified by the skill record — Chaos or Cosmos, for example.

## Base attribute zone

```text
ATK = AtkBase × (1 + AtkUp) + AtkAdd
HP  = HPBase  × (1 + HPUp)  + HPAdd
DEF = DefBase × (1 + DefUp) + DefAdd
```

## DMG bonus zone

```text
DMG bonus zone = 1 + Σ(damage increases)
```

Damage increases add together in this zone: universal DMG bonus, element-specific bonuses,
skill-specific bonuses, state-specific bonuses and the bonuses provided by various buffs.

## Vulnerability zone

Vulnerability is a debuff on the enemy, usually written in skill descriptions as "increases damage
taken". It does not count towards our DMG bonus zone; it sums into its own separate zone:

```text
vulnerability zone = 1 + Σ(enemy damage-taken increases)
```

## Crit zone

```text
crit zone = 1 + CRIT rate × CRIT DMG
```

The fixed 50% CRIT rate rule for DOT and some Cycle damage is handled per damage category.

## Defence zone

The default scene is Outer Realm:

```text
enemy defence = (enemy level + 90) × (1 - DEF penetration) × (1 - DEF reduction)
defence zone  = (our character level + 100) / (enemy defence + our character level + 100)
```

The open-world scene only changes the enemy defence base to `enemy level + 100`. Further scenes should
add their own explicit base rule.

## Resistance zone

The default Boss base resistance is 20%:

```text
X = Boss base RES - RES-reduction buffs on the Boss - our character's RES-penetration buffs

X >= 0: resistance zone = 1 - X
X <  0: resistance zone = 1 - X / 1.10
```

Negative resistance moves into the damage-increase range. RES reduction comes from skills and enemy
weaknesses; RES penetration comes from weapons and skills.

## Independent zones

Independent damage increases do not add into the DMG bonus zone; they multiply item by item:

```text
independent zone = Π(1 + each independent increase)
```

Known sources include Infusion's damage increase, Sakiri's talent Turbid Burn enhancement, and — while
Turbid Burn is active — "for each damage-over-time state on the target, damage over time taken
increases by 25%, up to 100%". For example, Infusion 20% plus Sakiri's talent 25% gives an independent
zone of `1.20 × 1.25`.

## DOT damage

DOT (Requiem's nightmare damage, for example) resolves once per second. Its multiplier is recorded in
the skill database and its base zones match direct damage, but the CRIT rate is fixed at 50%:

```text
DOT per-tick damage = the direct-damage formula with CRIT rate fixed at 0.50
```

DOT has a maximum stack count. The current convention resolves per stack: on resolution the current
stacks are cleared, each stack contributes its own remaining duration multiplied by the per-tick damage,
and the results are summed.

```text
total DOT damage = Σ(remaining duration of stack i × DOT per-tick damage)
```

Ticks are currently treated as one per second, so remaining duration is in seconds. This DOT resolution
convention can be replaced once more precise rules are known.

## Cycle base rules

Cycle triggers when a character fills the Cycle bar and then switches to a character of an adjacent
element, producing the outgoing character's QTE and its effects. Cycle ownership goes to whichever
participant has the higher `Cycle intensity × character level multiplier`; that character determines the
level, Cycle intensity, defence and resistance multipliers for Cycle damage such as Genesis and Turbid
Burn.

```text
Cycle intensity multiplier = 1 + Cycle intensity / 600
```

The `/ 600` here means "Cycle intensity / 6" is a percentage damage increase. The extra coefficient used
by Overlay and Infusion is:

```text
Cycle coefficient = 24 × Cycle intensity / (Cycle intensity + 180)
```

All Psyche damage uses 100% defence penetration, so its defence zone is `1`. The resistance zone for the
Psyche element is still calculated — Psyche defence resistance cannot be penetrated.

### Recorded Cycle effects

- **Genesis** (Cosmos + Anima): one Genesis stalk produces 5 Genesis blossoms; every 2 seconds one flies
  at the target and explodes, up to 3 stalks. Damage is level multiplier × Cycle intensity multiplier ×
  defence zone × resistance zone; the level-80 multiplier is 9000; it does not benefit from Overlay's
  damage increase.
- **Overlay** (Anima + Incantation): within 12 seconds, when Anima/Incantation damage ends it appends a
  one-off `original damage × 20% × Cycle coefficient`.
- **Turbid Burn** (Incantation + Chaos): a 15-second DOT; the level-80 multiplier is 2700, CRIT is fixed
  at 50%, and it uses the Cycle owner's CRIT DMG and other parameters.
- **Dark Star** (Chaos + Lakshana): resolves Psyche damage after 5 seconds; the level-80 multiplier is
  45000 and the defence zone is not calculated. The same trigger cannot stack it repeatedly, different
  triggers can, and the total resolves when the last Dark Star ends.
- **Infusion** (Lakshana + Mental): within 12 seconds, Lakshana/Mental damage gains
  `20% × Cycle coefficient` in the DMG bonus zone.
- **Delay** (Mental + Cosmos): 5 seconds of reduced ATK and speed.
- **Accrual** (Cosmos + Anima + Mental): a Genesis blossom hitting a Delayed target grants 10 extra
  ultimate energy.
- **Dissonance** (Chaos + Lakshana + Incantation): directly deducts Break value while the target is
  under both Dark Star and Turbid Burn; the exact amount is still to be filled in.

## Break damage

```text
Break damage = character level multiplier × Break intensity zone × enemy Break cap zone
             × defence zone × resistance zone
```

```text
Break intensity zone = 1 + team total Break intensity / 300 + Σ(Break damage increases)
enemy Break cap zone = enemy Break cap value / 3
```

The official `UnbaldamagePara` curve explicitly records levels 1–80, where the level-80 character
multiplier is `3603`. The Outer Realm Boss's Break cap value defaults to `50`; for a specified enemy,
read `UnbalMax` from its attribute pack first and fall back to the default only when no pack is
available. Break damage does not use ATK/HP/DEF multiplier attributes, nor DMG bonus, vulnerability,
crit or independent increases; the defence and resistance zones follow the current target's rules.

Dissonance only affects when the enemy enters Break and the timing of accelerated Break exit. It does
not affect Break damage or the enemy Break cap calculation.

## Imported official data

| Data | Official source | Current interpretation |
| --- | --- | --- |
| Break level multiplier | `DT_GlobalCommonData/UnbaldamagePara` | Exact curve for levels 1–80; 3603 at level 80 |
| Genesis damage | `DT_ReactionDamageData/GE_ActorReaction_1_Damage` | 16 official tiers; final tier 9000 |
| Turbid Burn damage | `DT_ReactionDamageData/Buff_Reaction_5_new` | 16 official tiers; final tier 2700 |
| Dark Star damage | `DT_ReactionDamageData/Buff_Reaction_4_new` | 16 official tiers; final tier 45000 |
| Skill multipliers | `DT_SkillDamageData` | ATK, HP and DEF multiplier tiers stored per GE |
| Cycle constants | `DT_ReactionEffectFigure` | Official single-point curve values with confirmed units |
| Enemy parameters | `DT_MonsterPackData*` | Defence, resistance and `UnbalMax` for standard/Outer Realm packs |

Raw SQLite arrays keep their official `source_tier` and are never rewritten into derived levels. The
service interprets the 15 skill tiers and 16 Cycle tiers according to the confirmed rules below, keeping
source facts and business mapping separate.

## Current scope

- Implemented: direct damage, DOT per-tick and per-stack resolution.
- Implemented: Break damage.
- Implemented: Cycle ownership selection, Cycle intensity multiplier, Overlay/Infusion coefficients, and
  the 15-tier skill and 16-tier Cycle level mappings.
- Imported: the complete Break level curve, official Cycle damage tiers, Cycle constants, skill
  multipliers, enemy attribute packs and Abyss bindings.
- Still to come: an official scene snapshot linking runtime target instances to static attribute packs,
  the complete state timing of each Cycle damage type, and further live confirmation of the Dissonance
  Break deduction.

The implementation is in `src/services/damage_calculation_service.py`. That service is a pure function:
it does not read or write SQLite, does not touch the UI, and does not replace the old character page's
"direct damage score".

## Confirmed mappings and default state timing

### The 15 skill multiplier tiers

Each damage GE stores ATK, HP and DEF multiplier arrays in `DT_SkillDamageData`. The 15-tier array is
indexed by `effective skill level - 1`, clamped to the final tier when it goes past the end. Effective
skill level is not simply the awakening level added on: the confirmed rule is the base skill level plus
"+1 to all skills once awakening level reaches 3". So a base level 10 skill with triple awakening uses
tier 11 (index 10). Other awakening effects are still supplied by the caller, avoiding unconfirmed
inference.

The source data path in the upstream export (the export directory is not part of the repository):

`SOURCE_EXPORT_ROOT/Content/DataTable/skill/DT_SkillDamageData.json`

The SDK structure is `FSkillDamageExecutionData`, in:

`SOURCE_EXPORT_ROOT/CppSDK/SDK/HTGame_structs.hpp`.

### The 16 Cycle tiers

One tier per 5 levels is confirmed:

```text
tier = floor((character level - 1) / 5)
```

Levels 1–5 are tier 1 and levels 76–80 are tier 16. This applies to Genesis, Turbid Burn and Dark Star.

### Default Cycle state rules

- Apart from Dark Star, re-application currently refreshes the duration rather than creating a new
  instance. This is marked as not yet verified in play.
- Dark Star times and explodes independently per trigger; the same trigger re-applying refreshes its own
  instance.
- Overlay records the damage actually dealt and appends `actual damage × 20% × Cycle coefficient` on
  expiry.
- A state removed early does not resolve immediately unless a skill-specific effect explicitly requires
  it.
- No extra "same frame" rule is defined; events are processed in the order the server receives them.
- Dissonance currently deducts `enemy Break cap × 15%`. A fixed value, level coefficient and multiplayer
  correction are all recorded as undetermined and excluded from the calculation for now.

### Monster instances and attribute packs

Static migration v4 introduced `monster_instance_profile` and `monster_instance_profile_variant`,
retaining the raw bindings from monster instance to attribute pack and to world/dungeon/abyss level
variants. Static migration v5 introduced `abyss_level`, `abyss_level_monster_spawn` and
`abyss_monster_pool_entry`, importing Abyss stages, waves, monster pools and attribute-pack
relationships. These tables remain in the current static schema v16.

This chain follows only the dedicated configuration under
`HT/Content/DataAssets/DataAssetSet/Abyss`: `AbyssCloneLevelDataTable` → `MonsterPoolID` →
`DT_AbyssMonsterPool` → `AttributeID` → `DT_MonsterPackData`. All 366 unique `AttributeID` values
currently resolve to a normal attribute pack. `FT_` is the prefix for the 999 Yoruko game mode — it does
not indicate an Outer Realm or Abyss scene and must not be used to determine the scene.
