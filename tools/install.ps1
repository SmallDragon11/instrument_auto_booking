# 安裝「實驗規劃助手」：複製程式到 %LOCALAPPDATA%\Programs\ExperimentPlanner，建立桌面與開始功能表捷徑，然後啟動。
# 由同資料夾的「安裝.cmd」呼叫；更新版本時再執行一次即可（會覆蓋程式檔，設定與紀錄不受影響）。
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\ExperimentPlanner'),
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [string]$StartMenuDir = [Environment]::GetFolderPath('Programs'),
    [switch]$NoLaunch,
    [switch]$Quiet
)
$ErrorActionPreference = 'Stop'
$Title = '實驗規劃助手'
$ExeName = 'ExperimentPlanner.exe'

function Show-Message([string]$Text, [int]$Icon) {
    if ($Quiet) { Write-Output $Text; return }
    (New-Object -ComObject WScript.Shell).Popup($Text, 0, $Title, $Icon) | Out-Null
}

try {
    $source = Join-Path $PSScriptRoot 'ExperimentPlanner'
    if (-not (Test-Path (Join-Path $source $ExeName))) {
        throw "找不到程式檔案：請先把整個 zip 解壓縮，再執行資料夾裡的「安裝.cmd」。"
    }
    if (Get-Process -Name 'ExperimentPlanner' -ErrorAction SilentlyContinue) {
        throw "實驗規劃助手正在執行：請先在系統匣圖示按右鍵 →「結束」，再重新安裝。"
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    robocopy $source $InstallDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "複製程式檔案失敗（robocopy 代碼 $LASTEXITCODE）。" }
    Get-ChildItem -Path $InstallDir -Recurse -File | Unblock-File
    $exe = Join-Path $InstallDir $ExeName
    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in @($DesktopDir, $StartMenuDir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $link = $shell.CreateShortcut((Join-Path $dir "$Title.lnk"))
        $link.TargetPath = $exe
        $link.WorkingDirectory = $InstallDir
        $link.IconLocation = "$exe,0"
        $link.Description = $Title
        $link.Save()
    }
    if (-not $NoLaunch) { Start-Process -FilePath $exe }
    Show-Message "安裝完成。之後可以從桌面或開始功能表的「$Title」開啟。" 64
    exit 0
}
catch {
    Show-Message "安裝失敗：$($_.Exception.Message)" 16
    exit 1
}
