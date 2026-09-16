# NTE Capture component source

Headless runtime and minimal D3D12 proxy derived from UETools-NTE. Included GPL v3 and Microsoft Detours MIT notices retain their terms.

Source base: ef0a350c5253c25d93b6577e131b654e643921c5; modified working-tree input SHA-256: 666f1dcd0aa336bb58831b161eeef7e8e19aa3cbd4bb2b302e0e3b1f98d14f8a. The base alone does not reproduce this build. Release x64, raw evidence enabled, single compiler job.

Per-hit dynamic attributes, applied modifiers and client formula observations remain available. Attribute RepNotify observations use the same context event stream, recording receiver, property, explicit old parameter when available, received value and callback-return value. These are client replication observations, not certified server per-hit operands. No periodic Buff scan or actor effect-presence list is added.

Origin dependency validation copies all current bytes in original order, using checked kernel copies for byte dependencies and sharing region descriptions only for explicit permission checks inside one uninterrupted validation pass. No game calls or cross-callback permission reuse occur in that pass. Direct and proxy non-game protocol tests and read-proof invalidation tests passed. This exact binary still requires gameplay validation after restart. No developer UI, MCP, private source or debug symbols is shipped. Production hot unload is unsupported.
