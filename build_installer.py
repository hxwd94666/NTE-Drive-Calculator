# 生成安装包脚本并同步应用版本信息。
r"""
Build the Windows installer for NTE Drive Calc.

Requirements:
    - A PyInstaller app bundle in dist/NTE_Drive_Calc
    - Inno Setup 6 installed, or INNO_SETUP_ISCC pointing to ISCC.exe

Typical usage:
    .\.venv\Scripts\python.exe build_installer.py
    .\.venv\Scripts\python.exe build_installer.py --skip-app-build
    .\.venv\Scripts\python.exe build_installer.py --generate-only
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tools import build_cli


ROOT = Path(__file__).parent.resolve()
DIST_APP = ROOT / "dist" / "NTE_Drive_Calc"
APP_EXE = DIST_APP / "NTE_Drive_Calc.exe"
APP_INTERNAL = DIST_APP / "_internal"
APP_NTE_CORE = APP_INTERNAL / "nte-core.exe"
APP_USER_SCHEMA = APP_INTERNAL / "src" / "storage" / "sqlite" / "schema" / "001_user_data.sql"
APP_STATIC_DATABASE = APP_INTERNAL / "data" / "game_static.sqlite3"
INSTALLER_DIR = ROOT / "installer"
OUTPUT_DIR = INSTALLER_DIR / "output"
ISS_PATH = INSTALLER_DIR / "NTE_Drive_Calc.iss"
APP_ICON = ROOT / "assets" / "app_icon.ico"
VIGEM_BUNDLE_EXE = ROOT / "ViGEmBus_1.22.0_x64_x86_arm64.exe"

APP_NAME = "NTE Drive Calc"
APP_EXE_NAME = "NTE_Drive_Calc.exe"
APP_ID = "{{D7DA28BE-8A19-4E05-9216-3F16C4C2C820}"
CORE_CONFIG_FILES = ("roles.json", "sets.json", "stats.json", "shapes.json",
                     "my_roles_model.json", "tapes.json", "weapons.json")


def _run(cmd: list[str], cwd: Path = ROOT) -> None:
    build_cli.run(cmd, cwd)


def _running_in_automation() -> bool:
    return build_cli.running_in_automation()


def _choose_workshop_sync_mode(skip_workshop_sync: bool, require_workshop_sync: bool) -> tuple[bool, bool]:
    if skip_workshop_sync or require_workshop_sync:
        return build_cli.choose_build_mode(
            skip_workshop_sync=skip_workshop_sync,
            require_workshop_sync=require_workshop_sync,
            has_explicit_choice=True,
        )
    return build_cli.choose_build_mode()


def _find_iscc() -> Path | None:
    candidates = [
        os.environ.get("INNO_SETUP_ISCC"),
        shutil.which("ISCC.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def _read_app_version() -> str:
    try:
        from src.app.constants import APP_VERSION
    except Exception as exc:
        raise RuntimeError("APP_VERSION not found in src.app.constants ") from exc
    return APP_VERSION


def _find_package_dir(package_name: str) -> Path | None:
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def _find_vigem_installer() -> tuple[Path, bool]:
    if VIGEM_BUNDLE_EXE.exists():
        return VIGEM_BUNDLE_EXE, True

    pkg_dir = _find_package_dir("vgamepad")
    if pkg_dir is None:
        raise RuntimeError(
            f"ViGEmBus installer not found: {VIGEM_BUNDLE_EXE}. "
            "vgamepad is also not installed, so no fallback MSI is available."
        )

    msi = pkg_dir / "win" / "vigem" / "install" / "x64" / "ViGEmBusSetup_x64.msi"
    if not msi.exists():
        raise RuntimeError(f"ViGEmBus driver MSI not found: {msi}")
    return msi, False


def _app_process_running() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {APP_EXE_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return APP_EXE_NAME.lower() in result.stdout.lower()


def _validate_app_bundle() -> None:
    """安装前检查 PyInstaller 产物，避免生成启动后才报缺文件的安装包。"""

    required = {
        "主程序": APP_EXE,
        "PyInstaller 运行目录": APP_INTERNAL,
        "nte-core 本地组件": APP_NTE_CORE,
        "用户数据库结构": APP_USER_SCHEMA,
        "发行版静态数据库": APP_STATIC_DATABASE,
    }
    missing = [f"{label}：{path}" for label, path in required.items() if not path.exists()]
    if missing:
        raise RuntimeError("PyInstaller 产物不完整，缺少：\n" + "\n".join(missing))


def _ensure_app_bundle(skip_app_build: bool, *, skip_workshop_sync: bool = False, require_workshop_sync: bool = False) -> None:
    if skip_app_build:
        if require_workshop_sync:
            raise RuntimeError("--require-workshop-sync cannot be used with --skip-app-build because the existing app bundle cannot be refreshed.")
        if not APP_EXE.exists() or not APP_INTERNAL.exists():
            raise RuntimeError(
                "dist/NTE_Drive_Calc is missing. Run build_exe.py first or omit --skip-app-build."
            )
    else:
        if _app_process_running():
            raise RuntimeError(
                f"{APP_EXE_NAME} is currently running. Close it before rebuilding dist/NTE_Drive_Calc, "
                "or use --skip-app-build to package the existing app bundle."
            )

        build_cmd = [sys.executable, str(ROOT / "build_exe.py")]
        if skip_workshop_sync:
            build_cmd.append("--skip-workshop-sync")
        if require_workshop_sync:
            build_cmd.append("--require-workshop-sync")
        _run(build_cmd)

    _validate_app_bundle()


def _inno_path(path: Path) -> str:
    return str(path.resolve())


def _write_iss(version: str, vigem_installer: Path, vigem_is_exe: bool) -> None:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    setup_icon_line = f"SetupIconFile={_inno_path(APP_ICON)}\n" if APP_ICON.exists() else ""
    vigem_dest_name = "ViGEmBus_Setup.exe" if vigem_is_exe else "ViGEmBusSetup_x64.msi"
    vigem_file_line = (
        f'Source: "{_inno_path(vigem_installer)}"; DestDir: "{{app}}\\drivers"; '
        f'DestName: "{vigem_dest_name}"; Flags: ignoreversion'
    )
    core_config_excludes = ",".join(f"config\\{name}" for name in CORE_CONFIG_FILES)
    core_internal_config_replace_lines = "\n".join(
        f'Source: "{_inno_path(APP_INTERNAL / "config" / name)}"; DestDir: "{{app}}\\_internal\\config"; '
        'Flags: ignoreversion; Tasks: replacecoreconfig'
        for name in CORE_CONFIG_FILES
    )
    core_runtime_config_replace_lines = "\n".join(
        f'Source: "{_inno_path(APP_INTERNAL / "config" / name)}"; DestDir: "{{app}}\\config"; '
        'Flags: ignoreversion; Tasks: replacecoreconfig'
        for name in CORE_CONFIG_FILES
    )
    core_internal_config_keep_lines = "\n".join(
        f'Source: "{_inno_path(APP_INTERNAL / "config" / name)}"; DestDir: "{{app}}\\_internal\\config"; '
        'Flags: ignoreversion onlyifdoesntexist; Check: ShouldKeepExistingCoreConfig'
        for name in CORE_CONFIG_FILES
    )
    core_runtime_config_keep_lines = "\n".join(
        f'Source: "{_inno_path(APP_INTERNAL / "config" / name)}"; DestDir: "{{app}}\\config"; '
        'Flags: ignoreversion onlyifdoesntexist; Check: ShouldKeepExistingCoreConfig'
        for name in CORE_CONFIG_FILES
    )
    core_config_backup_copy_lines = "\n".join(
        f'  if FileExists(ExpandConstant(\'{{app}}\\config\\{name}\')) then\n'
        f'    FileCopy(ExpandConstant(\'{{app}}\\config\\{name}\'), BackupDir + \'\\{name}\', False);'
        for name in CORE_CONFIG_FILES
    )
    if vigem_is_exe:
        vigem_install_filename = "{app}\\drivers\\ViGEmBus_Setup.exe"
        vigem_install_params = "/qn /norestart"
    else:
        vigem_install_filename = "{sys}\\msiexec.exe"
        vigem_install_params = (
            '/i "{app}\\drivers\\ViGEmBusSetup_x64.msi" '
            "/qn /norestart REINSTALL=ALL REINSTALLMODE=amus"
        )
    vigem_install_params = vigem_install_params.replace('"', '""')
    chinese_messages = """[Messages]
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=%1 卸载
InformationTitle=提示
ConfirmTitle=确认
ErrorTitle=错误
SetupLdrStartupMessage=即将安装 %1。是否继续？
AdminPrivilegesRequired=安装本程序需要管理员权限。
SetupAppRunningError=安装程序检测到 %1 正在运行。%n%n请先关闭程序后点击“确定”继续，或点击“取消”退出安装。
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。如果现在退出，程序将不会被完整安装。%n%n是否退出安装？
ButtonBack=< 上一步
ButtonNext=下一步 >
ButtonInstall=安装
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是
ButtonNo=否
ButtonFinish=完成
ButtonBrowse=浏览...
ClickNext=点击“下一步”继续，或点击“取消”退出安装。
BrowseDialogTitle=选择文件夹
BrowseDialogLabel=请选择一个文件夹，然后点击“确定”。
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=本向导将在你的电脑上安装 [name/ver]。%n%n建议在继续之前关闭其他正在运行的应用程序。
WizardSelectDir=选择安装位置
SelectDirDesc=请选择 [name] 的安装目录
SelectDirLabel3=安装程序会将 [name] 安装到以下文件夹。
SelectDirBrowseLabel=点击“下一步”继续。如需选择其他文件夹，请点击“浏览”。
DiskSpaceGBLabel=至少需要 [gb] GB 可用磁盘空间。
DiskSpaceMBLabel=至少需要 [mb] MB 可用磁盘空间。
DirExistsTitle=文件夹已存在
DirExists=文件夹：%n%n%1%n%n已经存在。是否仍然安装到该文件夹？
DirDoesntExistTitle=文件夹不存在
DirDoesntExist=文件夹：%n%n%1%n%n不存在。是否创建该文件夹？
WizardSelectTasks=选择附加任务
SelectTasksDesc=请选择安装时需要执行的附加任务
SelectTasksLabel2=请选择安装 [name] 时要执行的附加任务，然后点击“下一步”。
WizardReady=准备安装
ReadyLabel1=安装程序已准备好开始安装 [name]。
ReadyLabel2a=点击“安装”开始安装；如需检查或修改设置，请点击“上一步”。
ReadyLabel2b=点击“安装”开始安装。
ReadyMemoDir=安装位置：
ReadyMemoGroup=开始菜单文件夹：
ReadyMemoTasks=附加任务：
WizardPreparing=准备安装
PreparingDesc=安装程序正在准备安装 [name]。
ApplicationsFound=以下程序正在使用需要更新的文件。建议允许安装程序自动关闭这些程序。
ApplicationsFound2=以下程序正在使用需要更新的文件。建议允许安装程序自动关闭这些程序。安装完成后，安装程序会尝试重新启动它们。
CloseApplications=自动关闭这些程序
DontCloseApplications=不要关闭这些程序
WizardInstalling=正在安装
InstallingLabel=请稍候，安装程序正在将 [name] 安装到你的电脑。
FinishedHeadingLabel=[name] 安装向导完成
FinishedLabelNoIcons=[name] 已成功安装到你的电脑。
FinishedLabel=[name] 已成功安装到你的电脑。你可以通过已创建的快捷方式启动程序。
ClickFinish=点击“完成”退出安装程序。
RunEntryExec=运行 %1
SetupAborted=安装未完成。%n%n请修正问题后重新运行安装程序。
StatusClosingApplications=正在关闭应用程序...
StatusCreateDirs=正在创建文件夹...
StatusExtractFiles=正在释放文件...
StatusCreateIcons=正在创建快捷方式...
StatusSavingUninstall=正在保存卸载信息...
StatusRunProgram=正在完成安装...
StatusRollback=正在回滚更改...
ErrorExecutingProgram=无法执行文件：%n%1
FileExists2=文件已存在。
FileExistsOverwriteExisting=覆盖现有文件
FileExistsKeepExisting=保留现有文件
ExistingFileNewer2=现有文件比安装程序要安装的文件更新。
ExistingFileNewerOverwriteExisting=覆盖现有文件
ExistingFileNewerKeepExisting=保留现有文件（推荐）
ConfirmUninstall=确定要完全移除 %1 及其所有组件吗？
UninstallStatusLabel=请稍候，正在从你的电脑中移除 %1。
UninstalledAll=%1 已成功从你的电脑中移除。
UninstalledMost=%1 卸载完成。%n%n部分内容无法移除，可以手动删除。
WizardUninstalling=卸载状态
StatusUninstalling=正在卸载 %1...
"""

    chinese_custom_messages = """[CustomMessages]
NameAndVersion=%1 版本 %2
AdditionalIcons=附加快捷方式：
CreateDesktopIcon=创建桌面快捷方式
ProgramOnTheWeb=%1 官方页面
UninstallProgram=卸载 %1
LaunchProgram=启动 %1
"""

    content = f"""; Generated by build_installer.py. Do not edit by hand.
#define MyAppName "{APP_NAME}"
#define MyAppVersion "{version}"
#define MyAppPublisher "NTE"
#define MyAppExeName "{APP_EXE_NAME}"

[Setup]
AppId={APP_ID}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
DefaultDirName={{autopf}}\\NTE Drive Calc
DefaultGroupName={{#MyAppName}}
DisableProgramGroupPage=yes
OutputDir={_inno_path(OUTPUT_DIR)}
OutputBaseFilename=NTE_Drive_Calc_Setup_{{#MyAppVersion}}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
{setup_icon_line}PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
CloseApplications=yes
CloseApplicationsFilter=NTE_Drive_Calc.exe

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

{chinese_messages}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："
Name: "installvigem"; Description: "安装 ViGEmBus 虚拟手柄驱动"; GroupDescription: "运行依赖："; Flags: checkedonce
Name: "replacecoreconfig"; Description: "替换基础配置 JSON（roles / sets / stats / shapes）"; GroupDescription: "配置更新："

[Files]
Source: "{_inno_path(APP_EXE)}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{_inno_path(APP_INTERNAL)}\\*"; DestDir: "{{app}}\\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "{core_config_excludes}"
{core_internal_config_replace_lines}
{core_runtime_config_replace_lines}
{core_internal_config_keep_lines}
{core_runtime_config_keep_lines}
{vigem_file_line}

[Dirs]
Name: "{{app}}\\config"; Permissions: users-modify
Name: "{{app}}\\logs"; Permissions: users-modify
Name: "{{app}}\\scanned_images"; Permissions: users-modify

[Icons]
Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"
Name: "{{group}}\\安装 ViGEmBus 驱动"; Filename: "{vigem_install_filename}"; Parameters: "{vigem_install_params}"; WorkingDir: "{{app}}"; Check: IsWin64
Name: "{{group}}\\卸载 {{#MyAppName}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"; Tasks: desktopicon

[Run]
Filename: "{vigem_install_filename}"; Parameters: "{vigem_install_params}"; StatusMsg: "正在安装或修复 ViGEmBus 虚拟手柄驱动..."; Flags: waituntilterminated; Tasks: installvigem; Check: ShouldInstallViGEmBus
Filename: "{{cmd}}"; Parameters: "/C sc start ViGEmBus >NUL 2>NUL & exit /B 0"; StatusMsg: "正在启动 ViGEmBus 驱动服务..."; Flags: runhidden waituntilterminated; Tasks: installvigem; Check: IsWin64
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram,{{#StringChange(MyAppName, '&', '&&')}}}}"; Flags: nowait postinstall skipifsilent runascurrentuser

[Code]
procedure BackupCoreConfigBeforeReplace;
var
  BackupDir: string;
begin
  if not WizardIsTaskSelected('replacecoreconfig') then
    Exit;
  BackupDir := ExpandConstant('{{app}}\\config_backup\\' + GetDateTimeString('yyyymmddhhnnss', #0, #0));
  ForceDirectories(BackupDir);
{core_config_backup_copy_lines}
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    BackupCoreConfigBeforeReplace;
end;

function ShouldInstallViGEmBus: Boolean;
begin
  Result := IsWin64 and WizardIsTaskSelected('installvigem');
end;

function ShouldKeepExistingCoreConfig: Boolean;
begin
  Result := not WizardIsTaskSelected('replacecoreconfig');
end;

{chinese_custom_messages}
"""
    ISS_PATH.write_text(content, encoding="utf-8-sig")
    build_cli.ok(f"Wrote installer script: {ISS_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build NTE Drive Calc installer.")
    parser.add_argument("--version", default=os.environ.get("APP_VERSION") or _read_app_version())
    parser.add_argument("--skip-app-build", action="store_true", help="Use existing dist/NTE_Drive_Calc.")
    parser.add_argument("--generate-only", action="store_true", help="Generate .iss but do not run Inno Setup.")
    parser.add_argument("--skip-workshop-sync", action="store_true", help="Do not sync workshop weights before building the app bundle.")
    parser.add_argument("--require-workshop-sync", action="store_true", help="Fail release packaging if workshop weight sync cannot run.")
    args = parser.parse_args()

    try:
        skip_workshop_sync, require_workshop_sync = _choose_workshop_sync_mode(
            args.skip_workshop_sync,
            args.require_workshop_sync,
        )
        _ensure_app_bundle(
            skip_app_build=args.skip_app_build,
            skip_workshop_sync=skip_workshop_sync,
            require_workshop_sync=require_workshop_sync,
        )
        vigem_installer, vigem_is_exe = _find_vigem_installer()
        _write_iss(version=args.version, vigem_installer=vigem_installer, vigem_is_exe=vigem_is_exe)

        if args.generate_only:
            build_cli.ok("Generate-only mode complete.")
            return 0

        iscc = _find_iscc()
        if not iscc:
            build_cli.warn("Inno Setup compiler was not found.")
            build_cli.warn("Install Inno Setup 6, or set INNO_SETUP_ISCC to ISCC.exe.")
            build_cli.warn("Then run: .\\.venv\\Scripts\\python.exe build_installer.py --skip-app-build")
            return 2

        _run([str(iscc), str(ISS_PATH)])
        setup = OUTPUT_DIR / f"NTE_Drive_Calc_Setup_{args.version}.exe"
        if not setup.exists():
            raise RuntimeError(f"Installer build finished, but output was not found: {setup}")
        build_cli.ok(f"Installer complete: {setup}")
        return 0
    except subprocess.CalledProcessError as exc:
        build_cli.fail(f"Command failed with exit code {exc.returncode}")
        return exc.returncode
    except Exception as exc:
        build_cli.fail(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
