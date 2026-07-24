# Third-Party Licenses

This file summarizes the third-party libraries referenced by the current project manifests. The project license in `LICENSE` applies only to project-owned code and documentation; third-party packages remain under their own licenses.

This summary is not a substitute for the upstream license text. For binary redistribution, keep the relevant upstream notices from Cargo, NuGet, Python packages, Npcap, Windows SDK components, and any bundled resource pipeline tools.

## Rust Application Dependencies

Resolved from `Cargo.toml` and `Cargo.lock` with `cargo metadata --format-version 1 --locked`.

| Package | Version in lockfile | License | Upstream |
|---|---:|---|---|
| `aes` | 0.8.4 | MIT OR Apache-2.0 | https://github.com/RustCrypto/block-ciphers |
| `anyhow` | 1.0.102 | MIT OR Apache-2.0 | https://github.com/dtolnay/anyhow |
| `base64` | 0.22.1 | MIT OR Apache-2.0 | https://github.com/marshallpierce/rust-base64 |
| `chrono` | 0.4.45 | MIT OR Apache-2.0 | https://github.com/chronotope/chrono |
| `crossbeam-channel` | 0.5.15 | MIT OR Apache-2.0 | https://github.com/crossbeam-rs/crossbeam |
| `eframe` | 0.34.3 | MIT OR Apache-2.0 | https://github.com/emilk/egui |
| `egui_material_icons` | 0.6.0 | [MIT](licenses/egui_material_icons-LICENSE-MIT.txt); embedded Material Symbols font is [Apache-2.0](licenses/material_symbols-LICENSE-APACHE-2.0.txt) | https://github.com/lucasmerlin/hello_egui/tree/main/crates/egui_material_icons |
| `hex` | 0.4.3 | MIT OR Apache-2.0 | https://github.com/KokaKiwi/rust-hex |
| `image` | 0.25.10 | MIT OR Apache-2.0 | https://github.com/image-rs/image |
| `libloading` | 0.8.9 | ISC | https://github.com/nagisa/rust_libloading |
| `pcap-file` | 2.0.0 | MIT | https://github.com/courvoif/pcap-file |
| `raw-window-handle` | 0.6.2 | MIT OR Apache-2.0 OR Zlib | https://github.com/rust-windowing/raw-window-handle |
| `rfd` | 0.15.4 | MIT | https://github.com/PolyMeilex/rfd |
| `serde` | 1.0.228 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| `serde_json` | 1.0.150 | MIT OR Apache-2.0 | https://github.com/serde-rs/json |
| `windows-sys` | 0.60.2 direct; additional transitive versions may appear | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| `winresource` | 0.1.31 | MIT | https://github.com/BenjaminRi/winresource |

`egui_material_icons` embeds Google's Material Symbols Rounded font. Google publishes Material Symbols under Apache-2.0: https://github.com/google/material-design-icons. The release workflow includes both license texts in every binary archive.

The current resolved Rust graph is permissively licensed. The lockfile includes transitive packages under MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, BSL-1.0, ISC, Zlib, Unlicense, Unicode-3.0, OFL-1.1, Ubuntu Font License, and LLVM-exception variants. Re-run the metadata command above after dependency changes and check any new or changed license expression before release.

## Python Tool Dependencies

Resolved from `tools/pyproject.toml` and `tools/requirements.txt`.

| Package | Version range | License | Upstream |
|---|---|---|---|
| `pycryptodome` | `>=3.23,<4` | Public domain and BSD-2-Clause portions | https://www.pycryptodome.org/ |

## C# Probe Dependencies

Resolved from `tools/cue4parse_probe/Cue4ParseProbe.csproj` and `tools/external-tools.json`.

| Component | Version or source | License | Upstream |
|---|---|---|---|
| `Newtonsoft.Json` | 13.0.4 | MIT | https://www.newtonsoft.com/json |
| `CUE4Parse` | Git subcheckout in `tools/external/` | Apache-2.0 | https://github.com/FabianFG/CUE4Parse |

`tools/external/` is not part of normal application runtime and should not be committed. If the C# probe is redistributed, include CUE4Parse's Apache-2.0 license and NOTICE information, plus licenses for its own dependencies.

## External Runtime Components

| Component | Role | License note |
|---|---|---|
| Rust toolchain and Cargo | Build the desktop app | Distributed separately by the Rust project. |
| Npcap | Runtime packet capture driver/library | Distributed separately by the Npcap project; review its license before bundling an installer. |
| Windows SDK / Win32 APIs | OS integration through `windows-sys` | Governed by Microsoft SDK and OS terms. |
| .NET SDK | Builds the optional C# probe | Distributed separately by Microsoft. |

## Resource Files

Files under `res/` can include project-authored JSON, derived metadata, and small client-derived UI resources. They are not automatically covered by the project software license if third-party rights apply. Treat game-derived names, icons, fonts, and tables as third-party material unless you have confirmed a separate right to redistribute them.
