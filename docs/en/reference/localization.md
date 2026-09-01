# UI localisation

*English · [简体中文](../../reference/localization.md)*

UI language is provided by `src/i18n`. The language preference is stored alongside the theme in
`config/global_ui_preferences.json`. `zh_CN` (the source language) and `en` are supported.

## Two kinds of text, two mechanisms

Chinese text in the UI splits into two kinds that must be handled differently:

| Kind | Examples | Mechanism | Why |
| --- | --- | --- | --- |
| UI copy | `保存`, `工作台`, `检查更新` | `tr("...")` | Pure display text; the whole string can be replaced |
| Game terms | `攻击力%`, `环合强度`, `「失落光芒」` | `display_term("...")` | Also an OCR match value and a static-database lookup key |

Game terms **must not** be translated in place. They are compared against OCR output from the game
client and used as lookup keys in `data/game_static.sqlite3`; rewriting them breaks scanning, parsing,
scoring and loadouts at once. `display_term` replaces only the display name — the key itself is
unchanged.

## Catalogues

- `locales/en.json` — UI copy, **keyed by the Chinese source string**.
- `locales/glossary.en.json` — game-term display names, split into `stats`/`elements`/… sections.

Keying on the source string means a missing translation degrades to the original Chinese rather than to
a key name, so the catalogue can be filled in gradually and uncovered screens stay usable. The source
language `zh_CN` needs no catalogue.

The English in `glossary.en.json` comes from the game's own string tables, joined to the static database
on `name_text_key` and `attribute_id`, with `_meta.provenance` recording the source of each tier. When
editing, change only the English value — never the Chinese key.

## When it takes effect

Language, like the theme, takes effect **on next launch**: both change how every widget is built, and
there is no live repaint. `src/ui/app.py` calls `set_language()` before importing any UI module so that
module-level `tr()` resolves against the right catalogue. New module-level copy must preserve that
order.

That call happens at **import time**, so merely importing `src.ui.app` activates the language from the
local preference file. Test assertions are written against source-language strings, so
`tools/quality/run_tests.py` sets `NTE_UI_LANGUAGE=zh_CN`; `app.py` reads that environment variable
first, and test results no longer depend on local preferences.

## Multilingual names that nte-core already supplies

nte-core inventory items carry `names`/`suit_names` (`en`/`ja`/`zh_cn`) alongside a stable
`property_id`, so equipment names, set names and stat names need **no** glossary entry:
`display_localized(names, chinese_fallback)` takes the game data's own name for the active language and
falls back to `display_term` only when the payload has nothing.

Rows from the `vision` source carry Chinese only, so that fallback path must stay. Note that
`_localized()` still returns Chinese — it is the source of filter keys such as `item_type_id`, so do not
change it to resolve by language.

## Resolve display names only at render time

`display_term` is called only when a value is **about to be written into a widget**. Weight lookups,
scoring, sorting and alias normalisation all keep using the Chinese key. Existing call sites: the set
name, main stat and sub stats in `_equip_card`, the attribute rows in `AttributeSummaryPanel`,
`_display_bonus_stat_label`, character names and set names.

One easy trap: the Chinese key carries the percent sign (`攻击力%`) while the English display name does
not (`ATK Bonus`). The percent suffix must therefore **be derived from the Chinese key**, never by
checking whether the display name contains `%`:

```python
main_key = str(main_stat)
main_text = display_term(main_key)
percent_suffix = "%" if "%" in main_key else ""   # from the key, not the display name
```

`_format_panel_value` decides with `bonus_uses_percent(stat)`, which already operates on the key and is
unaffected.

## Long-form game text

Set effects, awakening effects and Arc skill descriptions are original game text and do not enter
`en.json`. The static database stores their string-table keys (`description_text_key` and friends), and
at runtime `display_text(text_table, text_key, chinese_fallback)` looks them up in
`locales/gametext.<lang>.json`.

That file is generated from a locres export by `tools/game_data/build_game_text_locale.py`; only the
generated result is committed, never the locres export itself. `fork_star_level` has no key column, so
it is derived from the `upgradestar_pack_X` → `buff_X_effect` naming convention. Keys are stored and
read in lower case to avoid missing lookups from case differences between the static database and the
string tables.

The English originals keep the same `{n}` placeholders, so refinement-value substitution holds in both
languages.

**Skill names in battle reports are not translated** — the name nte-core reports is shown verbatim.
nte-core's own skill-name table is already a mix (some English, some Chinese, some raw IDs such as
`GA_Shinku_Melee`), and maintaining a local mapping would mean re-exporting the locres for every
character the game adds, which is not worth the cost. Skill **categories** (`E技能`, `普攻` and so on)
are a small fixed set and still go through `tr()`.

## Singular and plural

Chinese does not inflect for number; English does. Where it matters, add a **sibling key** to the
catalogue whose name states which field decides it:

```json
"{count} 个驱动":              "{count} Modules",
"{count} 个驱动::one::count":  "{count} Module"
```

`tr()` switches to the sibling only when that field equals 1. Naming the field is required: these
sentences often carry a second integer (a snapshot or job number), and a rule like "any integer equals
1" would fire on the wrong one.

The catalogue stays a flat string dictionary — `load_catalog` filters the sibling keys out and
`load_plurals` splits them at load time, so `tr()` remains a single dictionary lookup. This covers
English's one/other split only; it is not full CLDR plural handling.

## Adding new copy

1. Wrap it in code with `tr("Chinese source string")`; convert f-strings to
   `tr("...{name}...", name=value)`.
2. Add that Chinese source string as a key in `locales/en.json`.
3. Game terms use `display_term` instead and never enter `en.json`.

`locales/` is a read-only release resource, located by
`src.integrations.bundled_resources.bundled_locales_dir` and shipped with the package by
`build_exe.py`.

## Related tests

`tests/test_i18n.py` pins the fallback behaviour, term mapping and catalogue completeness, and checks
that the theme and language sharing one preference file do not overwrite each other.
