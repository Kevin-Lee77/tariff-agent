# 来源与第三方组件

本仓库提供商品归类与申报治理的脱敏工程。真实客户系统、私有词典、材料和测试样本不在发布范围。

设计参考 [HSCodeComp 论文](https://arxiv.org/abs/2605.14857)和 [Analytics-Everywhere-Lab/hts](https://github.com/Analytics-Everywhere-Lab/hts) 的层级规则、检索和分阶段研判思路；本工程围绕风险前置、术语治理、十位目录、专家协同、恢复和接口组织实现，不将参考项目的性能写成本仓库成绩。

LangGraph、FastMCP、FastAPI、Flask、React、Qdrant、SQLAlchemy、OpenTelemetry 和 Qwen 模型遵循各自许可证及使用条款。

`deploy/langfuse/upstream.compose.yaml` 来自固定版本 [Langfuse](https://github.com/langfuse/langfuse)，提交与校验记录见同目录 `upstream.json`。其中上游默认值不是项目凭证；生成器为实际实例创建随机密钥。该文件使用遵循上游许可证和说明。
