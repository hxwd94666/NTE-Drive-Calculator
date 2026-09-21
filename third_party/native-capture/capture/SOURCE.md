# capture component source

Source base: 5d9c0269f60abc968b932e78d0cf1f91c01ea240; modified working-tree input SHA-256: c81da392f422619d6f784861bf7be06b98fd73b762a419c951c07c2f768d5f21. The base alone does not reproduce this build. Release x64. Existing licenses and notices retain their terms.

HUD integration source: 1c3d65a5fa265ac4a176478fe4effd91955bce11. Native E/Q presentation, bounded UI discovery and callback-owned reads are adapted to the existing standalone dispatcher. Attribute replication retains its capture route; HUD notifications do not create hits. Local state-batch ordering and raw-evidence capture remain in the build.

Direct/proxy loading, HUD configuration and the existing Core handshake passed in an isolated non-game host. In-game appearance, skill-form transitions and secondary effect-window rendering remain unverified.
