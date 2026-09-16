# nte-core component source and license

Independent CLI derived from NTE DPS Toolkit. Included LICENSE, NOTICE.md, LICENSING.md, THIRD_PARTY_LICENSES.md and dependency licenses retain their terms.

Source base: 77a775ad7639d17ae63b2a6c15c7cb6f49c01917; modified working-tree input SHA-256: 673eb4ccec24eb4a18a01539a9c11fb6b59a334519653b57c1f7fcbc5cf16f42. Build: Release x64, cli with the seven core-manifest JSON resources embedded, no external_resources or desktop features, one job. Source paths are remapped. The base alone does not reproduce this build.

Core forwards optional HUD control through the same native connection without changing capture state. It advertises the capability only when the provider supports it. Targeted request and paired direct/proxy tests passed; exact in-game acceptance is pending.

Original event payloads, per-hit evidence, dynamic attributes and formula observations retain their existing transport. Formula interpretation remains in the separate analysis component. Core delegates user/UAC/logon access policy to Windows pipe access control, retaining target PID, process creation time and pipe-server identity checks.
