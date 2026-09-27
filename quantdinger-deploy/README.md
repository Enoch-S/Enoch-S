# QuantDinger 一键部署（自动选择剩余空间最大的磁盘）

这些脚本会调用 [QuantDinger](https://github.com/OpenByteInc/QuantDinger) 官方安装程序，
并自动把安装目录放到你电脑上**剩余空间最大的本地磁盘**。

## 前置条件

- 安装 Docker（含 Compose v2）并确保已启动
  - Windows / macOS：[Docker Desktop](https://www.docker.com/products/docker-desktop/)
  - Linux：[Docker Engine](https://docs.docker.com/engine/install/)
- 能访问 GitHub 和 ghcr.io（国内网络可能需要代理或镜像，见官方
  [安装故障排查](https://github.com/OpenByteInc/QuantDinger/blob/main/docs/deployment/INSTALL_TROUBLESHOOTING.md)）

## Windows

1. 下载本目录的 `deploy-quantdinger.ps1` 和 `deploy-quantdinger.bat`，放在同一文件夹
2. 双击 `deploy-quantdinger.bat`
3. 按提示设置管理员账号和密码

安装位置：`<最大磁盘>:\QuantDinger`，例如 `D:\QuantDinger`。

## Linux / macOS

```bash
bash deploy-quantdinger.sh
```

安装位置：最大磁盘的挂载点下的 `quantdinger` 目录；如果最大磁盘就是系统盘/家目录所在盘，
则安装到 `~/quantdinger`。

## 安装完成后

- Web：<http://127.0.0.1:8888>
- 移动 H5：<http://127.0.0.1:8889>
- API 健康检查：<http://127.0.0.1:5000/api/health>

常用命令（在安装目录中执行）：

```bash
docker compose ps        # 查看状态
docker compose logs -f   # 查看日志
docker compose down      # 停止
docker compose up -d     # 启动
```

## 关于 Docker 数据的位置

安装目录只存放配置文件（compose 文件、`.env`、`backend.env`）。
**Docker 镜像和 PostgreSQL / Redis 数据由 Docker 自己保存**，默认在系统盘：

- Windows / macOS：Docker Desktop → Settings → Resources → Advanced → **Disk image location**，
  改到大磁盘（如 `D:\DockerData`）后 Apply & restart
- Linux：在 `/etc/docker/daemon.json` 写入 `{"data-root": "/大磁盘挂载点/docker"}`，
  然后 `sudo systemctl restart docker`

建议**先改 Docker 数据位置，再运行部署脚本**，这样全部数据都会落在最大的磁盘上。
