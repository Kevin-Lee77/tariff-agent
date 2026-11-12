import React, { useEffect, useState, useRef } from "react";
import { X, Check, ArrowRight, CircleAlert, Plus } from "lucide-react";
import { api, post } from "../api";
import { Status, Empty, ElementEditor, EvidenceSource } from "./ui";
import { stages, kindText } from "../domain";
import { RiskReport, LanguageReport } from "./DeclarationReport";
import { CaseMemory } from "../pages/Memory";

function TaskDetail({ taskId, actor, onClose, onSelect, notify, refresh }) {
  const activeTask = useRef(taskId);
  activeTask.current = taskId;
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const close = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", close);
    return () => {
      activeTask.current = null;
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", close);
    };
  }, []);
  const [task, setTask] = useState(null),
    [events, setEvents] = useState([]),
    [tab, setTab] = useState("result"),
    [code, setCode] = useState(""),
    [reason, setReason] = useState(""),
    [clarifications, setClarifications] = useState([
      { name: "", value: "", code: "" },
    ]);
  const reviewer = ["admin", "reviewer"].includes(actor.role),
    writer = ["admin", "operator"].includes(actor.role);
  const load = async () => {
    const [row, trace] = await Promise.all([
      api("/v1/tasks/" + taskId),
      api("/v1/tasks/" + taskId + "/events"),
    ]);
    if (activeTask.current !== taskId) return;
    setTask(row);
    setEvents(trace);
  };
  useEffect(() => {
    setTask(null);
    setTab("result");
    setReason("");
    load().catch((e) => notify(e.message, true));
    const timer = setInterval(() => load().catch(() => {}), 2500);
    return () => clearInterval(timer);
  }, [taskId]);
  useEffect(() => {
    setCode(task?.result?.votes?.top_code || "");
  }, [task?.id, task?.result?.votes?.top_code]);
  async function action(path, body = {}) {
    try {
      const result = await post(
        path,
        /\/(clarifications|review)$/.test(path)
          ? { ...body, expected_revision: task.revision }
          : body,
      );
      await load();
      refresh();
      notify("操作已保存");
      return result;
    } catch (err) {
      notify(err.message, true);
    }
  }
  const result = task?.result,
    cites = result?.citations || [],
    done = new Set(events.map((e) => e.stage));
  const usage = events
    .filter((e) => e.stage === "model_usage")
    .reduce((n, e) => n + (e.data.usage?.total_tokens || 0), 0);
  return (
    <div className="drawer-mask" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="任务详情"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drawer-header">
          <div>
            <span className="eyebrow">业务任务 / {taskId.slice(0, 12)}</span>
            <h2>{kindText[task?.kind] || "任务详情"}</h2>
          </div>
          <button
            className="icon-btn"
            onClick={onClose}
            aria-label="关闭任务详情"
          >
            <X size={21} />
          </button>
        </header>
        {!task || task.id !== taskId ? (
          <div className="empty">正在载入任务…</div>
        ) : (
          <>
            <div className="drawer-summary">
              <Status value={task.status} />
              {task.payload.retrieval_profile === "bm25_diagnostic" && (
                <span className="badge">BM25诊断</span>
              )}
              <span>{new Date(task.created * 1000).toLocaleString()}</span>
              {task.payload.synthetic && (
                <span className="badge">联调样例</span>
              )}
            </div>
            <div className="drawer-tabs">
              {[
                ["result", "处理结果"],
                ["evidence", "引用证据"],
                ["trace", "执行记录"],
                ...(task.kind === "case" ? [["memory", "记忆与恢复"]] : []),
              ].map(([id, label]) => (
                <button
                  className={tab === id ? "active" : ""}
                  key={id}
                  onClick={() => setTab(id)}
                >
                  {label}
                  {id === "evidence" && cites.length > 0 && (
                    <small>{cites.length}</small>
                  )}
                </button>
              ))}
            </div>
            <div className="drawer-content">
              {tab === "memory" && (
                <CaseMemory task={task} reviewer={reviewer} notify={notify} />
              )}
              {tab === "result" && (
                <>
                  <div className="description-card">
                    <span>业务输入</span>
                    <p>
                      {task.payload.description ||
                        (task.kind === "asset_batch"
                          ? task.payload.filename || "业务数据导出"
                          : task.kind === "terms"
                            ? task.payload.pending.join("、")
                            : `申报税号 ${task.payload.code}`)}
                    </p>
                  </div>
                  {task.kind === "asset_batch" && (
                    <section className="panel form-panel">
                      <h3>业务批次处理</h3>
                      <p>
                        {result?.invalid
                          ? `发现 ${result.invalid} 行错误，整批未入库。`
                          : result?.applied
                            ? "校验通过，已入库。"
                            : result?.download_ready
                              ? "导出完成，可下载结果。"
                              : "批次排队或处理中，详细逐行进度见业务数据与词典。"}
                      </p>
                      <div className="batch-actions">
                        <a href={`/v1/business/batches/${taskId}/report`}>
                          下载结果报告
                        </a>
                        <a
                          href={`/v1/business/batches/${taskId}/report?errors_only=true`}
                        >
                          下载错误明细
                        </a>
                      </div>
                    </section>
                  )}
                  {task.kind === "case" && (
                    <div className="step-list">
                      {stages.map(([id, label], i) => (
                        <div key={id} className={done.has(id) ? "done" : ""}>
                          <span>
                            {done.has(id) ? <Check size={12} /> : i + 1}
                          </span>
                          {label}
                        </div>
                      ))}
                    </div>
                  )}
                  <RiskReport
                    result={
                      result?.risk_check ||
                      (task.kind === "declaration_risk" ? result : null)
                    }
                    taskId={taskId}
                  />
                  <LanguageReport
                    result={
                      result?.normalization ||
                      (task.kind === "declaration_language" ? result : null)
                    }
                  />
                  {writer &&
                    task.kind === "case" &&
                    [
                      "risk_blocked",
                      "risk_review_required",
                      "language_review_required",
                      "awaiting_models",
                      "awaiting_information",
                    ].includes(task.status) && (
                      <RevisionForm
                        task={task}
                        onCreated={onSelect}
                        notify={notify}
                      />
                    )}
                  {task.status === "failed" && (
                    <div className="form-error">
                      <b>任务未完成</b>
                      <p>
                        模型请求、依赖或结果校验失败。错误类型：{task.error}
                        。请检查运行状态或联系管理员重试。
                      </p>
                    </div>
                  )}
                  {task.status === "cancelled" && (
                    <Empty
                      title="任务已取消"
                      description="后续阶段将停止；已经发出的外部请求可能仍产生费用。"
                    />
                  )}
                  {result?.votes && (
                    <>
                      <div className="recommendation">
                        <span>
                          建议归类编码 · {result.votes.top_code.length}位
                        </span>
                        <strong>{result.votes.top_code}</strong>
                        <p>该结果为机器研判建议，尚需专家确认。</p>
                        <div>
                          {Object.entries(result.votes.levels).map(
                            ([level, value]) => (
                              <span key={level}>
                                {level}位一致率{" "}
                                <b>{Math.round(value * 100)}%</b>
                              </span>
                            ),
                          )}
                        </div>
                      </div>
                      {result.conflicts?.length > 0 && (
                        <div className="issue-box">
                          <h3>待核实事项</h3>
                          {result.conflicts.map((x) => (
                            <p key={x}>
                              <CircleAlert size={15} />
                              {{
                                catalog_notes_version_alignment_required:
                                  "税则目录与注释年份需核实",
                                derived_node_requires_source_verification:
                                  "路径包含派生索引，需核实条文",
                                low_layer_agreement: "多次研判一致率不足",
                                no_verifiable_citation: "缺少可验证引用",
                              }[x] || x}
                            </p>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                  {result?.report && (
                    <div className="report">
                      <h3>协同研判</h3>
                      <p>{result.report.summary}</p>
                      {result.report.questions?.map((q) => (
                        <p key={q}>{q}</p>
                      ))}
                    </div>
                  )}
                  {task.status === "awaiting_information" && writer && (
                    <section className="action-box">
                      <h3>补充商品信息</h3>
                      <ElementEditor
                        values={clarifications}
                        setValues={setClarifications}
                      />
                      <button
                        className="btn primary"
                        onClick={() =>
                          action(`/v1/cases/${taskId}/clarifications`, {
                            elements: clarifications.filter(
                              (x) => x.name && x.value,
                            ),
                          })
                        }
                      >
                        补充并继续 <ArrowRight size={15} />
                      </button>
                    </section>
                  )}
                  {task.kind === "terms" && result && (
                    <>
                      <h3>同义词分组</h3>
                      {Object.entries(result.groups || {}).map(
                        ([main, aliases]) => (
                          <div className="term-group" key={main}>
                            <b>{main}</b>
                            {aliases.map((x) => (
                              <span key={x}>{x}</span>
                            ))}
                          </div>
                        ),
                      )}
                      <h3>独立词与否定表达</h3>
                      <div className="word-tags">
                        {result.independent_words?.map((x) => (
                          <span key={x}>{x}</span>
                        ))}
                      </div>
                      <p className="small-note">
                        否定词：{result.negative_terms?.join("、") || "无"}
                      </p>
                      {reviewer &&
                        task.status === "completed" &&
                        !result.synthetic && (
                          <button
                            className="btn primary"
                            onClick={() =>
                              action(
                                `/v1/governance/terms/jobs/${taskId}/publish`,
                              )
                            }
                          >
                            确认并发布词簇 <Check size={15} />
                          </button>
                        )}
                      {result.synthetic && (
                        <div className="notice compact">
                          联调样例不能发布到正式术语库。
                        </div>
                      )}
                    </>
                  )}
                  {task.kind === "risk" && result && (
                    <div className="report">
                      <h3>
                        {result.status === "not_covered"
                          ? "未形成有效比较"
                          : `匹配完成 · ${result.compared} 次比较`}
                      </h3>
                      <p>
                        {result.synthetic
                          ? "本结果为联调样例，不代表真实申报风险。"
                          : "命中结果需要人工复核。"}
                      </p>
                      {result.hits?.map((x) => (
                        <div className="issue-row" key={x.knowledge_id}>
                          <b>{x.vocabulary}</b>
                          <span>相似度 {x.similarity.toFixed(4)}</span>
                          <span>{x.knowledge_id}</span>
                        </div>
                      ))}
                      {result.unknown?.length > 0 && (
                        <p>
                          存在 {result.unknown.length}{" "}
                          条未登录词记录，不能按“无风险”处理。
                        </p>
                      )}
                    </div>
                  )}
                  {reviewer &&
                    task.kind === "case" &&
                    ["awaiting_review"].includes(task.status) && (
                      <section className="action-box">
                        <h3>专家复核</h3>
                        <label>
                          最终编码
                          <input
                            value={code}
                            onChange={(e) => setCode(e.target.value)}
                            placeholder="输入核实后的六/八/十位编码"
                          />
                        </label>
                        <label>
                          复核依据
                          <textarea
                            rows={3}
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                            placeholder="请记录确认或驳回的具体依据"
                          />
                        </label>
                        <div className="review-buttons">
                          <button
                            className="btn secondary"
                            disabled={reason.trim().length < 3}
                            onClick={() =>
                              action(`/v1/cases/${taskId}/review`, {
                                approved: false,
                                reason,
                                reviewer: actor.username,
                              })
                            }
                          >
                            驳回
                          </button>
                          <button
                            className="btn primary"
                            disabled={
                              reason.trim().length < 3 ||
                              !/^\d{6}(\d{2}){0,2}$/.test(code)
                            }
                            onClick={() =>
                              action(`/v1/cases/${taskId}/review`, {
                                approved: true,
                                final_code: code,
                                reason,
                                reviewer: actor.username,
                              })
                            }
                          >
                            <Check size={16} />
                            确认编码
                          </button>
                        </div>
                      </section>
                    )}
                  {!result &&
                    !["failed", "cancelled"].includes(task.status) && (
                      <Empty
                        title="任务正在处理"
                        description="执行进度与结果会自动更新，无需重复提交。"
                      />
                    )}
                </>
              )}
              {tab === "evidence" &&
                (cites.length ? (
                  cites.map((c, i) => (
                    <blockquote key={i}>
                      <p>{c.quote}</p>
                      <cite>
                        <EvidenceSource sourceId={c.source_id} />
                      </cite>
                    </blockquote>
                  ))
                ) : (
                  <Empty
                    title="暂无引用证据"
                    description="归类研判产生的原文引用将保留来源标识。"
                  />
                ))}
              {tab === "result" &&
                task.kind === "terms" &&
                task.status === "completed" &&
                reviewer && (
                  <TermEditor
                    key={taskId}
                    task={task}
                    onCreated={onSelect}
                    notify={notify}
                  />
                )}
              {tab === "trace" && (
                <>
                  <div className="trace-summary">
                    已记录 {events.length} 条事件
                    {usage > 0 && ` · 模型 Token ${usage.toLocaleString()}`}
                  </div>
                  {events.map((e) => (
                    <div className="trace-item" key={e.seq}>
                      <span className="trace-dot" />
                      <div>
                        <b>
                          {stages.find((s) => s[0] === e.stage)?.[1] ||
                            {
                              teacher: "专家协同报告",
                              finished: "处理完成",
                              failed: "处理失败",
                              model_request: "模型请求",
                              model_usage: "模型用量",
                              ranking_rejected: "候选或引用校验未通过",
                            }[e.stage] ||
                            e.stage}
                        </b>
                        <small>
                          {new Date(e.created * 1000).toLocaleTimeString()}
                          {e.data.elapsed_seconds != null &&
                            ` · ${e.data.elapsed_seconds}s`}
                        </small>
                        {e.data.usage && (
                          <p>Token：{e.data.usage.total_tokens || 0}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </>
              )}
            </div>
            <div className="drawer-footer">
              <span>任务结果与操作记录持久保存</span>
              <div>
                {writer &&
                  [
                    "queued",
                    "running",
                    "awaiting_information",
                    "awaiting_review",
                  ].includes(task.status) && (
                    <button
                      className="btn secondary"
                      onClick={() => action(`/v1/tasks/${taskId}/cancel`)}
                    >
                      取消任务
                    </button>
                  )}
                {reviewer && task.status === "failed" && (
                  <button
                    className="btn secondary"
                    onClick={() => action(`/v1/tasks/${taskId}/retry`)}
                  >
                    重试任务
                  </button>
                )}
                <button className="btn secondary" onClick={onClose}>
                  关闭
                </button>
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function RevisionForm({ task, onCreated, notify }) {
  const [text, setText] = useState(task.payload.description),
    [code, setCode] = useState(task.payload.declared_code || ""),
    [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = Object.fromEntries(
        Object.entries(task.payload).filter(([k]) => !k.startsWith("_")),
      );
      const next = await post(
        `/v1/cases/${task.id}/resubmissions`,
        { ...payload, description: text, declared_code: code || null },
        true,
      );
      onCreated(next.id);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="action-box">
      <h3>企业整改 / 补充后重新校验</h3>
      <p>生成新任务，保留本次风险报告；重新从税智源开始处理。</p>
      <form onSubmit={submit}>
        <label>
          申报税号
          <input
            value={code}
            pattern="\d{2,10}"
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <label>
          完整修订申报原文
          <textarea
            required
            minLength={2}
            maxLength={6000}
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <button className="btn primary" disabled={busy}>
          提交修订版本
        </button>
      </form>
    </section>
  );
}

function TermEditor({ task, onCreated, notify }) {
  const [rows, setRows] = useState(
      Object.entries(task.result.groups || {}).map(([main, aliases]) => ({
        main,
        aliases: aliases.join("、"),
      })),
    ),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(e) {
    e.preventDefault();
    const groups = {};
    for (const row of rows) {
      if (!row.main.trim() || groups[row.main.trim()])
        return notify("主词不能为空或重复", true);
      groups[row.main.trim()] = row.aliases
        .split(/[、,，\n]/)
        .map((x) => x.trim())
        .filter(Boolean);
    }
    setBusy(true);
    try {
      const next = await post(
        `/v1/governance/terms/jobs/${task.id}/curate`,
        { groups, reason },
        true,
      );
      onCreated(next.id);
      notify("已生成独立修订版本，原结果保持可追溯");
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="action-box">
      <h3>人工修订词簇</h3>
      <p className="small-note">可调整主词和别名；保存为新版本后再确认发布。</p>
      <form onSubmit={save}>
        {rows.map((row, i) => (
          <div className="element-row" key={i}>
            <input
              aria-label={`修订主词 ${i + 1}`}
              value={row.main}
              onChange={(e) =>
                setRows(
                  rows.map((x, j) =>
                    j === i ? { ...x, main: e.target.value } : x,
                  ),
                )
              }
            />
            <input
              aria-label={`修订别名 ${i + 1}`}
              value={row.aliases}
              placeholder="别名使用顿号分隔"
              onChange={(e) =>
                setRows(
                  rows.map((x, j) =>
                    j === i ? { ...x, aliases: e.target.value } : x,
                  ),
                )
              }
            />
            <button
              type="button"
              className="icon-btn"
              aria-label={`移除词簇 ${i + 1}`}
              onClick={() => setRows(rows.filter((_, j) => j !== i))}
            >
              <X size={14} />
            </button>
          </div>
        ))}
        <button
          type="button"
          className="text-btn"
          onClick={() => setRows([...rows, { main: "", aliases: "" }])}
        >
          <Plus size={14} />
          添加词簇
        </button>
        <label>
          修订说明
          <input
            required
            minLength={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="说明调整主从词或拆分词簇的依据"
          />
        </label>
        <button
          className="btn secondary"
          disabled={busy || reason.trim().length < 3}
        >
          保存修订版本
        </button>
      </form>
    </section>
  );
}

export { TaskDetail, TermEditor };
