# 解除安裝「實驗規劃助手」：移除開機自動啟動、桌面與開始功能表捷徑、程式資料夾；設定與紀錄預設保留，會先詢問。
# 由同資料夾的「解除安裝.cmd」呼叫（它會先把本檔複製到暫存資料夾再執行，這樣程式資料夾才刪得掉）。
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\ExperimentPlanner'),
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [string]$StartMenuDir = [Environment]::GetFolderPath('Programs'),
    [string]$DataDir = (Join-Path $env:APPDATA 'InstrumentBooking'),
    [string]$ProfileDir = (Join-Path $env:LOCALAPPDATA 'InstrumentBooking'),
    [string]$RunKeyName = 'InstrumentBooking',
    [switch]$RemoveData,
    [switch]$Quiet,
    [string]$ProcessName = 'ExperimentPlanner'
)
$ErrorActionPreference = 'Stop'
$Title = '實驗規劃助手'
$ExeName = 'ExperimentPlanner.exe'
$RunKeyPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

function Show-Message([string]$Text, [int]$Icon) {
    if ($Quiet) { Write-Output $Text; return }
    (New-Object -ComObject WScript.Shell).Popup($Text, 0, $Title, $Icon) | Out-Null
}

function Confirm-RemoveData {
    if ($Quiet) { return [bool]$RemoveData }
    # 4 = 是/否、32 = 問號圖示、256 = 預設「否」；回傳 6 代表「是」。
    $answer = (New-Object -ComObject WScript.Shell).Popup(
        "要一併刪除設定與預約紀錄、Google 登入資料嗎？`n選「否」會保留，之後重新安裝可以繼續使用。", 0, $Title, 4 + 32 + 256)
    return ($answer -eq 6)
}

try {
    $prefix = $InstallDir.TrimEnd('\') + '\'
    $running = (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue) -or
        (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })
    if ($running) {
        throw "實驗規劃助手正在執行：請先在系統匣圖示按右鍵 →「結束」，再重新解除安裝。"
    }
    $hasFolder = Test-Path -LiteralPath $InstallDir
    if ($hasFolder -and
        (Get-ChildItem -LiteralPath $InstallDir -Force | Select-Object -First 1) -and
        -not (Test-Path -LiteralPath (Join-Path $InstallDir $ExeName))) {
        throw "安裝位置不是本程式的資料夾，為避免刪除不相關的檔案，已停止解除安裝：$InstallDir"
    }
    $removeData = Confirm-RemoveData

    # 程式資料夾最容易刪不掉（檔案被佔用），先刪；失敗時自動啟動與捷徑都還在，可以直接重試。
    if ($hasFolder) { Remove-Item -LiteralPath $InstallDir -Recurse -Force }
    Remove-ItemProperty -Path $RunKeyPath -Name $RunKeyName -ErrorAction SilentlyContinue
    foreach ($link in @((Join-Path $DesktopDir "$Title.lnk"),
                        (Join-Path $StartMenuDir "$Title.lnk"),
                        (Join-Path $StartMenuDir "解除安裝$Title.lnk"))) {
        Remove-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    }
    if ($removeData) {
        foreach ($dir in @($DataDir, $ProfileDir)) {
            if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        }
    }
    $kept = if ($removeData) { '設定與紀錄也已一併刪除。' } else { "設定與紀錄已保留在 $DataDir。" }
    Show-Message "解除安裝完成。$kept" 64
    exit 0
}
catch {
    Show-Message "解除安裝失敗：$($_.Exception.Message)" 16
    exit 1
}
