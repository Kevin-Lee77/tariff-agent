import {
  LayoutDashboard,
  Files,
  GitBranch,
  BookOpen,
  ScanText,
  ShieldCheck,
  Activity,
  ListChecks,
  BrainCircuit,
} from "lucide-react";
const statusText = {
  queued: "排队中",
  running: "处理中",
  awaiting_information: "待补充",
  awaiting_review: "待复核",
  approved: "已确认",
  rejected: "已驳回",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已取消",
  risk_blocked: "风险退回",
  risk_review_required: "风险待核",
  language_review_required: "术语待核",
  awaiting_models: "待推理服务",
};
const kindText = {
  asset_batch: "业务数据批次",
  case: "商品归类",
  terms: "术语词典维护",
  risk: "匹配算法联调",
  declaration_risk: "税智源风险校验",
  declaration_language: "税智语文本处理",
};
const stages = [
  ["risk_precheck", "税智源校验"],
  ["normalize", "税智语处理"],
  ["attributes", "属性抽取"],
  ["retrieve", "候选检索"],
  ["l1", "品目初筛"],
  ["l2", "章注确认"],
  ["subheading", "子目解析"],
  ["verify", "证据终评"],
];
const menus = [
  ["evaluation", Activity, "评测中心"],
  ["memory", BrainCircuit, "Agent记忆"],
  ["assets", Files, "业务数据与词典"],
  ["overview", LayoutDashboard, "工作概览"],
  ["classification", GitBranch, "商品归类"],
  ["tasks", Files, "任务中心"],
  ["review", ShieldCheck, "专家复核"],
  ["terms", ScanText, "术语治理"],
  ["risk", ListChecks, "规范申报"],
  ["knowledge", BookOpen, "知识与来源"],
  ["system", Activity, "运行状态"],
];
const roleText = {
  admin: "系统管理员",
  operator: "业务操作员",
  reviewer: "归类专家",
};

export { statusText, kindText, stages, menus, roleText };
