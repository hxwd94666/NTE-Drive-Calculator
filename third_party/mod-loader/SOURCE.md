# User-mode Loader source

Based on the existing nte-dps-toolkit Loader; base commit 3974e6ec1875b94320c9511860f27c809c060722, working-tree input SHA-256 3979fc24686d0360fadd06261548ddda1d504c0ddd16740bc27e27a4cdba6bcc. The base alone does not reproduce this build. Input digest covers the native Loader source tree, excluding build and binary outputs.

Adds Taiwan launcher-core recognition for NTETWGame.exe in the existing monitor and embedded shim. Calc supplies the selected NTETWLauncher.exe path. Existing trusted-installation checks, exact process-name matching, session ownership and cleanup remain unchanged.

Release EXE and embedded shim were rebuilt together. Resource 101 matches the rebuilt shim byte-for-byte; static ABI and payload-kind declarations, Taiwan target names, local-path and PDB-reference checks passed. Runtime capability execution, isolated host loading and actual launcher/game integration were not run for this build. D3D, capture plugins, sampling Core and analysis Core were not rebuilt or changed.

The executable retains requireAdministrator. Existing standard LoadLibrary mode, UAC, stop event and owner PID remain in use; no kernel driver or security-policy changes. AGPL-3.0 and bundled dependency notices remain applicable.
