# 部署与运行

业务主机运行 FastAPI、Worker、两个 Flask 和 Qdrant；GPU 模型可独立部署，通过鉴权 HTTPS 接入。DeepSeek 与 GPU 网关分别使用凭证。示例绑定回环地址，域名、证书、防火墙需按目标环境配置。

## 原生部署

按 README 安装后运行 `tariff init-config`，生成器创建随机服务密钥并拒绝覆盖 `.env`。配置授权数据、模型和数据库，执行 Alembic 与账号创建，再启动：

```bash
mkdir -p run logs
supervisord -c deploy/supervisord.conf
supervisorctl -c deploy/supervisord.conf status
```

端口：API 18000，税智源 18021，税智语 18022，Qdrant 18033。可运行 `scripts/install_qdrant.py` 下载二进制并生成私有配置。不要把生成配置、业务库或数据提交 Git。

## 模型准备

```bash
python scripts/download_assets.py
python scripts/download_small_model.py --revision <selected-model-commit>
python -m tariff_agent.gpu_gateway --root . --host 127.0.0.1 --port 18081
```

下载与启动分开执行。启动前按 `gpu_gateway.py` 契约准备私有模型、证书和访问密钥，量化及显存预算需在目标 GPU 验证。跨机配置入口为 `scripts/configure_gpu_gateway.py`，传入自己的 HTTPS 地址、网关访问密钥文件和服务 CA；不同服务凭证不混用。

## PostgreSQL 与 Compose

Compose 使用 Linux host network。私有 `.env` 设置 `TARIFF_PG_PASSWORD`、`TARIFF_QDRANT_API_KEY`、模型与授权数据路径，然后执行：

```bash
docker compose --env-file .env config --quiet
docker compose build
docker compose up -d
```

PostgreSQL 健康检查和 migrate 控制启动顺序。API、Worker 与两个 Flask 均连接同一业务 PostgreSQL，Qdrant 数据与快照单独管理。容器 UID 10001 需具备数据目录权限。Compose 与 Supervisor 不在同一端口同时启动；镜像构建、硬件和网络仍需在目标环境验收。

## 观测与检查

Trace 默认本地存储，OTLP 需显式配置。Langfuse 生成器见 `scripts/prepare_langfuse.py` 和 [说明](../deploy/langfuse/README.md)，不会自动启动容器或外发跟踪。

运行前核对存活、业务就绪、字典版本、风险阻断、专家权限和恢复。备份业务库、检查点和 Qdrant 保持版本一致；私有备份和日志不提交。
