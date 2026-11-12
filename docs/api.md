# 接口与集成

默认业务入口 `http://127.0.0.1:18000`，运行后的 `/docs` 是完整 OpenAPI 契约。浏览器使用会话，写操作提交 CSRF；服务使用独立 API Key，不向前端暴露模型凭证。

## 业务任务

- `POST /auth/login`、`GET /auth/me`：会话和角色。
- `POST /v1/cases`：归类任务，返回 202，支持 `Idempotency-Key`。
- `GET /v1/tasks/{task_id}`、`/events`、`/stream`：状态、轨迹和事件流。
- `POST /v1/cases/{task_id}/clarifications`：补充后重新进入业务流程。
- `POST /v1/cases/{task_id}/review`：审核人员确认/驳回。
- `GET /v1/cases/{task_id}/enterprise-feedback`：风险反馈。
- `GET /v1/feedback/export`：受权限保护的专家确认样本导出。

请求包含 `description`、可选 `declared_code`、`elements`、`target_digits` 与 `governance_profile`。正式与 sandbox 分开；合成数据不能通过更改一个字段变成正式审核数据。

状态区分排队、执行、风险阻断、风险待核、术语待核、补充信息、模型未就绪、专家待核、通过、驳回、失败与取消。调用方不能把待核文本当作正式归类结论。

## 两个 Flask 接口

税智源 `127.0.0.1:18021`：`POST /v1/declarations/check`。

税智语 `127.0.0.1:18022`：`POST /v1/declarations/normalize`。

二者均使用 `X-API-Key` 和同一申报 Schema：

```json
{
  "description": "自行提供的申报原文",
  "declared_code": null,
  "elements": [],
  "target_digits": 10
}
```

上面只说明字段，不是业务样本。税智源返回风险决策及原文标注；税智语返回规范化文本、变化与提示。Workflow 自动保证先风险后术语；独立调用时由上游选择时机。

## 其他能力

- `/v1/assets/*`：申报数据、词典、修订和发布。
- `/v1/cases/{task_id}/memory`：案件上下文及人工状态。
- `/v1/evaluations/status`：索引、固定划分、实验和 Badcase。
- `/v1/evaluations/experiments/{name}/summary`：实验汇总。
- `/health/live`、`/health/ready`：存活和业务就绪。

FastMCP 入口为 `tariff_agent.mcp_server`，不得借工具接入绕过规则和来源检查。`legacy.py` 与集成路由保留旧 Java 接口适配，旧系统及私有源码不随仓库发布。

新增的`/v1/business/*`提供文件导入/导出批次、逐行报告，以及词典批量调整、祖先回退与撤销发布；详见[业务管理与接口](business-workflows.md)。
