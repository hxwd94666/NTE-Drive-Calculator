<div align="center">

<img src="https://raw.githubusercontent.com/hxwd94666/NTE-Drive-Calc/main/assets/app_icon.png" alt="NTE Drive Calc" width="108">
<h1>NTE Drive Calc</h1>
<p><strong>异环驱动计算器</strong></p>

Turn your *Neverness to Everness* inventory into a calculable dataset, appraise Modules and Cartridges
and build loadouts for every character algorithmically, then assemble them automatically.

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](#environment)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](#development)
[![Download](https://img.shields.io/badge/download-GitHub%20Release-238636)](https://github.com/hxwd94666/NTE-Drive-Calculator/releases)
[![Downloads](https://img.shields.io/github/downloads/hxwd94666/NTE-Drive-Calculator/total?label=downloads&color=238636)](https://github.com/hxwd94666/NTE-Drive-Calculator/releases)
[![Stars](https://img.shields.io/github/stars/hxwd94666/NTE-Drive-Calculator?label=stars&color=f4b400)](https://github.com/hxwd94666/NTE-Drive-Calculator/stargazers)

English · [简体中文](README.md)

[Download](#download) · [Features](#features) · [Quick start](#quick-start) · [Feedback](#feedback)

</div>

<a id="intro"></a>

## 📖 About

NTE Drive Calc is a Windows desktop companion tool for building Modules and Cartridges in
*Neverness to Everness*.

It addresses the problems that get worse the further into the game you are:

- the inventory keeps growing, and assembling and judging gear by hand costs too much time
- several characters compete for the same gear, with no obvious way to divide it sensibly
- there is no consistent standard for whether a newly dropped Module or Cartridge is worth keeping
- you want to manage an account's inventory long-term without relying on your own arithmetic and memory

The tool builds a local inventory — by packet capture or screenshot recognition — then combines
character blueprints, set requirements, stat weights and character priority into loadouts you can
actually apply, and assembles them for you.

<a id="features"></a>

## ⭐️ Features

| Capability | What it solves |
|---|---|
| 📷 Inventory capture | Turn Modules and Cartridges into a calculable inventory by screenshot scan or one-click sync |
| 🔍 Single-item appraisal | See who a piece suits, what it scores, and whether it is worth keeping |
| 🧮 Loadout generation | Build Module and Cartridge plans from blueprints, sets, weights and priority |
| 📐 Automatic assembly | Apply the generated loadout inside the game automatically |
| 📈 Character margins | Work out which attributes a character lacks most, to tune stat weights |
| 🧹 Module management | Lock or discard gear automatically by score, rarity and type after a scan |

<a id="preview"></a>

## 🔥 Screenshots

See the [Chinese README](README.md#preview) for the screenshot gallery — the images are shared between
both versions.

<a id="download"></a>

## ⬇️ Download and install

The latest installer is recommended:

- GitHub Release: <https://github.com/hxwd94666/NTE-Drive-Calc/releases>
- Mirror酱 (paid): <https://mirrorchyan.com/zh/projects?rid=NTE-Drive-Calc&channel=stable>
- Quark Drive (free): <https://pan.quark.cn/s/82f16b845aec>
- Baidu Drive (free): <https://pan.baidu.com/s/1sPVqCpzmkQwKYCGstcZuIQ?pwd=ygke>
- Bilibili: <https://b23.tv/nXJGdh3>

> Saving a copy from the drive links each release earns the author a little; saving from a phone earns
> more. Treat it as a way to support the project at no cost.

Keep `Install ViGEmBus virtual gamepad driver` ticked during installation. Scanning needs the virtual
gamepad driver to simulate paging through the inventory.

<a id="quick-start"></a>

## 🚀 Quick start

1. Install and open the app.
2. Open the Module or Cartridge inventory in the game.
3. Get your data with inventory sync on the dashboard, or a full scan from the calculate page.
4. Once you have data, pick the characters you want loadouts for on the calculate page.
5. Choose an allocation strategy and start.
6. Review the results, then confirm and save the equipment lock.
7. In the game, open the character page, go to the loadout page and click assemble.

<a id="scenarios"></a>

## 🧰 Common uses

- **Building loadouts** — capture inventory data and generate plans from weights.
- **Is this piece worth it?** — appraisal shows the score, matching characters and retention value.
- **What does this character lack?** — character margins rank current attribute gains.
- **Inventory full?** — automatically discard low scores and lock high ones by score, rarity and type.

<a id="environment"></a>

## 🖥️ Requirements

- Windows 10/11 x64
- Game language: Simplified Chinese
- Recommended resolution: 1080p, 2K, 4K or 2560x1600
- Scanning requires the ViGEmBus virtual gamepad driver

> The app ships a language switcher (Simplified Chinese / English) in Settings, and the change takes
> effect on next launch. The **game** must stay in Simplified Chinese: screenshot scanning, appraisal and
> automatic assembly match OCR output against Chinese game terms. Inventory sync through nte-core is
> language-independent, but the OCR-based features are not.

<a id="feedback"></a>

## ❓️ Feedback

If you hit a recognition error, a failed installation, a scanning problem or a loadout result that looks
wrong, please include where you can:

- a screenshot of the problem
- the steps you took
- the log file produced after enabling runtime logging on the settings page
- the app version you are using

Where to report:

- GitHub Issues: <https://github.com/hxwd94666/NTE-Drive-Calc/issues>

<a id="development"></a>

## 🧑‍💻 Local development

`2.0.0` moved the runtime data boundary to SQLite and drives the character, inventory and loadout
services by official game IDs. For the design, see [Architecture](docs/en/architecture.md) and
[External integrations](docs/en/integrations.md); developers and coding agents start from the
[developer documentation index](docs/en/README.md).

```powershell
uv sync --group build --group dev
uv run python main.py
```

Without `uv`, pip can install the runtime dependencies straight from `pyproject.toml`:

```powershell
python -m pip install .
```

Build the desktop application:

```powershell
.\.venv\Scripts\python.exe .\build_exe.py
```

Build the installer:

```powershell
.\.venv\Scripts\python.exe .\build_installer.py
```

## 📑 Licence and third-party components

The project's own source code is released under [AGPL-3.0](LICENSE). The `nte-core.exe`,
`nte-mods-plugin`'s `dwmapi.dll` and ViGEmBus distributed with the program are independent components;
their origin, applicable terms and notices are in [NOTICE](NOTICE) and `third_party/`. The root licence
does not rewrite their respective licences or grants.

This is an unofficial player tool. Game names, characters, assets and related rights belong to their
respective owners. Before using packet capture, plugin or automation features, satisfy yourself about
the applicable game rules, terms of service and local law.

## 💖 Support

[<img width="150" alt="Sponsor us" src="https://pic1.afdiancdn.com/static/img/welcome/button-sponsorme.png">](https://afdian.com/a/hxwd94666)

If you like the project, consider supporting it. Supporters currently get group-chat access for prompt
bug reports and feature requests.

## 👥 Contributors

<a href="https://github.com/hxwd94666/NTE-Drive-Calc/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=hxwd94666/NTE-Drive-Calc" alt="contributors">
</a>

## 🤝 Acknowledgements

- 异环工坊 (WeChat mini program): character scoring standards and stat weight references
- [nte-dps-toolkit](https://github.com/kongbaiz/nte-dps-toolkit): protocol parsing core and assembly
  plugin support
