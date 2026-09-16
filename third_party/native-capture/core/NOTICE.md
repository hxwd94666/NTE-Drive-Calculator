# Notices

NTE DPS TOOL is an independent, community-maintained diagnostics tool. It is not affiliated with, endorsed by, sponsored by, or approved by the NTE game publisher, developer, platform operator, or any related rights holder.

## Use Scope

- This project is licensed under the GNU AGPL v3.0. You may use, modify, and redistribute it (including commercially) provided you comply with the AGPL's copyleft and network-use source-disclosure requirements. See [LICENSE](LICENSE) and [LICENSING.md](LICENSING.md).
- To use this project in a closed-source product or proprietary hosted service (i.e. without disclosing your source under the AGPL), obtain a separate commercial license from the copyright holder. See [LICENSING.md](LICENSING.md).
- Do not publish private traffic captures, decrypted payloads, resource export keys, usmap files, unpacked client assets, or user-specific local paths.

## Game Data And Assets

The repository may contain stable derived resource tables and small UI assets needed by the tool. Game names, character names, icons, screenshots, fonts, data tables, and other client-derived materials remain the property of their respective rights holders.

Before redistributing a build or fork, review the included `res/` files and make sure you have the rights required for your distribution channel. If a public release needs a lower-risk package, prefer shipping code and scripts separately from extracted or derived game assets.

## Security And Privacy

Runtime packet captures and exported debug files can contain local network metadata and gameplay state. Keep generated files under `logs/`, `target/`, `data/`, and other ignored directories out of commits and public reports.
