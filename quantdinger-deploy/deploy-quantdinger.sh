#!/usr/bin/env bash
#
# 在 Linux / macOS 上把 QuantDinger 部署到剩余空间最大的磁盘。
#
# 用法：
#   bash deploy-quantdinger.sh
#
# 可选环境变量：
#   QUANTDINGER_INSTALL_REF=main   # 要安装的 QuantDinger 分支/标签

set -eu

REF="${QUANTDINGER_INSTALL_REF:-main}"

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

MOUNTS="$(list_mounts)"
if [ -z "$MOUNTS" ]; then
    echo "未找到可用的本地磁盘。" >&2
    exit 1
fi

echo "检测到的磁盘（按剩余空间排序）："
printf '%s\n' "$MOUNTS" | awk -F '\t' '{ printf "  %-30s 剩余 %8.1f GB  (%s)\n", $3, $1/1024/1024, $2 }'

TARGET_MOUNT="$(printf '%s\n' "$MOUNTS" | head -n 1 | cut -f3)"

# 最大的磁盘就是家目录所在磁盘时，直接装到家目录下，避免需要 root 权限
HOME_MOUNT="$(df -P "$HOME" | awk 'NR == 2 { mp = $6; for (i = 7; i <= NF; i++) mp = mp " " $i; print mp }')"
if [ "$TARGET_MOUNT" = "$HOME_MOUNT" ] || [ "$TARGET_MOUNT" = "/" ]; then
    INSTALL_DIR="$HOME/quantdinger"
else
    INSTALL_DIR="${TARGET_MOUNT%/}/quantdinger"
fi

echo
echo "将安装到剩余空间最大的磁盘 ${TARGET_MOUNT}：${INSTALL_DIR}"

if ! command -v docker >/dev/null 2>&1; then
    echo "未检测到 Docker。请先安装 Docker（含 Compose v2）：https://docs.docker.com/get-docker/" >&2
    exit 1
fi

if ! mkdir -p "$INSTALL_DIR" 2>/dev/null; then
    echo "需要管理员权限在 ${TARGET_MOUNT} 下创建目录……"
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR"
fi

if [ "$TARGET_MOUNT" != "$HOME_MOUNT" ] && [ "$TARGET_MOUNT" != "/" ]; then
    DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
    echo
    echo "提示：Docker 镜像和数据库数据保存在 Docker 数据目录 ${DOCKER_ROOT:-（未知）}，不在安装目录内。"
    echo "      如需一并放到 ${TARGET_MOUNT}，Linux 可在 /etc/docker/daemon.json 设置"
    echo "      {\"data-root\": \"${TARGET_MOUNT%/}/docker\"} 后重启 Docker；macOS 在 Docker Desktop 设置里修改 Disk image location。"
fi

TMP_SCRIPT="$(mktemp)"
trap 'rm -f "$TMP_SCRIPT"' EXIT
curl -fsSL "https://raw.githubusercontent.com/OpenByteInc/QuantDinger/${REF}/install.sh" -o "$TMP_SCRIPT"

# 调用 QuantDinger 官方安装程序（会询问管理员账号密码、生成密钥并启动服务）
QUANTDINGER_INSTALL_DIR="$INSTALL_DIR" QUANTDINGER_INSTALL_REF="$REF" bash "$TMP_SCRIPT" "$INSTALL_DIR"
