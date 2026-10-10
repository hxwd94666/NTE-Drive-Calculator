# Native component source

Source repository: https://github.com/kongbaiz/UETools-NTE

Base commit: bf7ffc7f06f1766e008c6a974d94c4fd4aa310d9. Modified working-tree input SHA-256: 0c2f7ead8bdf56fc9c4f2a5893d9e94001164e4b424dd61dd558df6615ef67a5. The base alone does not reproduce this build.

Reviewed upstream reference: c6b30a17a46f0725ce4b4fc34cbdee96bca233b1. Selected changes include bounded HUD lookup and layout batches, native-mode definition-only skill reads, owned-event exclusion, batched idle Hook retirement, and optional performance spans and window summaries. The prior guarded-root, event empty-slot, skill-category and awakening-definition changes are retained.

The branch retains separate Combat/HUD plugins, native avatar-tree mounting, construction-context tracking and native-only wait states. Performance retains seven service kinds, Calc control and leases, and complete raw records by default. Diagnostic compaction requires explicit opt-in. Developer MCP tools and training controls are not included in this five-DLL bundle.

Five Release x64 DLLs use selected-function protection and four final-byte signatures. The proxy tolerates an absent optional D3D12GetInterface on older Windows; all other required exports remain checked. The only changed native build input relative to the previous bundle is src/host/d3d12_proxy.cpp. Synthetic old/new export and assembly ABI checks, plus the exact protected signed-host dependency, tamper, plugin loading and capture-pipe handshake fixture passed locally. Core and Loader are reused unchanged. No Windows 10 LTSC or real-game acceptance was performed for this candidate.
