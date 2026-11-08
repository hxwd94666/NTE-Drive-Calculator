# Native Core source

Base commit: cc0604c94dab31c0730092b1f7208cbe764e5c73. Core build input digest: 60c0d9117d6dc3b378f4c3d424b8393b28fe90ca8dbb24c8fd41fe8d33d648c3.

Merged Release x64 Core 0.4.8 with cli features and embedded core data resources; desktop and private MCP features are disabled. Source paths are remapped. Root Cargo, build.rs, src and res inputs are hashed as sorted relative-path/content-SHA256 pairs.

Current role/equipment projections and typed pause evidence are retained. Menu-only transitions do not extend battle end; overlapping skill transitions still do. Seven menu regressions, thirteen native clock tests (with overlap) and the embedded-resource test passed. Version and offline packet hello/shutdown were verified. Independent analysis Core is unchanged.

Existing protected DLLs, signatures, loader and licenses are retained byte-for-byte. No game deployment or current-build live acceptance was performed.
