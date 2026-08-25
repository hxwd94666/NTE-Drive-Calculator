# NTE Core - Windows x64

- Local JSON-RPC 2.0 over NDJSON stdio sidecar for third-party integrations.
- Does not open a network port and does not include desktop UI images or windowing dependencies.
- Read `CLI_PROTOCOL.md` or `CLI_PROTOCOL_ZH.md` before integrating.
- The executable is compressed with UPX `--best --lzma` and integrity-tested before packaging.
- Source branch: `codex/dedupe-embedded-resources-upx-lzma`.
- Source commit: `c1165ff2ddaf6fe33936347a5156dd7e796180f8`.
- Rust toolchain: `1.98.0-x86_64-pc-windows-msvc`.
