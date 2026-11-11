"""Render editable README SVGs with the standard library; contains no business records."""

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs/images"
OUT.mkdir(parents=True, exist_ok=True)


class Diagram:
    def __init__(self, title, subtitle, height):
        self.parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="{height}" viewBox="0 0 1440 {height}" role="img" aria-label="{escape(title)}">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#64748b" stroke-width="1.5"/></marker></defs>
<rect width="1440" height="{height}" rx="24" fill="#f3f6fb"/>
<path d="M24 0H1416Q1440 0 1440 24V132H0V24Q0 0 24 0" fill="#10243a"/>
<g font-family="Inter,Segoe UI,Microsoft YaHei,Noto Sans CJK SC,sans-serif">''']
        self.text(44, 39, "TARIFF AGENT / ENGINEERING OVERVIEW", 14, "#67e8d4", 600)
        self.text(44, 82, title, 32, "#ffffff", 700)
        self.text(44, 111, subtitle, 17, "#b8c9dc")

    def text(self, x, y, text, size=19, color="#42556e", weight=400):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(text)}</text>')

    def box(self, x, y, w, h, label, title, lines, accent="#0f766e"):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="white" stroke="#d9e3ef"/>')
        self.parts.append(f'<rect x="{x}" y="{y+17}" width="4" height="32" rx="2" fill="{accent}"/>')
        self.text(x+22, y+30, label, 13, accent, 700)
        self.text(x+22, y+61, title, 23, "#142c46", 700)
        for i, line in enumerate(lines):
            self.text(x+22, y+94+i*29, line, 18)

    def edge(self, path, dashed=False):
        self.parts.append(f'<path d="{path}" fill="none" stroke="#64748b" stroke-width="2.2" marker-end="url(#arrow)"'+(' stroke-dasharray="6 5"' if dashed else '')+'/>')

    def save(self, name):
        self.parts.append('</g></svg>')
        (OUT/name).write_text('\n'.join(self.parts), encoding='utf-8')


def architecture():
    d = Diagram("风险先行的申报治理与十位归类", "三个业务服务 · 可控 Agent · 专家协同 · 模型服务分离", 1160)
    d.edge('M452 226H514')
    d.edge('M920 226H982')
    d.edge('M1185 292V321H250V350')
    d.edge('M452 435H514')
    d.edge('M920 435H982')
    d.edge('M250 523V568')
    d.edge('M718 523V568')
    d.edge('M1185 523V568')
    d.box(48, 170, 404, 122, '01 / CONSOLE', 'React 业务工作台', ['申报 · 词典 · 任务 · 专家复核'])
    d.box(514, 170, 406, 122, '02 / API', 'FastAPI 业务接入', ['鉴权 / CSRF / Schema / 幂等键'])
    d.box(982, 170, 410, 122, '03 / EXECUTION', '持久任务 + 独立 Worker', ['租约 / 心跳 / 有限重试'])
    d.box(48, 350, 404, 173, 'GATE A / FLASK', '税智源 · 原文风险校验', ['适用税号 / 申报要素 / 风险词', 'Word2Vec / 原文位置 / 覆盖状态', '仅可判断且无风险 → 后续服务'])
    d.box(514, 350, 406, 173, 'GATE B / FLASK', '税智语 · 术语与规范治理', ['同义 / 规范词 / 否定 / 独立词', '互斥 / 歧义 / 字段与章节范围', '保留原文与编号 → 归类研判'])
    d.box(982, 350, 410, 173, 'LANGGRAPH / NINE NODES', '六阶段归类 Workflow', ['抽取 → 检索 → L1 → L2', '子目解析 → 证据终评', '合法路径 / 六、八、十位目标'])
    d.box(48, 568, 404, 173, 'STOP / DO NOT CLASSIFY', '风险阻断与企业反馈', ['命中：原文标注 + 整改报告', '缺项 / 未覆盖：补充或人工核查', '整改后提交新版本，重新校验'], '#b45309')
    d.box(514, 568, 406, 173, 'DICTIONARY LIFECYCLE', '词典维护与建议审核', ['未知词向量聚类 / 词簇合并', '规范 / 否定 / 独立 / 互斥词典', '修订 → 审核 → 版本发布'])
    d.box(982, 568, 410, 173, 'HUMAN IN THE LOOP', '专家确认与反馈回流', ['DeepSeek 疑难报告 / 缺项追问', '原生 interrupt / resume', '审核样本导出 / 经验显式发布'])
    d.text(48, 801, '基础设施与运行约束', 23, '#142c46', 700)
    d.box(48, 824, 404, 192, 'MODEL PLANE', '模型网关与 API', ['DeepSeek：规则 / 子目 / 疑难', 'Qwen 4B：可选抽取与初筛', 'Qwen 8B Embedding + Reranker', '跨机鉴权 TLS / 独立凭证'])
    d.box(514, 824, 406, 192, 'KNOWLEDGE & PERSISTENCE', '检索、数据与检查点', ['Qdrant：税则 + 历史案例', 'BM25 / 向量 / RRF / 精排', 'SQLite / PostgreSQL / Alembic', '原生检查点 / 任务与词典版本'])
    d.box(982, 824, 410, 192, 'QUALITY & OPERATIONS', '记忆、评测与可观测性', ['案件事实 / 审核经验 / TTL', '证据预算 / 卸载回读 / 轨迹', '开发 Badcase / 留出家族隔离', 'OpenTelemetry / 可选 OTLP'])
    d.text(48, 1070, '工程约束', 16, '#0f766e', 700)
    d.text(162, 1070, '风险未通过不归类  ·  未审核经验不共享  ·  私有业务数据不随代码发布', 18)
    d.text(48, 1110, 'PUBLIC SOURCE EDITION  /  架构示意，不包含原部署地址、账号或业务记录', 14, '#6b7e94')
    d.save('architecture.svg')


def workflow():
    d = Diagram("六阶段归类 + 有界工具探索", "固定业务顺序 · 局部只读探索 · 合法父子路径 · 专家确认", 1160)
    for x in [452, 920]:
        d.edge(f'M{x} 258H{x+62}')
    d.edge('M1185 346V378H250V410')
    for x in [452, 920]:
        d.edge(f'M{x} 498H{x+62}')
    d.edge('M1185 586V654')
    d.edge('M250 586V654')
    d.edge('M718 586V654')
    d.box(48, 176, 404, 170, 'STAGE 01 / ATTRIBUTES', '属性抽取 Agent', ['原文 + 规范化文本 + 术语提示', '材质 / 功能 / 用途 / 加工方式', 'Qwen 4B → 按需 DeepSeek 升级'])
    d.box(514, 176, 406, 170, 'STAGE 02 / RETRIEVAL', '候选检索 Agent', ['税则 + 历史案例双库', 'BM25 + Qwen 向量 → RRF', '排除评测家族 / 候选上限 40'])
    d.box(982, 176, 410, 170, 'STAGE 03 / SHORTLIST', 'L1 短名单与初筛', ['Reranker + 小模型初筛', '当前父节点与候选白名单', '非法输出有界纠正 / 上限 10'])
    d.box(48, 410, 404, 176, 'STAGE 04 / RULE REASONING', 'L2 章注与排他确认', ['类注 / 章注 / 排他规则', '局部 plan → read_tool 子图', '补充证据 / 最多 3 个候选'])
    d.box(514, 410, 406, 176, 'STAGE 05 / HIERARCHY', '子目路径解析', ['保留五 / 七 / 九位结构分组', '每步只允许合法子节点', '六 / 八 / 十位目标逐层完成'])
    d.box(982, 410, 410, 176, 'STAGE 06 / VERIFICATION', '终评与一致性校验', ['五次采样 / 引用原文验证', '编码合法性 / 路径 / 证据', '一致率只作信号，不当概率'])
    d.box(48, 654, 404, 178, 'BOUNDED EXPLORATION', '两轮预算与工具白名单', ['plan → read_tool → plan', '候选范围 / 只读 / 超时与去重', '证据不足可追问，不扩散工具'])
    d.box(514, 654, 406, 178, 'CONTEXT & RECOVERY', '上下文与持久执行', ['原始事实优先 / 整条证据筛选', '超额材料卸载 / 鉴权回读', '检查点 + 输入与知识版本'])
    d.box(982, 654, 410, 178, 'TEACHER & EXPERT', '疑难升级与人工协同', ['DeepSeek 结构化疑难分析', '缺项补充 / 专家确认与驳回', '审核后导出样本和发布经验'])
    d.parts.append('<rect x="48" y="884" width="1344" height="177" rx="16" fill="#e6f4f0"/>')
    d.text(74, 923, '一条案件的可追溯输出', 24, '#0f5e55', 700)
    d.text(74, 961, '原始输入 / 规范化版本 / 风险标注 / 属性 / 候选路径 / 引用 / 追问 / 专家决定', 21)
    d.text(74, 1000, '整体十位 Top1 / Top3 为主，六位辅助；四位 Recall 只描述候选检索。', 20)
    d.text(74, 1034, '阶段到达、待补充、无输出和失败均保留，不能只统计有输出样本。', 18)
    d.text(48, 1110, '六阶段 ≠ 六次调用  /  职责分工 ≠ 独立模型进程数量', 16, '#6b7e94')
    d.save('workflow.svg')


if __name__ == '__main__':
    architecture()
    workflow()
    print('Rendered two SVG architecture diagrams')
