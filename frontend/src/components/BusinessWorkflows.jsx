import React, { useEffect, useState } from "react";
import { api, post } from "../api";

const kinds = {
  synonym: "同义词",
  standard: "规范词",
  nonstandard: "非规范词",
  negative: "否定词",
  independent: "独立词",
  mutex: "规范互斥",
  classification_mutex: "归类互斥",
  score: "评分规则",
};

export function BusinessBatches({
  actor,
  notify,
  version,
  assetKind,
  onImported,
}) {
  const [items, setItems] = useState([]),
    [batch, setBatch] = useState(null),
    [selected, setSelected] = useState(null);
  const [file, setFile] = useState(null),
    [kind, setKind] = useState("dictionary"),
    [dictionaryKind, setDictionaryKind] = useState("synonym");
  const [name, setName] = useState("新导入词典"),
    [sandbox, setSandbox] = useState(true),
    [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0),
    [total, setTotal] = useState(0),
    [exportKind, setExportKind] = useState("current");
  const writer = ["admin", "operator"].includes(actor.role);
  useEffect(() => {
    if (version) setSandbox(Boolean(version.synthetic));
  }, [version?.id]);
  useEffect(() => {
    let alive = true;
    setBatch(null);
    const load = async () => {
      const list = await api(`/v1/business/batches?offset=${offset}&limit=20`);
      if (alive) {
        setItems(list.items);
        setTotal(list.total);
      }
      if (selected) {
        const result = await api(`/v1/business/batches/${selected}`);
        if (alive) setBatch(result);
      }
    };
    load().catch((e) => notify(e.message, true));
    const timer = setInterval(() => load().catch(() => {}), 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selected, offset]);
  async function upload(event) {
    event.preventDefault();
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) {
      notify("文件不能超过4 MiB", true);
      return;
    }
    setBusy(true);
    try {
      const query = new URLSearchParams({
        filename: file.name,
        asset_kind: kind,
        dictionary_kind: dictionaryKind,
        name,
        synthetic: String(sandbox),
      });
      const task = await api("/v1/business/imports?" + query, {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": "application/octet-stream",
          "Idempotency-Key": crypto.randomUUID(),
        },
      });
      setOffset(0);
      setSelected(task.id);
      notify("已创建导入批次，逐行校验通过后统一入库");
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function exportData() {
    setBusy(true);
    try {
      const task = await post(
        "/v1/business/exports",
        {
          asset_kind:
            exportKind === "reports"
              ? "reports"
              : version
                ? "entries"
                : assetKind,
          dictionary_id: exportKind === "reports" ? null : version?.id || null,
          synthetic: sandbox,
        },
        true,
      );
      setSelected(task.id);
      notify("导出任务已排队");
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function control(action) {
    try {
      await post(`/v1/tasks/${selected}/${action}`);
      setBatch(await api(`/v1/business/batches/${selected}`));
    } catch (e) {
      notify(e.message, true);
    }
  }
  return (
    <section className="panel form-panel">
      <h2>批量导入与导出</h2>
      <p>
        支持 UTF-8 CSV / XLSX，最大4
        MiB、5000行。先校验整批；存在错误时不写入业务数据，修正文件后重新提交。
      </p>
      <form onSubmit={upload}>
        <div className="three-fields">
          <label>
            导入对象
            <select
              aria-label="导入对象"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="dictionary">词典条目</option>
              <option value="bill">申报记录</option>
              <option value="negative">风险记录</option>
            </select>
          </label>
          {kind === "dictionary" && (
            <label>
              词典类别
              <select
                aria-label="导入词典类别"
                value={dictionaryKind}
                onChange={(e) => setDictionaryKind(e.target.value)}
              >
                {Object.entries(kinds).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            导入环境
            <select
              aria-label="导入环境"
              value={sandbox ? "sandbox" : "business"}
              onChange={(e) => setSandbox(e.target.value === "sandbox")}
            >
              <option value="sandbox">合成联调</option>
              <option value="business">业务数据 · 词典仍需审核</option>
            </select>
          </label>
        </div>
        {kind === "dictionary" && (
          <label>
            词典名称
            <input
              aria-label="导入词典名称"
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
        )}
        <label>
          选择数据文件
          <input
            aria-label="导入数据文件"
            type="file"
            accept=".csv,.xlsx"
            onChange={(e) => setFile(e.target.files[0] || null)}
          />
        </label>
        <div className="form-bottom">
          <a href={`/v1/business/templates/${kind}`}>下载空白CSV模板</a>
          <div className="batch-actions">
            <button className="btn primary" disabled={!writer || busy || !file}>
              上传并校验
            </button>
            <label>
              导出对象
              <select
                aria-label="导出对象"
                value={exportKind}
                onChange={(e) => setExportKind(e.target.value)}
              >
                <option value="current">当前数据 / 词典</option>
                <option value="reports">治理报告</option>
              </select>
            </label>
            <button
              type="button"
              className="btn secondary"
              disabled={!writer || busy}
              onClick={exportData}
            >
              导出当前数据
            </button>
          </div>
        </div>
      </form>
      <p className="small-note">
        业务词典导入后为待审核；风险记录导入不会自动声明覆盖。合成数据不能发布为正式词典。导出按所选环境及当前词典过滤。
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>批次 / 文件</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((task) => (
              <tr key={task.id}>
                <td>
                  {task.payload.filename || "数据导出"}
                  <small className="block-note">
                    {task.id.slice(0, 12)} ·{" "}
                    {task.payload.operation === "export" ? "导出" : "导入"}
                  </small>
                </td>
                <td>
                  {task.status === "completed"
                    ? task.result?.invalid
                      ? "校验未通过"
                      : "已完成"
                    : {
                        queued: "排队中",
                        running: "处理中",
                        cancelled: "已取消",
                        failed: "失败",
                      }[task.status] || task.status}
                </td>
                <td>
                  <button
                    className="text-btn"
                    onClick={() => setSelected(task.id)}
                  >
                    查看批次
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form-bottom">
        <span>共 {total} 个批次</span>
        <div className="batch-actions">
          <button
            className="btn secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一批次页
          </button>
          <button
            className="btn secondary"
            disabled={offset + 20 >= total}
            onClick={() => setOffset(offset + 20)}
          >
            下一批次页
          </button>
        </div>
      </div>
      {batch && (
        <div className="batch-detail">
          <h3>批次进度 · {batch.job.id.slice(0, 12)}</h3>
          <p>
            {batch.job.status} · 已校验 {batch.processed}/
            {batch.job.result?.total ?? "待解析"} · 有效{" "}
            {batch.counts.valid || 0} · 错误 {batch.counts.invalid || 0}
          </p>
          <p>
            {batch.job.result?.applied
              ? "整批校验通过，已入库。"
              : batch.job.result?.invalid
                ? "发现错误，整批未写入业务库。"
                : ""}
          </p>
          <div className="batch-actions">
            <a href={`/v1/business/batches/${batch.job.id}/report`}>
              下载结果报告
            </a>
            <a
              href={`/v1/business/batches/${batch.job.id}/report?errors_only=true`}
            >
              下载错误明细
            </a>
            {writer && ["queued", "running"].includes(batch.job.status) && (
              <button
                className="btn secondary"
                onClick={() => control("cancel")}
              >
                取消批次
              </button>
            )}
            {["admin", "reviewer"].includes(actor.role) &&
              batch.job.status === "failed" && (
                <button
                  className="btn secondary"
                  onClick={() => control("retry")}
                >
                  重试批次
                </button>
              )}
            {batch.job.result?.dictionary_id && (
              <button
                className="btn secondary"
                onClick={() => onImported(batch.job.result.dictionary_id)}
              >
                查看导入词典
              </button>
            )}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>文件行号</th>
                  <th>校验状态</th>
                  <th>错误说明</th>
                </tr>
              </thead>
              <tbody>
                {batch.rows.map((row) => (
                  <tr key={row.row_number}>
                    <td>{row.row_number}</td>
                    <td>{row.status === "valid" ? "通过" : "错误"}</td>
                    <td>{row.error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small-note">
            页面显示前50行；完整诊断通过下载查看。取消后保留校验进度，业务入库与最终任务状态在同一事务提交。
          </p>
        </div>
      )}
    </section>
  );
}

export function DictionaryOperations({ version, selected, notify, onSaved }) {
  const [snapshot, setSnapshot] = useState(null),
    [action, setAction] = useState("confirm"),
    [target, setTarget] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  const [term, setTerm] = useState(""),
    [canonical, setCanonical] = useState(""),
    [scope, setScope] = useState(""),
    [element, setElement] = useState("");
  const [field1, setField1] = useState(""),
    [field2, setField2] = useState(""),
    [words1, setWords1] = useState(""),
    [words2, setWords2] = useState(""),
    [score, setScore] = useState(1);
  const [restoreTarget, setRestoreTarget] = useState("");
  useEffect(() => {
    setAction("confirm");
    setRestoreTarget("");
  }, [version.id]);
  useEffect(() => {
    let alive = true;
    setSnapshot(null);
    api(`/v1/business/dictionaries/${version.id}`)
      .then((value) => {
        if (alive) setSnapshot(value);
      })
      .catch((e) => notify(e.message, true));
    return () => {
      alive = false;
    };
  }, [version.id]);
  async function submit(operation) {
    if (!snapshot) return;
    setBusy(true);
    try {
      const result = await post(
        `/v1/business/dictionaries/${version.id}/operations`,
        {
          expected_revision: snapshot.revision,
          reason,
          operations: [operation],
        },
      );
      notify("已保存独立词典修订，历史版本保留");
      onSaved(result);
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function versionAction(kind) {
    setBusy(true);
    try {
      const result = await post(
        `/v1/business/dictionaries/${version.id}/${kind}`,
        {
          expected_revision: snapshot.revision,
          reason,
          target_id: restoreTarget,
        },
      );
      notify(
        kind === "withdraw"
          ? "已撤销发布，当前词典已停用"
          : "已生成回退草稿，审核后生效",
      );
      onSaved(result);
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  function add() {
    const payload = {};
    if (["mutex", "classification_mutex", "score"].includes(version.kind)) {
      payload.conditions = [
        {
          element_code: field1,
          terms: words1
            .split(/[，,]/)
            .map((s) => s.trim())
            .filter(Boolean),
        },
        {
          element_code: field2,
          terms: words2
            .split(/[，,]/)
            .map((s) => s.trim())
            .filter(Boolean),
        },
      ];
      if (version.kind === "score") payload.score = Number(score);
    }
    return submit({
      action: "add",
      entry: {
        term,
        canonical: canonical || term,
        scope,
        element_code: element,
        payload,
      },
    });
  }
  const disabled = busy || !snapshot || reason.trim().length < 3;
  return (
    <section className="panel form-panel">
      <h3>词典批量调整</h3>
      <p>
        在上方勾选词条；调整保存为新版本，正式词典需再次审核。跨要素、跨章节和正反极性混组会被拒绝。
      </p>
      <div className="three-fields">
        <label>
          批量操作
          <select
            aria-label="词条批量操作"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          >
            <option value="confirm">确认词条</option>
            <option value="unconfirm">撤销词条确认</option>
            <option value="rename">词条改名</option>
            {["synonym", "standard"].includes(version.kind) && (
              <>
                <option value="promote">选中从词设为主词</option>
                <option value="merge">合并到指定主词</option>
                <option value="move">移动到指定主词</option>
                <option value="split">拆分为新词组</option>
              </>
            )}
            <option value="delete">从新版本删除</option>
          </select>
        </label>
        <label>
          目标主词 / 新词名
          <input
            aria-label="词条目标"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <label>
          调整依据
          <input
            aria-label="批量调整依据"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            minLength={3}
          />
        </label>
      </div>
      <div className="form-bottom">
        <span>已选择 {selected.length} 条</span>
        <button
          className="btn primary"
          disabled={disabled || !selected.length}
          onClick={() => submit({ action, entry_ids: selected, value: target })}
        >
          保存批量调整
        </button>
      </div>
      <details>
        <summary>新增词条或跨要素规则</summary>
        <div className="three-fields">
          <label>
            词条 / 规则名称
            <input
              aria-label="新增词条名称"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
          </label>
          <label>
            规范主词
            <input
              aria-label="新增规范主词"
              value={canonical}
              onChange={(e) => setCanonical(e.target.value)}
            />
          </label>
          <label>
            章节范围
            <input
              aria-label="新增章节范围"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              placeholder="可留空或填编码前缀"
            />
          </label>
        </div>
        <label>
          适用要素编码
          <input
            aria-label="新增适用要素"
            value={element}
            onChange={(e) => setElement(e.target.value)}
          />
        </label>
        {["mutex", "classification_mutex", "score"].includes(version.kind) && (
          <div className="three-fields">
            <label>
              条件一要素编码
              <input
                aria-label="规则条件一要素"
                value={field1}
                onChange={(e) => setField1(e.target.value)}
              />
            </label>
            <label>
              条件一词组
              <input
                aria-label="规则条件一词组"
                value={words1}
                onChange={(e) => setWords1(e.target.value)}
                placeholder="逗号分隔，任一词命中"
              />
            </label>
            <span />
            <label>
              条件二要素编码
              <input
                aria-label="规则条件二要素"
                value={field2}
                onChange={(e) => setField2(e.target.value)}
              />
            </label>
            <label>
              条件二词组
              <input
                aria-label="规则条件二词组"
                value={words2}
                onChange={(e) => setWords2(e.target.value)}
              />
            </label>
            {version.kind === "score" && (
              <label>
                规则分数
                <input
                  type="number"
                  min={-1000}
                  max={1000}
                  value={score}
                  onChange={(e) => setScore(e.target.value)}
                />
              </label>
            )}
          </div>
        )}
        <button
          className="btn secondary"
          disabled={disabled || !term.trim()}
          onClick={add}
        >
          保存新增词条
        </button>
      </details>
      <label>
        回退至历史版本
        <select
          aria-label="回退历史版本"
          value={restoreTarget}
          onChange={(e) => setRestoreTarget(e.target.value)}
        >
          <option value="">请选择历史版本</option>
          {(snapshot?.ancestors || []).map((row) => (
            <option key={row.id} value={row.id}>
              {new Date(row.created * 1000).toLocaleString()} · {row.status} ·{" "}
              {row.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      <div className="batch-actions">
        <button
          className="text-btn"
          disabled={disabled || !restoreTarget}
          onClick={() => versionAction("restore")}
        >
          按目标历史版本生成回退草稿
        </button>
        <button
          className="text-btn"
          disabled={
            disabled ||
            version.synthetic ||
            version.status !== "approved" ||
            !version.active
          }
          onClick={() => versionAction("withdraw")}
        >
          撤销此版本发布
        </button>
      </div>
      <p className="small-note">
        回退只允许同一词典的祖先版本。规则评分为人工配置分数；跨要素规则只有全部条件命中时触发，不等于模型概率。
      </p>
    </section>
  );
}
