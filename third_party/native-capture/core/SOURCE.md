# nte-core component source and license

Independent CLI derived from NTE DPS Toolkit. Included LICENSE, NOTICE.md, LICENSING.md, THIRD_PARTY_LICENSES.md and dependency licenses retain their terms.

Source base: 3e3bcd05583c369cfecc477aaa0adc933591841b; modified working-tree input SHA-256: 38988445a95bdf1cffc0d49a04f1c22e29b857923cfb086b596743cbcd29b9be. Build: Release x64, cli with the seven core-manifest JSON resources embedded, no external_resources or desktop features, one job. Source paths are remapped. The base alone does not reproduce this build.

Common event envelopes are checked for version, sequence, timestamp and size. Unknown payloads are preserved without interpreting calculation or Buff mechanisms. Event records cannot replace hit state references. Targeted protocol and axis-paging tests preserve execution evidence, dynamic attributes and formula observations, including hits without legacy effect-presence lists. Direct/proxy companion tests passed. Formula interpretation remains in the separate analysis component.

Core delegates user/UAC/logon access policy to Windows pipe access control instead of duplicating token comparisons. Target PID, process creation time and actual pipe-server identity checks remain.