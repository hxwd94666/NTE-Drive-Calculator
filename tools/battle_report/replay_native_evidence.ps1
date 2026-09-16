# 以独立 Rust 核心只读重放已保存的原生战报，输出诊断与模型缺口。
param(
    [Parameter(Mandatory)][string]$UserDatabase,
    [Parameter(Mandatory)][long]$RecordId,
    [long]$PanelRecordId = 0,
    [string]$OutputPath = ''
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$exe = Join-Path $root 'third_party/analysis-core/bin/nte-analysis-core.exe'
$manifest = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'third_party/analysis-core/component.json') | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256 -or
    $manifest.capabilities -notcontains 'engine_report_v2') { throw '分析组件哈希或能力不匹配。' }
if ($PanelRecordId -eq 0) { $PanelRecordId = $RecordId }
if (!$OutputPath) { $OutputPath = Join-Path $root "output/native-report-$RecordId.json" }
$request = @{schema_version='nte-analysis-request-v1'; batch_kind='engine_report_v2'; request=@{
    user_database_path=(Resolve-Path -LiteralPath $UserDatabase).Path
    static_database_path=(Join-Path $root 'data/game_static.sqlite3')
    battle_record_id=$RecordId; panel_record_id=$PanelRecordId
}}
$response = $request | ConvertTo-Json -Depth 8 -Compress | & $exe --threads 1
if ($LASTEXITCODE -ne 0) { throw 'Rust 战报重放失败，请查看上方固定错误码。' }
$result = $response | ConvertFrom-Json -Depth 100
if ($result.batch_kind -ne 'engine_report_v2') { throw '分析响应类型不匹配。' }
$destination = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
[IO.File]::WriteAllText($destination, ($response -join "`n"), [Text.UTF8Encoding]::new($false))
$result.result.summaries | Format-Table scope,numeric_hits,exact_hits,weighted_absolute_error
Write-Output "诊断结果：$destination"
Write-Output '存在模型缺口的部分结果不能作为完整战报残差。'
