# REA 一键部署（自动选择剩余空间最大的磁盘）

这些脚本把 [REA](https://github.com/morluto/rea)（npm 包 `rea-agents`，让 AI 智能体逆向分析
二进制文件、应用程序和网站的 MCP 服务 + CLI）安装到你电脑上**剩余空间最大的本地磁盘**。

脚本会做这些事：

1. 找出剩余空间最大的本地磁盘，在上面建立安装目录
2. 检查 Node.js：REA 需要 22.x（≥22.19）、24.x（≥24.11）或 26+；没有或版本太旧时，
   自动下载 Node.js 24 LTS 便携版到安装目录（校验 SHA256，不影响系统里已有的 Node.js）
3. 询问是否把 npm 缓存也移到大磁盘（智能体是通过 `npx` 运行 REA 的，程序文件会下载到 npm 缓存）
4. 用 npm 把 `rea-agents` 安装到安装目录，并把 `rea` 命令加入 PATH
5. 启动 `rea setup`，让你选择要接入 REA 的智能体（Claude Code、Codex、Cursor、Gemini CLI 等）

安装目录结构：

```
<安装目录>/
├── node/         Node.js 24 便携版（仅在需要时安装）
├── npm-global/   REA 程序和 rea 命令
└── npm-cache/    npm 缓存，包括智能体通过 npx 启动的 REA
```

## Windows

1. 下载本目录的 `deploy-rea.ps1` 和 `deploy-rea.bat`，放在同一文件夹
2. 双击 `deploy-rea.bat`
3. 出现“修改 npm 缓存位置？”时直接回车（选 Y）
4. 在 `rea setup` 中选择你的智能体，确认变更
5. 重启智能体

安装位置：`<最大磁盘>:\REA`，例如 `D:\REA`。

在 PowerShell 里直接输入 `rea` 会报“因为在此系统上禁止运行脚本”，这是 Windows 默认的脚本限制。
请改用 `rea.cmd`（例如 `rea.cmd setup`），或在命令提示符（cmd）里使用 `rea`。
如果想在 PowerShell 里直接用 `rea`，可以运行一次
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`（允许本机脚本，只对当前用户生效）。

## Linux / macOS

```bash
bash deploy-rea.sh
```

安装位置：最大磁盘的挂载点下的 `rea` 目录；如果最大磁盘就是系统盘/家目录所在盘，
则安装到 `~/rea`。PATH 设置写在 `~/.bashrc` / `~/.zshrc` 中，打开新终端后生效。

## 国内网络

- Node.js 下载慢：先自行安装 Node.js 24 LTS（脚本会直接使用），或设置 `REA_NODE_MIRROR`
  指向与 `https://nodejs.org/dist` 目录结构相同的镜像，例如：

  ```powershell
  # Windows PowerShell
  $env:REA_NODE_MIRROR = "https://npmmirror.com/mirrors/node"
  .\deploy-rea.ps1
  ```

  ```bash
  # Linux / macOS
  REA_NODE_MIRROR=https://npmmirror.com/mirrors/node bash deploy-rea.sh
  ```

- npm 下载慢：部署前设置 `npm_config_registry` 环境变量（写法同上）；部署后执行
  `npm config set registry https://registry.npmmirror.com`，让智能体启动 REA 时也走镜像。

其他可选环境变量：`REA_VERSION`（指定版本，默认最新）、`REA_NO_SETUP=1`（只安装，不启动设置）。

## 安装完成后

在智能体里直接提问即可，例如：

```text
弄清 Notes 应用的搜索功能如何工作，展示证据，并为我的项目实现类似功能。
```

也可以在终端使用：

```bash
rea --help
rea doctor                                                   # 检查环境是否就绪
rea analyze-javascript-application <应用目录或 .asar> --json   # 分析 JS / Electron 应用
```

原生二进制分析需要 Hopper、Ghidra 或 IDA（见 REA 的
[安装文档](https://github.com/morluto/rea/blob/main/docs/installation.md)）。
自己安装 Ghidra / IDA 时，也建议装在同一块大磁盘上。

## 更新与卸载

```bash
rea update      # 更新安装目录里的 REA
rea setup       # 更新后重新运行，刷新智能体里的注册信息，然后重启智能体
```

卸载：

1. `rea uninstall --purge-data`（移除智能体注册、skill 和 `~/.rea` 数据）
2. 删除安装目录（如 `D:\REA` 或 `~/rea`）
3. `npm config delete cache`（恢复 npm 默认缓存位置）
4. 从 PATH 中去掉安装目录：Windows 在“编辑账户的环境变量”中删除；
   Linux / macOS 删除 `~/.bashrc` / `~/.zshrc` 中 `# >>> REA (deploy-rea.sh) >>>` 到
   `# <<< REA (deploy-rea.sh) <<<` 之间的内容

## 说明

- REA 自己的少量配置和状态固定保存在家目录的 `~/.rea`，分析时的临时文件在系统临时目录，
  这两处无法通过 REA 的设置改到别的磁盘。
- REA 仅用于合法的研究、教育、互操作性，以及分析你拥有或获准检查的软件，
  详见 [DISCLAIMER](https://github.com/morluto/rea/blob/main/DISCLAIMER.md)。
