# NTE Core - Windows x64 local trial

This build adds a native battle provider to the existing capture CLI. The ordinary serve entry
continues to handle inventory and packet capture; serve-native receives per-hit Buff snapshots
through the versioned Windows named pipe. A battle never changes source mid-capture.
The native provider also reads the existing combat-clock Mod for the same game process.
Time-stop subtraction requires complete clock history; incomplete evidence retains wall-clock timing.
Native hits use the packet provider's CombatState and stdio Abyss reducer. Ordered DLL context
events require combat.context.v1 as well as combat.hit_buff.v1. Normal upper/lower transitions
retain one capture; ordinary scene transitions finalize and let Calc start the next capture.
Unknown stages and result success remain unknown. Existing game processes need a restart for the new DLL.
The existing core.status request reads provider readiness and scene diagnostics on demand.
Start rejection details are preserved for the client; the Core adds no polling or automatic retry.
Native settlement components retain independent amounts and use formal display categories.
Only formal skillKey values become ability identifiers; weak object references stay in raw evidence.
Packet settlements without a preceding skill hit retain their exact amounts and validated,
catalogued container character declarations. Unbound skills and elements remain unknown.
Main and additional amounts precede terminal reconciliation so residual accounting does not add them twice.
Already settled rows do not re-enter client calibration or become new preceding-hit candidates.

Native combat also retains compact callback and settlement evidence in the account raw-capture directory.
The journal uses the existing asynchronous writer, and reports partial writes separately from battle transport integrity.
Enabling raw capture requires combat.raw_evidence.v1; update the DLL and restart the game.

COMPONENT.md records the current hash and verification boundary. SOURCE.md records the base
revision and dirty-worktree build status. This local package contains binaries and notices only.
Native game coverage, time-stop capture and live performance remain unverified.
