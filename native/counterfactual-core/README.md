# counterfactual-core

`counterfactual-core` is an independent C++20 sidecar for one frozen-axis Buff
counterfactual slice. It does not read SQLite, accounts, UI state, or the game
process, and it is not called by the production Python entry point yet.

The first slice owns:

- immutable hits, character panels, Buff intervals, and target keys;
- half-open `[start_us, end_us)` interval and recipient filtering;
- additive attack/HP/defense, damage-up, critical, defense-ignore,
  penetration, and target-resistance projection for stateless direct hits;
- frozen critical branches and observed-damage ratio anchoring;
- deterministic per-hit and aggregate `complete`, `partial`, `unavailable`,
  and `not_applicable` results with stable gap codes.

Each target profile is deliberately scoped to one `(scope_half, target_id)`
and one `damage_attribute`; multiple attributes for the same target therefore
use separate profiles, and a scalar resistance is never reused across them.
`enemy_defense_base` is the formal unnormalized DefBase consumed by the Python
profile formula, including its `/6` normalization. The executable expects
callers to validate JSON against the bundled schema before invocation. Its
runtime parser still performs semantic safety checks, but it is not a second
full JSON Schema implementation.

Anything outside this slice, including DOT, topple, reactions, specialized
Calculations, state machines, or missing target profiles, remains explicit as
`unavailable` or `partial`. `unavailable` never carries a ratio or candidate
damage; only proven `not_applicable` rows carry exact ratio `1`.

## Build on the verified Windows toolchain

With Visual Studio 2022 and CMake available:

```powershell
cmake -S native/counterfactual-core -B build/counterfactual-core-vs -G "Visual Studio 17 2022" -A x64
cmake --build build/counterfactual-core-vs --config Release --verbose
ctest --test-dir build/counterfactual-core-vs -C Release --output-on-failure
python tools/counterfactual/run_cpp_differential.py build/counterfactual-core-vs/Release/counterfactual-core.exe
```

The repository has no new third-party C++ dependency. The JSON reader is a
small project-owned implementation limited to this versioned contract.

## Contract and oracle

- `contract/counterfactual-request-v1.schema.json`
- `contract/counterfactual-response-v1.schema.json`
- `tests/fixtures/ordinary-buffs.request.json`
- `tests/fixtures/ordinary-buffs.oracle.json`

Regenerate the public oracle with:

```powershell
python tools/counterfactual/generate_cpp_oracle_fixture.py
```

That generator calls the current Python interval projection, component ratio,
projection-gap, and aggregation services. The fixture contains only synthetic
public values and no account, UID, absolute-path, or captured-game data.
It intentionally rejects incomplete axes, non-additive operations, and values
below high confidence instead of silently coercing unsupported wire variants.
