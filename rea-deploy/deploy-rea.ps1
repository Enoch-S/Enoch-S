# 在 Windows 上把 REA（https://github.com/morluto/rea）部署到剩余空间最大的本地磁盘。
#
# 用法：双击同目录下的 deploy-rea.bat
# 或在 PowerShell 中运行：
#   powershell -ExecutionPolicy Bypass -File .\deploy-rea.ps1
#
# 可选环境变量：
#   $env:REA_VERSION = "6.4.0"                                   # 要安装的 REA 版本，默认最新版
#   $env:REA_NODE_MIRROR = "https://npmmirror.com/mirrors/node"  # Node.js 下载镜像（国内网络可用）
#   $env:REA_NO_SETUP = "1"                                      # 只安装，不启动 rea setup

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell 5.1 显示下载进度会让下载变得非常慢
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Fail([string]$message) {
    Write-Host "部署失败：$message" -ForegroundColor Red
    exit 1
}

if (-not [Environment]::Is64BitOperatingSystem) { Fail "REA 需要 64 位 Windows。" }

$package = "rea-agents"
$version = if ($env:REA_VERSION) { $env:REA_VERSION } else { "latest" }
$nodeMirror = if ($env:REA_NODE_MIRROR) { $env:REA_NODE_MIRROR.TrimEnd('/') } else { "https://nodejs.org/dist" }

# DriveType=3 表示本地固定磁盘（排除 U 盘、光驱、网络盘）
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Sort-Object FreeSpace -Descending)
if ($disks.Count -eq 0) { Fail "未找到本地固定磁盘。" }

Write-Host "检测到的本地磁盘：" -ForegroundColor Cyan
foreach ($d in $disks) {
    "{0}  剩余 {1,8:N1} GB / 共 {2,8:N1} GB" -f $d.DeviceID, ($d.FreeSpace / 1GB), ($d.Size / 1GB)
}

$target = $disks[0]
$drive = "$($target.DeviceID)\"
$installDir = Join-Path $drive "REA"
$nodeDir = Join-Path $installDir "node"
$npmPrefix = Join-Path $installDir "npm-global"
$npmCache = Join-Path $installDir "npm-cache"

Write-Host ""
Write-Host ("将安装到剩余空间最大的磁盘 {0}：{1}" -f $target.DeviceID, $installDir) -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $installDir | Out-Null

function Get-NodeVersion([string]$nodeExe) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $v = & $nodeExe -p "process.versions.node" 2>$null } catch { $v = $null }
    finally { $ErrorActionPreference = $previous }
    if ($v) { return "$v".Trim() }
    return ""
}

# REA 需要 Node.js 22.x (>=22.19)、24.x (>=24.11) 或 26+
function Test-NodeSupported([string]$v) {
    if ($v -notmatch '^(\d+)\.(\d+)\.') { return $false }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    return (($major -eq 22 -and $minor -ge 19) -or ($major -eq 24 -and $minor -ge 11) -or $major -ge 26)
}

# 下载 Node.js 24 LTS 便携版到安装目录，不影响系统里已有的 Node.js。
# REA 的 Windows 原生组件只有 x64 版本，ARM64 的 Windows 也用 x64 版 Node.js（系统自带转译）。
function Install-PortableNode {
    $work = Join-Path $installDir (".download-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        $sums = (Invoke-WebRequest -UseBasicParsing "$nodeMirror/latest-v24.x/SHASUMS256.txt").Content
        if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
        $match = [regex]::Match($sums, '(?m)^([0-9a-f]{64})\s+(node-v[\d.]+-win-x64\.zip)\s*$')
        if (-not $match.Success) { throw "未找到适用于 win-x64 的 Node.js" }
        $hash = $match.Groups[1].Value
        $file = $match.Groups[2].Value
        $zip = Join-Path $work $file

        Write-Host "正在下载 $file ……"
        Invoke-WebRequest -UseBasicParsing "$nodeMirror/latest-v24.x/$file" -OutFile $zip
        if ((Get-FileHash -Algorithm SHA256 $zip).Hash -ne $hash) { throw "安装包校验失败" }

        Write-Host "正在解压 Node.js ……"
        $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
        if ($tar) {
            & $tar.Source -xf $zip -C $work
            if ($LASTEXITCODE -ne 0) { throw "解压失败" }
        } else {
            Expand-Archive -Path $zip -DestinationPath $work -Force
        }
        if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
        Move-Item (Join-Path $work ([IO.Path]::GetFileNameWithoutExtension($file))) $nodeDir
    } catch {
        Fail ("Node.js 下载或安装失败（{0}）。可设置 REA_NODE_MIRROR 使用镜像后重试。" -f $_.Exception.Message)
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}

$portableNode = Join-Path $nodeDir "node.exe"
$systemNode = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
$systemNodeVersion = if ($systemNode) { Get-NodeVersion $systemNode.Source } else { "" }
$usePortable = $false

if ((Test-Path $portableNode) -and (Test-NodeSupported (Get-NodeVersion $portableNode))) {
    $usePortable = $true
} elseif ((Test-NodeSupported $systemNodeVersion) -and (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    Write-Host "使用已安装的 Node.js $systemNodeVersion"
} else {
    if ($systemNode) {
        Write-Host "已安装的 Node.js $systemNodeVersion 版本不满足 REA 要求（22.19+、24.11+ 或 26+）。" -ForegroundColor Yellow
    }
    Write-Host "将在 $nodeDir 安装 Node.js 24 LTS 便携版。"
    Install-PortableNode
    $usePortable = $true
}

if ($usePortable) {
    $env:Path = "$nodeDir;$env:Path"
    $npm = Join-Path $nodeDir "npm.cmd"
    Write-Host "使用 Node.js $(Get-NodeVersion $portableNode)（$nodeDir）"
} else {
    $npm = (Get-Command npm.cmd).Source
}
$env:Path = "$npmPrefix;$env:Path"

# 智能体通过 npx 启动 REA 的 MCP 服务，程序会下载到 npm 缓存（默认在 C 盘）；把缓存也放到大磁盘
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$currentCache = & $npm config get cache 2>$null | Select-Object -First 1
$ErrorActionPreference = $previous
$currentCache = if ($currentCache) { "$currentCache".Trim() } else { "" }
$cacheOnTarget = $currentCache -and ([IO.Path]::GetPathRoot($currentCache) -eq $drive)

if (-not $cacheOnTarget) {
    $shownCache = if ($currentCache) { $currentCache } else { "未知" }
    Write-Host ""
    Write-Host "智能体会通过 npx 运行 REA，相关文件下载到 npm 缓存（当前：$shownCache）。"
    Write-Host "建议把 npm 缓存改到 $npmCache（对本机所有 npm 生效，可用 npm config delete cache 恢复）。"
    $answer = Read-Host "修改 npm 缓存位置？[Y/n]"
    if (-not $answer -or $answer -match '^[Yy]') {
        & $npm config set cache $npmCache --location=user
        if ($LASTEXITCODE -ne 0) { Fail "无法修改 npm 缓存位置。" }
    }
}

Write-Host ""
Write-Host "正在安装 $package@$version 到 $npmPrefix ……"
& $npm install --global --prefix $npmPrefix "$package@$version"
if ($LASTEXITCODE -ne 0) { Fail "npm 安装 REA 失败，请检查网络或 npm 设置。" }

$rea = Join-Path $npmPrefix "rea.cmd"
if (-not (Test-Path $rea)) { Fail "npm 未生成 rea 命令。" }
$installedVersion = & $rea --version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { Fail "无法运行 rea。" }
Write-Host "REA $installedVersion 已安装到 $npmPrefix" -ForegroundColor Green

# 把 rea（以及便携版 Node.js）加入用户 PATH。直接读写注册表，保留 %VAR% 形式的条目不被展开
$pathDirs = @($npmPrefix)
if ($usePortable) { $pathDirs = @($nodeDir) + $pathDirs }
$envKey = Get-Item -Path "HKCU:\Environment"
$userPath = $envKey.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$kept = @("$userPath" -split ';' | Where-Object { $_ -and ($pathDirs -notcontains $_.TrimEnd('\')) })
Set-ItemProperty -Path "HKCU:\Environment" -Name "Path" -Value ((@($pathDirs) + $kept) -join ';') -Type ExpandString
# 通过 .NET 写一个变量，顺便通知资源管理器等程序重新读取环境变量
[Environment]::SetEnvironmentVariable("REA_INSTALL_DIR", $installDir, "User")
Write-Host ("已把 {0} 加入用户 PATH" -f ($pathDirs -join "、"))

if ($usePortable -and $systemNode -and -not (Test-NodeSupported $systemNodeVersion)) {
    Write-Host ""
    Write-Host "注意：系统中还装有旧版 Node.js $systemNodeVersion（$($systemNode.Source)）。" -ForegroundColor Yellow
    Write-Host "      它在系统 PATH 中时会优先于 REA 使用的新版本，建议卸载旧版或升级到 Node.js 24 LTS。" -ForegroundColor Yellow
}

Write-Host ""
if ($env:REA_NO_SETUP) {
    Write-Host "已跳过设置。之后在新终端运行 rea setup，把 REA 注册到 Claude Code、Cursor 等智能体。"
} else {
    Write-Host "启动 rea setup：选择要接入 REA 的智能体并确认变更……" -ForegroundColor Cyan
    & $rea setup
}

Write-Host ""
Write-Host "部署完成。安装目录：$installDir" -ForegroundColor Green
Write-Host "新打开的终端可直接使用 rea 命令；设置完成后请重启你的智能体（如果智能体提示找不到 npx，请注销并重新登录 Windows）。"
