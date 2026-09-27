# 在 Windows 上把 QuantDinger 部署到剩余空间最大的本地磁盘。
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File .\deploy-quantdinger.ps1
# 或直接双击同目录下的 deploy-quantdinger.bat
#
# 可选环境变量：
#   $env:QUANTDINGER_INSTALL_REF = "main"   # 要安装的 QuantDinger 分支/标签

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# DriveType=3 表示本地固定磁盘（排除 U 盘、光驱、网络盘）
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    Sort-Object FreeSpace -Descending
if (-not $disks) {
    Write-Host "未找到本地固定磁盘。" -ForegroundColor Red
    exit 1
}

Write-Host "检测到的本地磁盘：" -ForegroundColor Cyan
foreach ($d in $disks) {
    "{0}  剩余 {1,8:N1} GB / 共 {2,8:N1} GB" -f $d.DeviceID, ($d.FreeSpace / 1GB), ($d.Size / 1GB)
}

$target = $disks[0]
$installDir = Join-Path "$($target.DeviceID)\" "QuantDinger"
Write-Host ""
Write-Host ("将安装到剩余空间最大的磁盘 {0}：{1}" -f $target.DeviceID, $installDir) -ForegroundColor Green

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "未检测到 Docker。请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}

# 提醒：镜像和数据库卷由 Docker Desktop 存放在它自己的磁盘镜像里，默认在 C 盘
if ($target.DeviceID -ne "C:") {
    Write-Host ""
    Write-Host "提示：Docker 镜像和数据库数据默认保存在 Docker Desktop 的虚拟磁盘中（通常在 C 盘）。" -ForegroundColor Yellow
    Write-Host ("      如需一并放到 {0}，请在 Docker Desktop -> Settings -> Resources -> Advanced -> Disk image location" -f $target.DeviceID) -ForegroundColor Yellow
    Write-Host ("      改为 {0}\DockerData 后点 Apply & restart，再运行本脚本。" -f $target.DeviceID) -ForegroundColor Yellow
    Write-Host ""
    $answer = Read-Host "继续安装？[Y/n]"
    if ($answer -and $answer -notmatch '^[Yy]') { exit 0 }
}

$env:QUANTDINGER_INSTALL_DIR = $installDir
$ref = if ($env:QUANTDINGER_INSTALL_REF) { $env:QUANTDINGER_INSTALL_REF } else { "main" }

# 调用 QuantDinger 官方安装程序（会询问管理员账号密码、生成密钥并启动服务）
Invoke-RestMethod "https://raw.githubusercontent.com/OpenByteInc/QuantDinger/$ref/install.ps1" | Invoke-Expression
