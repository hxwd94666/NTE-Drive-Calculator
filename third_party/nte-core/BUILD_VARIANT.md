# NTE Core - Windows x64

- Local JSON-RPC 2.0 over NDJSON stdio sidecar for third-party integrations.
- Does not open a network port and does not include desktop UI images or windowing dependencies.
- Read `CLI_PROTOCOL.md` or `CLI_PROTOCOL_ZH.md` before integrating.
- Core version: `0.4.4`; CLI protocol/data versions: `1` / `1`; battle read contract: `5`.
- Public source branch: `ternary-chen/nte-dps-toolkit:codex/fix-battle-capture-v044`.
- Source commit: `24bb078fe38675d1719d24e92465a313baf65b3e`.
- The binary includes typed time-stop, target identity/reconciliation, bounded live battle-read, and targetless death-settlement marker fixes.
- Rust toolchain: `1.98.0-x86_64-pc-windows-msvc`.
- Build command: `cargo build --release --bin nte-core --no-default-features --features cli`.
- Uncompressed binary SHA-256: `CCE437B16BDE3EC444E87AF1F2725349471055E2F9A8EE31791CEE501BE4DAA6`.
- Packaged with UPX 5.2.0 `--best --lzma` and verified with `upx -t`.
- Packaged binary SHA-256: `92C5931948F7121942F617939CB8AE5B6638027969DCD0753E1AF7B939D6B6C5`.
