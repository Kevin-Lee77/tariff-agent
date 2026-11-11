import React from "react";

export function RiskReport({ result, taskId }) {
  if (!result) return null;
  const text = result.original_text || "";
  const units = Array.from(text);
  let cursor = 0;
  const parts = [];
  for (const [i, row] of [...(result.annotations || [])]
    .sort((a, b) => a.start - b.start)
    .entries()) {
    if (row.start < cursor) continue;
    parts.push(units.slice(cursor, row.start).join(""));
    parts.push(
      <mark key={i} title={row.comment}>
        {units.slice(row.start, row.end).join("")}
      </mark>,
    );
    cursor = row.end;
  }
  parts.push(units.slice(cursor).join(""));
  return (
    <section className="report">
      <h3>
        税智源 ·{" "}
        {{
          clear: "通过当前规则校验",
          blocked: "退回企业整改",
          information_required: "待补充申报信息",
          review_required: "待人工核查",
        }[result.decision] || "服务不可用"}
      </h3>
      {result.synthetic && (
        <div className="notice compact">
          联调词典，仅验证流程，不代表正式海关风险结论。
        </div>
      )}
      <p>{result.reason}</p>
      <p className="description-card">{parts}</p>
      {(result.hits || []).map((hit, i) => (
        <div className="issue-row" key={i}>
          <b>{hit.vocabulary}</b>
          <span>{hit.review_comment || "请核实命中内容"}</span>
          <small>{hit.knowledge_id}</small>
        </div>
      ))}
      <p className="small-note">
        规则版本：{result.dictionary_version || "不可用"} ·{" "}
        {(result.unknown || []).length} 项无法比较
      </p>
      {taskId && (
        <a
          className="btn secondary"
          href={`/v1/cases/${taskId}/enterprise-feedback`}
          download
        >
          下载企业反馈报告
        </a>
      )}
    </section>
  );
}

export function LanguageReport({ result }) {
  if (!result) return null;
  return (
    <section className="report">
      <h3>税智语 · 申报文本归一化</h3>
      {result.synthetic && <div className="notice compact">联调术语词典</div>}
      <p>{result.normalized_text || result.reason || "未完成处理"}</p>
      {(result.changes || []).map((row, i) => (
        <p key={i}>
          {row.before} → <b>{row.after}</b>
        </p>
      ))}
      <p>
        保留否定及独立词：
        {(result.protected_spans || []).map((x) => x.text).join("；") || "无"}
      </p>
      {(result.warnings || []).map((row, i) => (
        <p key={i}>
          需核查：{row.text || row.terms?.join("、")}（{row.reason}）
        </p>
      ))}
      {result.status === "dictionary_unavailable" && (
        <p>术语词典未配置，未进行自动替换。</p>
      )}
      <p className="small-note">
        词典版本：{result.dictionary_version || "不可用"}
      </p>
    </section>
  );
}
