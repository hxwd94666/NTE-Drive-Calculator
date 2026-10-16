# 精确旧发行前置维护：默认只读计划，显式清理与外置恢复，不启动应用或安装器。
#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Apply', 'Rollback')][string]$Mode = 'Plan',
    [Parameter(Mandatory = $true)][string]$Profile,
    [Parameter(Mandatory = $true)][string]$ProfileSha256,
    [Parameter(Mandatory = $true)][string]$TargetRoot,
    [string]$NewPackageRoot,
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [switch]$ApplicationStopped
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$held = [System.Collections.Generic.List[System.IO.Stream]]::new()
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)
# Windows 按已验证句柄标记删除，避免关闭句柄再按路径删除的替换窗口。
Add-Type -ReferencedAssemblies System.Web.Extensions -TypeDefinition @'
using System;
using System.IO;
using System.ComponentModel;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;
public static class LegacyTakeoverNative {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle handle, int kind, ref byte data, uint length);
    public static FileStream OpenExclusive(string name) {
        var handle = CreateFile(name, 0xC0010000, 0, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (handle.IsInvalid) { int error=Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
        return new FileStream(handle, FileAccess.ReadWrite);
    }
    public static void DeleteExact(FileStream stream) {
        byte delete = 1;
        if (!SetFileInformationByHandle(stream.SafeFileHandle, 4, ref delete, 1)) throw new Win32Exception(Marshal.GetLastWin32Error());
    }
    public static void UniqueKeys(string json) {
        var stack = new Stack<HashSet<string>>();
        var decoder = new JavaScriptSerializer();
        var tokens = Regex.Matches(json, "\"(?:\\\\.|[^\"\\\\])*\"|[{}\\[\\]:,]|[^{}\\[\\]:,\\s]+");
        for (int i=0; i<tokens.Count; i++) {
            string token=tokens[i].Value;
            if (token=="{") stack.Push(new HashSet<string>(StringComparer.Ordinal));
            else if (token=="[") stack.Push(null);
            else if (token=="}" || token=="]") { if (stack.Count>0) stack.Pop(); }
            else if (token.StartsWith("\"") && i+1<tokens.Count && tokens[i+1].Value==":" && stack.Count>0 && stack.Peek()!=null) {
                if (!stack.Peek().Add(decoder.Deserialize<string>(token))) throw new InvalidDataException("Duplicate JSON key.");
            }
        }
    }
}
'@

function Full-Path([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) { throw 'Use absolute paths.' }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($full.Length -le 3 -or $full.StartsWith('\\')) { throw 'A local non-root directory is required.' }
    return $full
}
function Check-Links([string]$Path) {
    $part = [IO.Path]::GetFullPath($Path)
    while ($part) {
        if (Test-Path -LiteralPath $part) {
            $item = Get-Item -Force -LiteralPath $part
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Linked path: $part" }
        }
        $parent = [IO.Directory]::GetParent($part)
        if ($null -eq $parent) { break }
        $part = $parent.FullName
    }
}
function Within([string]$Path, [string]$Root) {
    return $Path.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)
}
function Bootstrap([string]$Path) { return [IO.Path]::GetExtension($Path).ToLowerInvariant() -in @('.exe','.dll','.pyd','.pyz','.pyc','.pyo','.py','.zip','.pth') }
function Relative-Path([string]$Value) {
    if (-not $Value -or $Value.Contains('\') -or $Value.Contains(':')) { throw 'Invalid relative path.' }
    foreach ($part in $Value.Split('/')) {
        if (-not $part -or $part -in @('.','..') -or $part -match '[. ]$|[\x00-\x1f<>"|?*]' -or $part -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])([.]|$)') { throw "Windows path alias: $Value" }
    }
    if ($Value -cne 'NTE_Drive_Calc.exe' -and $Value -cne 'distribution.json' -and -not $Value.StartsWith('_internal/', [StringComparison]::Ordinal)) { throw "Non-release path: $Value" }
    return $Value
}
function Child-Path([string]$Root, [string]$Relative) {
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($Root, $Relative.Replace('/', '\')))
    if (-not (Within $path $Root) -or $path -eq $Root) { throw 'Path escaped its root.' }
    Check-Links $path
    return $path
}
function Read-Json([string]$Path) {
    Check-Links $Path
    if (-not [IO.File]::Exists($Path) -or (Get-Item -LiteralPath $Path).Length -gt 33554432) { throw "Invalid JSON file: $Path" }
    $json = $utf8.GetString([IO.File]::ReadAllBytes($Path))
    if ($json.Length -gt 0 -and $json[0] -eq [char]0xfeff) { throw 'JSON BOM is not accepted by the application contract.' }
    [LegacyTakeoverNative]::UniqueKeys($json)
    return $json | ConvertFrom-Json
}
function Hash-Stream([IO.Stream]$Stream) {
    $Stream.Position = 0
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $Stream.Position = 0 }
}
function Open-Locked([string]$Path, [bool]$Exclusive) {
    Check-Links $Path
    $access = [IO.FileAccess]::Read
    $sharing = [IO.FileShare]::Read
    if ($Exclusive) { $stream = [LegacyTakeoverNative]::OpenExclusive($Path) }
    else { $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, $access, $sharing) }
    $held.Add($stream)
    return ,$stream
}
function Match-File([IO.Stream]$Stream, $Row) {
    if ($Stream.Length -ne $Row.size -or (Hash-Stream $Stream) -cne $Row.sha256) { throw "Changed or incomplete file: $($Row.path)" }
}
function Rows($Value, [bool]$New) {
    if ($Value.schema_version -ne 1 -or $Value.product -cne 'nte-drive-calc' -or $Value.files -isnot [Array] -or $Value.files.Count -eq 0) { throw 'Invalid manifest identity.' }
    if ($Value.schema_version -isnot [int] -and $Value.schema_version -isnot [long]) { throw 'Invalid schema type.' }
    if ($New -and (($Value.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'architecture,build_id,files,product,schema_version,version') { throw 'Invalid manifest fields.' }
    if ($New) {
        foreach ($key in @('product','architecture','version','build_id')) {
            if ($Value.$key -isnot [string] -or -not $Value.$key) { throw 'Manifest identities must be nonempty strings.' }
        }
    }
    $map = @{}; $spelling = @{}
    foreach ($row in $Value.files) {
        if (($row.PSObject.Properties.Name | Sort-Object) -join ',' -cne 'bootstrap,path,sha256,size') { throw 'Invalid file fields.' }
        if ($row.path -isnot [string]) { throw 'Invalid path type.' }
        $path = Relative-Path $row.path
        if ($row.size -isnot [long] -and $row.size -isnot [int]) { throw 'Invalid size type.' }
        if ($row.size -lt 0 -or $row.sha256 -isnot [string] -or $row.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $row.bootstrap -isnot [bool] -or $row.bootstrap -ne (Bootstrap $path)) { throw 'Invalid size/hash/bootstrap.' }
        if ($map.ContainsKey($path)) { throw 'Duplicate Windows file identity.' }
        $prefix = ''
        foreach ($part in $path.Split('/')) {
            $prefix = ($prefix + '/' + $part).TrimStart('/')
            if ($spelling.ContainsKey($prefix) -and $spelling[$prefix] -cne $prefix) { throw 'Conflicting directory spelling.' }
            $spelling[$prefix] = $prefix
            if ($prefix -ne $path -and $map.ContainsKey($prefix)) { throw 'File/directory collision.' }
        }
        foreach ($existing in $map.Keys) { if ($existing.StartsWith($path + '/', [StringComparison]::OrdinalIgnoreCase)) { throw 'File/directory collision.' } }
        $map[$path] = $row
    }
    if (-not $map.ContainsKey('NTE_Drive_Calc.exe') -or ($New -and -not $map.ContainsKey('distribution.json'))) { throw 'Missing entrypoint/metadata.' }
    return ,$map
}
function Inventory([string]$Root) {
    $map = @{}; $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($Root)
    while ($pending.Count) {
        $directory = $pending.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            $relative = $item.FullName.Substring($Root.Length + 1).Replace('\', '/')
            if ($directory -eq $Root) {
                if ($item.Name -ieq 'unins000.exe') {
                    # 仅保留这一非组件候选；文件名不是内容可信或卸载登记有效的证明。
                    Check-Links $item.FullName
                    if ($item.PSIsContainer) { throw 'Uninstaller name is not a regular file.' }
                    continue
                }
                if ($item.Name -ine '_internal' -and $item.Name -ine 'release-files.json' -and $item.Name -ine 'distribution.json' -and -not (Bootstrap $item.Name)) { continue }
            }
            Check-Links $item.FullName
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
            else {
                if ($map.ContainsKey($relative)) { throw 'Duplicate Windows disk identity.' }
                $map[$relative] = $relative
            }
        }
    }
    return ,$map
}
function Write-Json([string]$Path, $Value) {
    Check-Links $Path
    $bytes = $utf8.GetBytes(($Value | ConvertTo-Json -Depth 16))
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

try {
    $target = Full-Path $TargetRoot; $backup = Full-Path $BackupRoot; $profilePath = Full-Path $Profile
    foreach ($path in @($target, $backup, $profilePath, $PSCommandPath)) { Check-Links $path }
    if ((Within $backup $target) -or (Within $target $backup) -or (Within $PSCommandPath $target) -or (Within $profilePath $target)) { throw 'Tool, profile and backup must be outside the target loading chain.' }
    if (-not [IO.Directory]::Exists($target)) { throw 'Target directory is missing.' }
    if ($ProfileSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Supply the reviewed profile SHA256.' }
    $profileHandle = Open-Locked $profilePath $false
    if ((Hash-Stream $profileHandle) -cne $ProfileSha256) { throw 'Profile pin mismatch.' }
    $oldValue = Read-Json $profilePath
    $old = Rows $oldValue $false
    if (-not $oldValue.profile_id) { throw 'Missing reviewed profile identity.' }
    if (-not $ApplicationStopped) { throw 'Exit the application and explicitly pass -ApplicationStopped; locks do not replace this requirement.' }

    if ($Mode -eq 'Rollback') {
        $journalPath = Child-Path $backup 'journal.json'
        $journalHandle = Open-Locked $journalPath $false
        $journal = Read-Json $journalPath
        if ($journal.schema_version -ne 1 -or $journal.target_root -ine $target -or $journal.profile_sha256 -cne $ProfileSha256) { throw 'Rollback identity mismatch.' }
        $new = Rows $journal.new_manifest $true
        if ($journal.recovery_id -cnotmatch '^[0-9a-f]{32}$' -or $journal.new_manifest_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid rollback package identity.' }
        $currentManifest = Open-Locked (Child-Path $target 'release-files.json') $true
        if ((Hash-Stream $currentManifest) -cne $journal.new_manifest_sha256) { throw 'Rollback belongs to a different current package.' }
        foreach ($row in $new.Values) {
            $current = Open-Locked (Child-Path $target $row.path) $true
            Match-File $current $row
        }
        $restore = @(); $seen = @{}; $index = 0; $stale = @()
        foreach ($relative in $journal.retired) {
            $null = Relative-Path $relative
            if ($seen.ContainsKey($relative) -or -not $old.ContainsKey($relative) -or $new.ContainsKey($relative)) { throw 'Rollback row is not an exact old-minus-new member.' }
            $seen[$relative] = $true
            $source = Open-Locked (Child-Path $backup ('files/' + $relative)) $false
            Match-File $source $old[$relative]
            $destination = Child-Path $target $relative
            # 日志中的随机 recovery_id 精确保留临时名；重试只清理该日志声明的暂存，不用通配符。
            $temporary = [IO.Path]::Combine([IO.Path]::GetDirectoryName($destination), '.nte-recovery-' + $journal.recovery_id + '-' + $index + '.tmp')
            $index++
            Check-Links $temporary
            if (Test-Path -LiteralPath $temporary) {
                if (-not [IO.File]::Exists($temporary)) { throw 'Recovery staging path is not a regular file.' }
                $stale += Open-Locked $temporary $true
            }
            if ([IO.File]::Exists($destination)) {
                $existing = Open-Locked $destination $true
                Match-File $existing $old[$relative]
            } elseif (Test-Path -LiteralPath $destination) { throw 'Rollback target is not a file.' }
            else { $restore += [PSCustomObject]@{ source = $source; destination = $destination; row = $old[$relative]; temporary = $temporary } }
        }
        foreach ($stream in $stale) { [LegacyTakeoverNative]::DeleteExact($stream); $stream.Dispose() }
        foreach ($item in $restore) {
            Check-Links $item.destination
            $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($item.destination))
            $output = [IO.File]::Open($item.temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            try { $item.source.CopyTo($output); $output.Flush($true); Match-File $output $item.row } finally { $output.Dispose() }
            Check-Links $item.destination
            # 暂存在目标同目录，跨卷备份也只在完整复制之后同卷原子发布；绝不覆盖新出现的文件。
            [IO.File]::Move($item.temporary, $item.destination)
        }
        [PSCustomObject]@{ mode = 'Rollback'; restored = @($restore | ForEach-Object { $_.row.path }); note = 'Only retired files restored; complete new-package overlay and all user data unchanged.' } | ConvertTo-Json -Depth 8
    } else {
        $package = Full-Path $NewPackageRoot
        if ((Within $package $target) -or (Within $target $package) -or (Within $backup $package) -or (Within $package $backup)) { throw 'New package, target and backup must be separate trees.' }
        Check-Links $package
        $manifestPath = Child-Path $package 'release-files.json'
        $manifestHandle = Open-Locked $manifestPath $false
        $manifestHash = Hash-Stream $manifestHandle
        $value = Read-Json $manifestPath; $new = Rows $value $true
        $metadataPath = Child-Path $package 'distribution.json'
        $metadataHandle = Open-Locked $metadataPath $false
        if ((Get-Item -LiteralPath $metadataPath).Length -gt 65536) { throw 'Distribution metadata exceeds the application limit.' }
        $metadata = Read-Json $metadataPath
        if ($value.architecture -cne 'x64' -or $metadata.schema_version -ne 1 -or $metadata.entrypoint -cne 'NTE_Drive_Calc.exe' -or $metadata.release_manifest -cne 'release-files.json' -or $metadata.build_id -eq 'legacy') { throw 'Unsupported package metadata.' }
        foreach ($field in @('product','architecture','version','build_id')) {
            if (-not $value.$field -or $value.$field -cne $metadata.$field) { throw 'Package identity mismatch.' }
        }
        if ($metadata.distribution -cnotin @('installed','portable') -or ($metadata.distribution -eq 'installed' -and $metadata.update_channel -cne 'installer') -or ($metadata.distribution -eq 'portable' -and $metadata.update_channel -cnotin @('manual','platform'))) { throw 'Invalid update channel.' }
        $required = @('schema_version','product','distribution','update_channel','version','build_id','architecture','entrypoint','release_manifest')
        $optional = @('data_policy','data_directory','data_root_key')
        foreach ($key in $required) { if ($key -cnotin $metadata.PSObject.Properties.Name) { throw 'Missing distribution field.' } }
        foreach ($property in $metadata.PSObject.Properties) {
            if ($property.Name -cnotin ($required + $optional)) { throw 'Unknown distribution field.' }
            if ($property.Name -ne 'schema_version' -and $property.Value -isnot [string]) { throw 'Invalid distribution field type.' }
        }
        if ($metadata.schema_version -isnot [int] -and $metadata.schema_version -isnot [long]) { throw 'Invalid distribution schema type.' }
        $policy = 'userdata'; $directory = 'userdata'; $rootKey = ''
        if ('data_policy' -cin $metadata.PSObject.Properties.Name) { $policy = $metadata.data_policy }
        if ('data_directory' -cin $metadata.PSObject.Properties.Name) { $directory = $metadata.data_directory }
        if ('data_root_key' -cin $metadata.PSObject.Properties.Name) { $rootKey = $metadata.data_root_key }
        if ($policy -cnotin @('userdata','external') -or $directory -cne 'userdata') { throw 'Invalid distribution data policy.' }
        if ($policy -eq 'external') {
            if ($metadata.distribution -cne 'portable' -or $metadata.update_channel -cne 'platform' -or $rootKey -cnotmatch '^[A-Za-z0-9_-]{1,80}$') { throw 'Invalid external data root identity.' }
        } elseif ($rootKey) { throw 'Unexpected external data root identity.' }
        # 两个树中的所有当前文件均先校验并持锁；从此到清理完成不允许覆盖或启动入口。
        foreach ($row in $new.Values) {
            $source = Open-Locked (Child-Path $package $row.path) $false; Match-File $source $row
            $current = Open-Locked (Child-Path $target $row.path) $true; Match-File $current $row
        }
        $targetManifest = Open-Locked (Child-Path $target 'release-files.json') $true
        if ((Hash-Stream $targetManifest) -cne $manifestHash) { throw 'Target has a different new manifest.' }
        $disk = Inventory $target
        $packageDisk = Inventory $package
        foreach ($path in $packageDisk.Keys) { if ($path -ine 'release-files.json' -and -not $new.ContainsKey($path)) { throw "Unlisted file in complete new package: $path" } }
        $planned = @()
        foreach ($path in ($disk.Keys | Sort-Object)) {
            if ($path -ieq 'release-files.json' -or $new.ContainsKey($path)) { continue }
            if (-not $old.ContainsKey($path)) { throw "Unknown loading file retained: $path" }
            $stream = Open-Locked (Child-Path $target $disk[$path]) $true
            Match-File $stream $old[$path]
            $planned += [PSCustomObject]@{ path = $disk[$path]; row = $old[$path]; stream = $stream }
        }
        $result = [ordered]@{ mode = $Mode; profile_id = $oldValue.profile_id; new_build_id = $value.build_id; retired = @($planned | ForEach-Object { $_.path }); backup = $null; next_step = 'Existing users: before first GUI startup, run the complete new EXE with --migrate-data-from OLD_ROOT --confirm-source-closed (platform external root selection first). Program cleanup does not migrate accounts. See docs/integrations.md.' }
        if ($Mode -eq 'Apply' -and $planned.Count) {
            # 外置备份的完整字节和日志先落盘；中断后无需猜测删除进度即可精确恢复缺失成员。
            $run = Child-Path $backup ([Guid]::NewGuid().ToString('N'))
            $null = [IO.Directory]::CreateDirectory($run)
            foreach ($item in $planned) {
                $path = Child-Path $run ('files/' + $item.path)
                $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path))
                $output = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                try { $item.stream.CopyTo($output); $output.Flush($true); Match-File $output $item.row } finally { $output.Dispose() }
            }
            $index = 0
            foreach ($item in $planned) {
                $destination = Child-Path $target $item.row.path
                $reserved = [IO.Path]::Combine([IO.Path]::GetDirectoryName($destination), '.nte-recovery-' + [IO.Path]::GetFileName($run) + '-' + $index + '.tmp')
                Check-Links $reserved
                if (Test-Path -LiteralPath $reserved) { throw 'Recovery staging name already exists.' }
                $index++
            }
            Write-Json (Child-Path $run 'journal.json') ([ordered]@{ schema_version = 1; target_root = $target; profile_sha256 = $ProfileSha256; recovery_id = [IO.Path]::GetFileName($run); new_manifest = $value; new_manifest_sha256 = $manifestHash; retired = @($planned | ForEach-Object { $_.row.path }) })
            $result.backup = $run
            # 先给出恢复位置，即使后续删除遇到占用也可从外置 journal 重放恢复。
            Write-Host "RECOVERY $run"
            foreach ($item in $planned) {
                $path = Child-Path $target $item.path
                Match-File $item.stream $item.row
                [LegacyTakeoverNative]::DeleteExact($item.stream)
                $item.stream.Dispose()
                if (Test-Path -LiteralPath $path) { throw "Retired file still exists: $($item.path)" }
            }
        }
        $result | ConvertTo-Json -Depth 8
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    foreach ($stream in $held) { $stream.Dispose() }
}
