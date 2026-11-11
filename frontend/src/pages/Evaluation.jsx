import React, { useEffect, useState } from "react";
import { api } from "../api";

const categoryNames = {
  retrieval_miss: "召回遗漏",
  low_rank_heading: "排序靠后（10名之后）",
  rerank_dropped_heading: "精排丢失",
  fusion_dropped_heading: "融合丢失",
  execution_failure: "执行失败",
  shortlist_lost_heading: "L1初筛丢失",
  ranked_lost_heading: "L2判定丢失",
  resolved_lost_heading: "子目路径丢失",
  clarification_required: "需要澄清",
  subheading_mismatch: "子目不一致",
};

export function Evaluation({ notify }) {
  const [data, setData] = useState(null);
  const [bank, setBank] = useState(null);
  useEffect(() => {
    let alive = true;
    api("/v1/evaluations/status")
      .then((r) => {
        if (alive) setData(r);
      })
      .catch((e) => notify(e.message, true));
    return () => {
      alive = false;
    };
  }, []);
  if (!data)
    return (
      <section className="panel form-panel">正在读取索引与评测状态…</section>
    );
  const counts = data.dataset?.counts || {};
  return (
    <>
      <section className="panel form-panel">
        <div className="section-heading">
          <div>
            <h2>数据隔离与实验</h2>
            <p>历史案例仅作经验参考；固定留出数据与同组样本不进入案例索引。</p>
          </div>
        </div>
        <div className="word-tags">
          {[
            [data.indexes.history?.points ?? counts.history, "历史案例索引"],
            [counts.dev, "固定开发集"],
            [counts.heldout, "固定留出集"],
            [data.indexes.history?.exclusion_groups, "隔离评测家族"],
          ].map(([value, title]) => (
            <span key={title}>
              {title} <b>{value?.toLocaleString() ?? "未准备"}</b>
            </span>
          ))}
        </div>
        <p>
          分组交叉：{data.dataset?.group_overlap ?? "未检查"} ·
          标签来源：历史申报，待专家复核。
        </p>
        <p className="small-note">
          评测同时检查结果、执行轨迹、引用、风险门禁及调用量。检索组件与完整Agent实验分别报告。
        </p>
        {data.warehouse && (
          <p>
            历史源数据{" "}
            {data.warehouse.warehouse_counts.raw_rows.toLocaleString()} 条 ·
            去重文本{" "}
            {data.warehouse.warehouse_counts.unique_texts.toLocaleString()} 条 ·
            本期代表案例 {data.warehouse.counts.selected.toLocaleString()} 条。
            全量语料保留，案例按频次、编码覆盖和模板去重选取。
          </p>
        )}
      </section>
      <section className="panel form-panel">
        <h2>Qdrant 检索索引</h2>
        <div className="service-grid">
          {Object.entries(data.indexes).map(([name, index]) => (
            <div className="service-card" key={name}>
              <div>
                <b>{name === "history" ? "历史案例库" : "税则品目库"}</b>
                <p>
                  {index.points?.toLocaleString() || 0} 条 ·{" "}
                  {index.connected ? "独立服务已连接" : "服务待就绪"}
                </p>
                <p>BM25稀疏索引：{index.sparse_ready ? "就绪" : "待构建"}</p>
                <p>
                  Qwen 8B向量：
                  {index.dense_ready
                    ? "全量就绪"
                    : index.dense_search_ready
                      ? `代表案例 ${index.dense_count.toLocaleString()} 条已向量化`
                      : name === "history" && !data.history_dense_enabled
                        ? "当前采用稀疏案例召回，未全量向量化"
                        : "待推理服务及建库"}
                </p>
              </div>
            </div>
          ))}
        </div>
      </section>
      <section className="panel form-panel">
        <h2>Badcase 回归</h2>
        <p>
          从开发集沉淀召回遗漏、精排丢失、路径错误与执行失败；专家确认前不进入训练。
        </p>
        {(data.badcase_banks || []).map((b) => (
          <article className="report" key={b.name}>
            <h3>
              {b.name} · {b.n} 条
            </h3>
            <p>
              {Object.entries(b.categories)
                .map(([k, v]) => `${categoryNames[k] || k}: ${v}`)
                .join(" · ")}
            </p>
            <button
              className="text-btn"
              onClick={() =>
                api(`/v1/evaluations/badcases/${b.name}`)
                  .then(setBank)
                  .catch((e) => notify(e.message, true))
              }
            >
              查看前20条
            </button>
          </article>
        ))}
        {(data.badcase_regressions || []).map((r) => (
          <article className="report" key={r.candidate + r.seed}>
            <h3>{r.candidate} 回归</h3>
            <p>
              基准错例 {r.bank_size} 条 · 已解决 {r.counts.resolved || 0} ·
              未解决 {r.counts.unresolved || 0} · 未覆盖 {r.counts.missing || 0}{" "}
              · 新增退化 {r.new_failure_ids.length}
            </p>
            <p>
              {r.all_seed_cases_covered
                ? "完整开发集已覆盖"
                : "开发集未完整覆盖"}
            </p>
          </article>
        ))}
        {bank &&
          bank.cases.map((c) => (
            <article className="report" key={c.id}>
              <p>{c.description}</p>
              <p>
                {categoryNames[c.category] || c.category} · 历史参考编码{" "}
                {c.reference_code} · 待专家复核
              </p>
            </article>
          ))}
      </section>
      {!!data.component_evaluations?.length && <section className="panel form-panel">
        <h2>税智源 / 税智语专项指标</h2>
        <p className="small-note">基于固定合成规则及词典派生样本；不等同专家语义标注或线上业务准确率。</p>
        {data.component_evaluations.map((report,index)=><article className="report" key={index}>
          <h3>{index===0?'编号保护修复前':'编号保护修复后'}</h3>
          <p>税智源：{report.source.n} 条；规则决策匹配 {report.source.decision_match.hits}/{report.source.decision_match.n}；风险标记F1 {(report.source.risk_detection_in_covered_complete_cases.f1*100).toFixed(2)}%（{report.source.risk_detection_in_covered_complete_cases.n} 条正负样本）</p>
          <p>原文风险位置标注：TP {report.source.original_annotation_exact_span.tp} · FP {report.source.original_annotation_exact_span.fp} · FN {report.source.original_annotation_exact_span.fn}</p>
          <p>税智语：全文规范化一致 {report.language.exact_text_match.hits}/{report.language.exact_text_match.n}（{(report.language.exact_text_match.value*100).toFixed(2)}%）；保护片段保留 {report.language.protected_text_preserved.hits}/{report.language.protected_text_preserved.n}</p>
          {report.language.identifier_value_preserved && <p>编号字段保持不变：{report.language.identifier_value_preserved.hits}/{report.language.identifier_value_preserved.n}</p>}
        </article>)}
      </section>}
      <section className="panel form-panel">
        <h2>实验结果</h2>
        {!data.experiments.length && <p>尚无已完成实验，不展示模拟指标。</p>}
        {data.experiments.map((run) => (
          <article className="report" key={run.name}>
            <h3>{run.name}</h3>
            <p>
              {run.n_completed?.toLocaleString()} 条 ·{" "}
              {{
                retrieval: "检索组件",
                governance: "申报治理回归",
                agent: "归类组件",
              }[run.suite] || run.suite}{" "}
              · {run.variant}
            </p>
            {run.name.startsWith("smoke") && (
              <p className="small-note">少量链路测试，不作为总体效果结论。</p>
            )}
            {run.pilot && <p className="small-note">固定开发集的先导子集；完整Agent开发集及留出集尚未完成，未评测前置治理。</p>}
            <div className="word-tags">
              {run.suite==='retrieval' && run.metrics?.["heading_recall@40"] && (
                <span>
                  品目 Recall@40{" "}
                  <b>
                    {(run.metrics["heading_recall@40"].value * 100).toFixed(2)}%
                    {` (${run.metrics['heading_recall@40'].hits}/${run.metrics['heading_recall@40'].n})`}
                  </b>
                </span>
              )}
              {run.metrics?.trajectory_pass_rate != null && (
                <span>
                  轨迹通过率{" "}
                  <b>{(run.metrics.trajectory_pass_rate * 100).toFixed(2)}%</b>
                </span>
              )}
              {run.task_success_rate != null && (
                <span>
                  治理场景通过率{" "}
                  <b>{(run.task_success_rate * 100).toFixed(2)}%</b>
                </span>
              )}
              <span>
                失败 <b>{run.metrics?.failures ?? 0}</b>
              </span>
              <span>
                生成请求 <b>{run.metrics?.model_requests ?? 0}</b>
              </span>
              {run.metrics?.top1_10digit && <span>十位Top1标签一致 <b>{run.metrics.top1_10digit.hits}/{run.metrics.top1_10digit.n}</b></span>}
              {run.metrics?.top1_6digit && <span>六位Top1标签一致 <b>{run.metrics.top1_6digit.hits}/{run.metrics.top1_6digit.n}</b></span>}
            </div>
            {run.stage_metrics && <>
              <p>待补充 {run.stage_metrics.statuses.awaiting_information || 0} 条 · 待复核 {run.stage_metrics.statuses.awaiting_review || 0} 条</p>
              {Object.entries(run.stage_metrics.stages).filter(([key])=>key!=='attributes').map(([key,value])=><p className="small-note" key={key}>
                {({retrieved:'候选检索',shortlist:'L1短名单',ranked:'L2候选',resolved:'子目解析'})[key]}：参考品目保留 {value.reference_heading_retention_all_cases.hits}/{value.reference_heading_retention_all_cases.n}（全体）；{value.reference_heading_retention_reached_cases.hits}/{value.reference_heading_retention_reached_cases.n}（已到达）
              </p>)}
            </>}
            <p className="small-note">
              {run.label_status === "synthetic_rule_oracle"
                ? "合成规则回归，验证流程契约。"
                : "历史标签一致性评估，不代表专家确认的业务准确率。"}
            </p>
            <a
              className="text-btn"
              href={`/v1/evaluations/experiments/${run.name}/summary`}
              download
            >
              下载实验报告
            </a>
          </article>
        ))}
      </section>
    </>
  );
}
