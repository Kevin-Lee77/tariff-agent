# 自托管观测配置

上游固定为Langfuse v4.76.0，提交及SHA256见`upstream.json`。`upstream.compose.yaml`保留上游来源；生成器删去不需要的AI网关，生成独立的Web/Worker/PostgreSQL/Redis/ClickHouse/MinIO六服务配置，不复用业务数据库。

应用侧OTLP鉴权、会话/实验属性和本地评测已实现，HTTP收集器传输有自动测试。观测控制台需要自行部署启动，测试收集器不等同Langfuse实例。

从项目根目录准备配置，不会启动容器或修改业务`.env`：

```bash
.venv/bin/python scripts/prepare_langfuse.py
```

随机密钥、独立账号及Compose文件写入`data/private/langfuse/`，文件权限0600，不打印密码，不覆盖已有文件。应用原账号与Langfuse账号分开。

在有Docker Compose权限的目标主机、同一项目目录执行：

```bash
docker compose --env-file data/private/langfuse/.env \
  -f data/private/langfuse/compose.generated.yaml config --quiet
docker compose --env-file data/private/langfuse/.env \
  -f data/private/langfuse/compose.generated.yaml up -d
docker compose --env-file data/private/langfuse/.env \
  -f data/private/langfuse/compose.generated.yaml ps
```

Web只监听目标主机`127.0.0.1:18090`，对象访问端口只监听`127.0.0.1:18091`，其余依赖不映射宿主端口。通过SSH转发访问；远程HTTP入口和TLS需要按实际部署地址配置。各镜像以本次固定上游配置为基础，应用Web/Worker固定4.76.0，基础镜像仍需在正式环境拉取后记录digest；当前未拉取，不声称镜像供应链已验收。

服务健康后，在业务主机配置生成的`agent-otel.env`两项内容。不同主机需先建立受控通道并改成可达地址；不要把密钥贴到聊天或提交Git。重启API、Worker和两个Flask后，再检查Langfuse是否收到Span。

应用导出不含申报原文或完整Prompt。评测使用当前`eval_runner`；配置OTLP后，按官方v4实验属性发送实验ID、数据集指纹、样本ID和编码结果。完整评测、标签及Badcase仍保存在项目私有目录。以官方OTLP/实验接入约定实现，未使用已弃用的dataset-run-items写接口。

官方资料：[自托管部署](https://langfuse.com/self-hosting/deployment/docker-compose)、[OTLP](https://langfuse.com/integrations/native/opentelemetry)、[实验属性](https://langfuse.com/integrations/native/opentelemetry/experiments)。
