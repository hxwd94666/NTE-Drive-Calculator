# Native capture component source

Source: https://github.com/kongbaiz/UETools-NTE; base commit 348a0aafe308967fc2b432b8a6395a6a54bfc998; modified working-tree input SHA-256 7f174b9dc007ad54ff448451db90a32e06a00e9a9752b42a6e545e12ca7b692c. The base alone does not reproduce this build.

Release x64 standalone capture and minimal D3D proxy. VMProtect policy and file identities are recorded in capture-component.json. Existing license terms apply. First-hit snapshot bytes are retained across half transitions. Buff baselines are emitted at first hit and confirmed half/reset boundaries; callbacks retain subsequent changes.

Focused fixtures and exact-binary isolated-host checks passed. Real-game capture, first-hit coverage and save/reopen acceptance remain pending.
