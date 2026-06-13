# NTE Drive Calc Installer

This project uses Inno Setup 6 to build a Windows setup executable.

## Build

Install Inno Setup 6 first:

```powershell
winget install JRSoftware.InnoSetup
```

Then build the setup executable:

```powershell
.\.venv\Scripts\python.exe .\build_installer.py
```

If `dist\NTE_Drive_Calc` already exists and you only want to rebuild the installer:

```powershell
.\.venv\Scripts\python.exe .\build_installer.py --skip-app-build
```

If Inno Setup is installed in a custom location:

```powershell
$env:INNO_SETUP_ISCC = "D:\Tools\Inno Setup 6\ISCC.exe"
.\.venv\Scripts\python.exe .\build_installer.py --skip-app-build
```

The final installer is written to:

```text
installer\output\NTE_Drive_Calc_Setup_1.0.0.exe
```

## Included Runtime Dependencies

The installer packages:

- `dist\NTE_Drive_Calc\NTE_Drive_Calc.exe`
- `dist\NTE_Drive_Calc\_internal`
- `ViGEmBusSetup_x64.msi` from the installed `vgamepad` Python package

During installation, the setup runs the ViGEmBus driver installer silently when the
`ViGEmBus` service is not already installed. The setup requires administrator
permission because driver installation needs elevated rights on Windows.

After installation, the app itself can be opened normally. Only the automatic scan
modes that control mouse/gamepad input will prompt the user to restart as
administrator.
