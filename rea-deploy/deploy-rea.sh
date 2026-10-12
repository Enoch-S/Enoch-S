#!/usr/bin/env bash
#
# 在 Linux / macOS 上把 REA（https://github.com/morluto/rea）部署到剩余空间最大的磁盘。
#
# 用法：
#   bash deploy-rea.sh
#
# 可选环境变量：
#   REA_VERSION=6.4.0                                    # 要安装的 REA 版本，默认最新版
#   REA_NODE_MIRROR=https://npmmirror.com/mirrors/node   # Node.js 下载镜像（国内网络可用）
#   REA_NO_SETUP=1                                       # 只安装，不启动 rea setup

set -eu

PACKAGE="rea-agents"
VERSION="${REA_VERSION:-latest}"
NODE_MIRROR="${REA_NODE_MIRROR:-https://nodejs.org/dist}"
NODE_MIRROR="${NODE_MIRROR%/}"

fail() {
    echo "部署失败：$1" >&2
    exit 1
}

case "$(uname -s)" in
    Linux) NODE_OS=linux ;;
    Darwin) NODE_OS=darwin ;;
    *) fail "REA 只支持 Linux 和 macOS，Windows 请使用 deploy-rea.bat。" ;;
esac
case "$(uname -m)" in
    x86_64|amd64) NODE_ARCH=x64 ;;
    aarch64|arm64) NODE_ARCH=arm64 ;;
    *) NODE_ARCH="" ;;
esac
command -v curl >/dev/null 2>&1 || fail "需要 curl，请先安装 curl。"

# 列出真实磁盘挂载点及可用空间（KB），排除内存盘、只读镜像等虚拟文件系统
list_mounts() {
    df -P -k 2>/dev/null | awk 'NR > 1 {
        mp = $6; for (i = 7; i <= NF; i++) mp = mp " " $i
        if ($1 !~ /^\/dev\//) next                          # 只保留真实块设备
        if (mp ~ /^\/(boot|snap|dev|run|sys|proc)(\/|$)/) next
        if (mp ~ /^\/System\/Volumes\/(VM|Preboot|Update|xarts|iSCPreboot|Hardware)$/) next
        print $4 "\t" $1 "\t" mp
    }' | sort -t "$(printf '\t')" -k1,1nr
}

# 输出某个路径（可以尚不存在）所在磁盘的挂载点
mount_of() {
    p="$1"
    while [ ! -e "$p" ]; do p="$(dirname "$p")"; done
    df -P "$p" | awk 'NR == 2 { mp = $6; for (i = 7; i <= NF; i++) mp = mp " " $i; print mp }'
}

MOUNTS="$(list_mounts)"
[ -n "$MOUNTS" ] || fail "未找到可用的本地磁盘。"

echo "检测到的磁盘（按剩余空间排序）："
printf '%s\n' "$MOUNTS" | awk -F '\t' '{ printf "  %-30s 剩余 %8.1f GB  (%s)\n", $3, $1/1024/1024, $2 }'

TARGET_MOUNT="$(printf '%s\n' "$MOUNTS" | head -n 1 | cut -f3)"

# 最大的磁盘就是家目录所在磁盘时，直接装到家目录下，避免需要 root 权限
HOME_MOUNT="$(mount_of "$HOME")"
if [ "$TARGET_MOUNT" = "$HOME_MOUNT" ] || [ "$TARGET_MOUNT" = "/" ]; then
    INSTALL_DIR="$HOME/rea"
else
    INSTALL_DIR="${TARGET_MOUNT%/}/rea"
fi
NODE_DIR="$INSTALL_DIR/node"
NPM_PREFIX="$INSTALL_DIR/npm-global"
NPM_CACHE="$INSTALL_DIR/npm-cache"

echo
echo "将安装到剩余空间最大的磁盘 ${TARGET_MOUNT}：${INSTALL_DIR}"

if ! mkdir -p "$INSTALL_DIR" 2>/dev/null; then
    echo "需要管理员权限在 ${TARGET_MOUNT} 下创建目录……"
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR"
fi

# REA 需要 Node.js 22.x (>=22.19)、24.x (>=24.11) 或 26+
node_supported() {
    v="$("$1" -p 'process.versions.node' 2>/dev/null)" || return 1
    major="${v%%.*}"
    rest="${v#*.}"
    minor="${rest%%.*}"
    case "$major" in ''|*[!0-9]*) return 1 ;; esac
    case "$minor" in ''|*[!0-9]*) return 1 ;; esac
    [ "$major" -eq 22 ] && [ "$minor" -ge 19 ] && return 0
    [ "$major" -eq 24 ] && [ "$minor" -ge 11 ] && return 0
    [ "$major" -ge 26 ]
}

# 下载 Node.js 24 LTS 便携版到安装目录，不影响系统里已有的 Node.js
install_portable_node() {
    [ -n "$NODE_ARCH" ] || fail "不支持的 CPU 架构 $(uname -m)，请手动安装 Node.js 24 后重试。"
    work="$(mktemp -d "$INSTALL_DIR/.download.XXXXXX")"
    trap 'rm -rf "$work"' EXIT

    curl -fsSL "$NODE_MIRROR/latest-v24.x/SHASUMS256.txt" -o "$work/SHASUMS256.txt" ||
        fail "无法从 $NODE_MIRROR 获取 Node.js 版本信息，可设置 REA_NODE_MIRROR 使用镜像。"
    line="$(grep -E "  node-v[0-9.]+-${NODE_OS}-${NODE_ARCH}\.tar\.gz\$" "$work/SHASUMS256.txt" | head -n 1 || true)"
    [ -n "$line" ] || fail "未找到适用于 ${NODE_OS}-${NODE_ARCH} 的 Node.js。"
    sum="${line%% *}"
    file="${line##* }"

    echo "正在下载 ${file} ……"
    curl -fL --progress-bar "$NODE_MIRROR/latest-v24.x/$file" -o "$work/$file" || fail "Node.js 下载失败。"
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$work/$file" | cut -d ' ' -f1)"
    else
        actual="$(shasum -a 256 "$work/$file" | cut -d ' ' -f1)"
    fi
    [ "$actual" = "$sum" ] || fail "Node.js 安装包校验失败，请重试。"

    tar -xzf "$work/$file" -C "$work"
    rm -rf "$NODE_DIR"
    mv "$work/${file%.tar.gz}" "$NODE_DIR"
    rm -rf "$work"
    trap - EXIT
}

PORTABLE_NODE=false
if [ -x "$NODE_DIR/bin/node" ] && node_supported "$NODE_DIR/bin/node"; then
    PORTABLE_NODE=true
elif command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && node_supported "$(command -v node)"; then
    echo "使用已安装的 Node.js $(node -p 'process.versions.node')"
else
    if command -v node >/dev/null 2>&1; then
        echo "已安装的 Node.js $(node -p 'process.versions.node' 2>/dev/null || echo '?') 版本不满足 REA 要求（22.19+、24.11+ 或 26+）。"
    fi
    echo "将在 ${NODE_DIR} 安装 Node.js 24 LTS 便携版。"
    install_portable_node
    PORTABLE_NODE=true
fi

if [ "$PORTABLE_NODE" = true ]; then
    export PATH="$NODE_DIR/bin:$PATH"
    echo "使用 Node.js $(node -p 'process.versions.node')（${NODE_DIR}）"
fi
export PATH="$NPM_PREFIX/bin:$PATH"

# 智能体通过 npx 启动 REA 的 MCP 服务，程序会下载到 npm 缓存；把缓存也放到大磁盘
# （路径里含有类似令牌的字符串时 npm 会拒绝显示，此时当作未知处理）
CURRENT_CACHE="$(npm config get cache 2>/dev/null || true)"
if [ "$CURRENT_CACHE" != "$NPM_CACHE" ] &&
    { [ -z "$CURRENT_CACHE" ] || [ "$(mount_of "$CURRENT_CACHE")" != "$TARGET_MOUNT" ]; }; then
    echo
    echo "智能体会通过 npx 运行 REA，相关文件下载到 npm 缓存（当前：${CURRENT_CACHE:-未知}）。"
    echo "建议把 npm 缓存改到 ${NPM_CACHE}（对本机所有 npm 生效，可用 npm config delete cache 恢复）。"
    answer=""
    if [ -t 0 ]; then
        read -r -p "修改 npm 缓存位置？[Y/n] " answer || answer=""
    fi
    case "$answer" in
        ''|[Yy]*) npm config set cache "$NPM_CACHE" --location=user ;;
    esac
fi

echo
echo "正在安装 ${PACKAGE}@${VERSION} 到 ${NPM_PREFIX} ……"
npm install --global --prefix "$NPM_PREFIX" "$PACKAGE@$VERSION" || fail "npm 安装 REA 失败，请检查网络或 npm 设置。"
REA_BIN="$NPM_PREFIX/bin/rea"
[ -x "$REA_BIN" ] || fail "npm 未生成 rea 命令。"
INSTALLED_VERSION="$("$REA_BIN" --version 2>/dev/null | tr -d '[:space:]')" || fail "无法运行 rea。"
echo "REA ${INSTALLED_VERSION} 已安装到 ${NPM_PREFIX}"

# 把 rea（以及便携版 Node.js）加入新终端的 PATH
MARK_BEGIN="# >>> REA (deploy-rea.sh) >>>"
MARK_END="# <<< REA (deploy-rea.sh) <<<"
PATH_DIRS="$NPM_PREFIX/bin"
if [ "$PORTABLE_NODE" = true ]; then
    PATH_DIRS="$NODE_DIR/bin:$PATH_DIRS"
fi
PATH_LINE="export PATH=\"$PATH_DIRS:\$PATH\""

update_rc() {
    rc="$1"
    tmp_rc="$(mktemp)"
    if [ -f "$rc" ]; then
        awk -v b="$MARK_BEGIN" -v e="$MARK_END" '$0 == b { skip = 1 } !skip { print } $0 == e { skip = 0 }' "$rc" > "$tmp_rc"
    fi
    printf '%s\n%s\n%s\n' "$MARK_BEGIN" "$PATH_LINE" "$MARK_END" >> "$tmp_rc"
    cat "$tmp_rc" > "$rc"
    rm -f "$tmp_rc"
    echo "已在 ${rc} 中加入 PATH 设置"
}

RC_UPDATED=false
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$rc" ]; then
        update_rc "$rc"
        RC_UPDATED=true
    fi
done
if [ "$RC_UPDATED" = false ]; then
    case "$(basename "${SHELL:-bash}")" in
        zsh) update_rc "$HOME/.zshrc" ;;
        *) update_rc "$HOME/.bashrc" ;;
    esac
fi

echo
if [ -n "${REA_NO_SETUP:-}" ]; then
    echo "已跳过设置。之后运行 rea setup 把 REA 注册到 Claude Code、Cursor 等智能体。"
elif [ -t 0 ] && [ -t 1 ]; then
    echo "启动 rea setup：选择要接入 REA 的智能体并确认变更……"
    "$REA_BIN" setup
else
    echo "运行 ${REA_BIN} setup 把 REA 注册到 Claude Code、Cursor 等智能体。"
fi

echo
echo "部署完成。安装目录：${INSTALL_DIR}"
echo "打开新终端后可直接使用 rea 命令；设置完成后请重启你的智能体。"
