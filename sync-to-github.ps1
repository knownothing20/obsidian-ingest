# obsidian-ingest 同步脚本
# 用途：从开发目录脱敏复制到 GitHub 目录，自动提交
# 用法：.\sync-to-github.ps1 [-Message "commit message"]

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$src = "/path/to/skill"
$dst = "D:\GitHub\obsidian-ingest"

Write-Host "📦 同步 obsidian-ingest 到 GitHub..." -ForegroundColor Cyan

# 清理目标目录（保留 .git）
Get-ChildItem $dst -Recurse -File | Where-Object {
    $_.FullName -notmatch '\\\.git\\' -and $_.FullName -notmatch '\\\.git$'
} | Remove-Item -Force

# 复制（排除 local/ 和 __pycache__/）
Get-ChildItem $src -Recurse -File | Where-Object {
    $_.FullName -notmatch '\\local\\' -and
    $_.FullName -notmatch '\\__pycache__\\' -and
    $_.Name -ne 'sync-to-github.ps1' -and
    $_.Name -ne '.gitignore'
} | ForEach-Object {
    $rel = $_.FullName.Replace($src, "")
    $destPath = Join-Path $dst $rel
    $destDir = Split-Path $destPath -Parent
    if (!(Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    Copy-Item $_.FullName $destPath
}

# 复制 .gitignore
Copy-Item "$src\.gitignore" "$dst\.gitignore" -Force

# 提交
Set-Location $dst
$changes = git status --porcelain
if ([string]::IsNullOrWhiteSpace($changes)) {
    Write-Host "✅ 没有变更，跳过提交" -ForegroundColor Green
    exit 0
}

git add -A

if ([string]::IsNullOrWhiteSpace($Message)) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    $Message = "sync: update from dev ($timestamp)"
}

git commit -m $Message
git push origin master 2>&1 | ForEach-Object { $_ }

Write-Host ""
Write-Host "✅ 已同步并推送到 GitHub" -ForegroundColor Green
Write-Host "🔗 https://github.com/knownothing20/obsidian-ingest" -ForegroundColor Cyan
