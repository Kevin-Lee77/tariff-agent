import React, { useEffect, useState } from "react";
import { api, post } from "../api";
import { ElementEditor } from "../components/ui";
import {
  BusinessBatches,
  DictionaryOperations,
} from "../components/BusinessWorkflows";

const labels = {
  synonym: "同义词",
  standard: "规范词",
  nonstandard: "非规范词",
  negative: "否定词",
  independent: "独立词",
  mutex: "规范互斥",
  classification_mutex: "归类互斥",
  score: "评分规则",
  element: "要素映射",
  observed: "观测词",
  pending: "未知词",
  risk: "风险策略",
};
export function Assets({ actor, notify, onSubmit }) {
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState("bill"),
    [data, setData] = useState({ items: [], total: 0 }),
    [summary, setSummary] = useState(null),
    [query, setQuery] = useState(""),
    [mode, setMode] = useState(""),
    [offset, setOffset] = useState(0),
    [version, setVersion] = useState(null),
    [bill, setBill] = useState(null),
    [refresh, setRefresh] = useState(0),
    [busy, setBusy] = useState(false);
  const reviewer = ["admin", "reviewer"].includes(actor.role),
    writer = ["admin", "operator"].includes(actor.role);
  useEffect(() => setSelected([]), [version?.id]);
  async function openDictionary(id) {
    try {
      const value = await api(`/v1/business/dictionaries/${id}`);
      setVersion(value);
      setKind("entries");
      setQuery("");
      setOffset(0);
      setRefresh((x) => x + 1);
    } catch (e) {
      notify(e.message, true);
    }
  }
  useEffect(() => {
    api("/v1/assets/summary")
      .then(setSummary)
      .catch((e) => notify(e.message, true));
  }, [refresh]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setData({ items: [], total: 0 });
    const params = new URLSearchParams({
      query,
      offset: String(offset),
      limit: "20",
    });
    if (mode) params.set("synthetic", mode === "sandbox" ? "true" : "false");
    if (version) params.set("dictionary_id", version.id);
    api("/v1/assets/" + kind + "?" + params)
      .then((result) => {
        if (current) setData(result);
      })
      .catch((e) => notify(e.message, true))
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [kind, query, mode, offset, version, refresh]);
  async function run(row) {
    setBusy(true);
    try {
      onSubmit(
        await post(
          `/v1/assets/bill/${row.id}/execute`,
          { classify: false },
          true,
        ),
      );
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function detail(row) {
    try {
      setBill(await api("/v1/assets/bill/" + row.id));
    } catch (e) {
      notify(e.message, true);
    }
  }
  return (
    <>
      <section className="panel form-panel">
        <div className="section-heading">
          <div>
            <h2>业务数据与词典</h2>
            <p>
              bill申报库、negative风险库与分版本术语词典；历史资料与合成数据分别保存。
            </p>
          </div>
        </div>
        <div className="word-tags">
          {summary &&
            [
              ["bill", "申报记录"],
              ["bill_element", "要素记录"],
              ["negative", "风险词"],
              ["dictionary_entry", "词典条目"],
            ].map(([key, label]) => (
              <span key={key}>
                {label} <b>{summary[key]?.toLocaleString()}</b>
              </span>
            ))}
        </div>
        <p>
          数据库：{summary?.database_backend || "载入中"} · 要素待映射：
          {summary?.bill_alignment?.requires_mapping?.toLocaleString() || 0} 条
        </p>
        <p className="small-note">
          编码／值数量不一致的申报保留全部字段，修订前禁止推断。合成数据只使用联调词典，不进入正式训练样本。
        </p>
        <div className="three-fields">
          <label>
            数据类型
            <select
              aria-label="数据类型"
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setOffset(0);
                setVersion(null);
                setBill(null);
              }}
            >
              <option value="bill">bill · 申报库</option>
              <option value="negative">negative · 风险库</option>
              <option value="dictionaries">税智语与风险词典版本</option>
              {kind === "entries" && <option value="entries">词典条目</option>}
            </select>
          </label>
          <label>
            数据环境
            <select
              aria-label="数据环境"
              value={mode}
              onChange={(e) => {
                setMode(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部</option>
              <option value="original">历史原始数据</option>
              <option value="sandbox">合成联调数据</option>
            </select>
          </label>
          <label>
            检索内容
            <input
              aria-label="数据检索"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
              placeholder="商品描述、风险词或词典名称"
            />
          </label>
        </div>
        {version && (
          <p>
            当前词典：{version.name} · {labels[version.kind] || version.kind}
            <button
              className="text-btn"
              onClick={() => {
                setVersion(null);
                setKind("dictionaries");
                setOffset(0);
              }}
            >
              返回版本列表
            </button>
          </p>
        )}
      </section>
      <section className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {kind === "entries" && reviewer && version?.kind !== "risk" && (
                  <th>选择</th>
                )}
                <th>
                  {kind === "bill"
                    ? "税号 / 申报内容"
                    : kind === "negative"
                      ? "风险词 / 范围"
                      : kind === "entries"
                        ? "词条 / 规范词"
                        : "词典版本 / 类型"}
                </th>
                <th>状态与来源</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  {kind === "entries" &&
                    reviewer &&
                    version?.kind !== "risk" && (
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`选择词条 ${row.term}`}
                          checked={selected.includes(row.id)}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, row.id]
                                : selected.filter((id) => id !== row.id),
                            )
                          }
                        />
                      </td>
                    )}
                  <td>
                    {kind === "bill" ? (
                      <>
                        <b>{row.code_ts}</b>
                        <p className="row-title">{row.description}</p>
                      </>
                    ) : kind === "negative" ? (
                      <>
                        <b>{row.vocabulary}</b>
                        <p>
                          {row.fit_area} · 要素 {row.element_code} · 阈值{" "}
                          {row.threshold}
                        </p>
                      </>
                    ) : kind === "entries" ? (
                      <>
                        <b>{row.term}</b>
                        <p>
                          规范词：{row.canonical} · 范围 {row.scope || "通用"} ·
                          要素{" "}
                          {row.element_name || row.element_code || "未限定"}
                        </p>
                        {row.payload?.conditions?.length > 0 && (
                          <p className="small-note">
                            全部命中：
                            {row.payload.conditions
                              .map(
                                (c) =>
                                  (c.element_code || c.element_name) +
                                  "：" +
                                  c.terms.join(" / "),
                              )
                              .join("；")}
                            {row.payload.score !== undefined
                              ? ` · 配置分数 ${row.payload.score}`
                              : ""}
                          </p>
                        )}
                      </>
                    ) : (
                      <>
                        <b>{row.name}</b>
                        <p>
                          {labels[row.kind] || row.kind} · {row.id.slice(0, 12)}
                        </p>
                      </>
                    )}
                  </td>
                  <td>
                    {row.synthetic !== undefined && (
                      <span className="badge">
                        {row.synthetic ? "合成联调" : "历史原始"}
                      </span>
                    )}
                    <p>
                      {row.alignment_status === "requires_mapping"
                        ? "待修订映射"
                        : row.alignment_status === "aligned"
                          ? "字段数量对齐"
                          : row.status === "unreviewed"
                            ? "待审核"
                            : row.status === "unsupported_scope"
                              ? "适用范围待解析"
                              : row.status === "approved"
                                ? "已审核"
                                : row.status === "sandbox"
                                  ? "联调"
                                  : row.status}
                    </p>
                    {row.active === 1 && <small>当前启用</small>}
                  </td>
                  <td>
                    {kind === "bill" ? (
                      <>
                        <button
                          className="text-btn"
                          onClick={() => detail(row)}
                        >
                          查看要素
                        </button>
                        <button
                          className="text-btn"
                          disabled={!writer || busy}
                          onClick={() => run(row)}
                        >
                          风险校验
                        </button>
                      </>
                    ) : kind === "dictionaries" ? (
                      <button
                        className="text-btn"
                        onClick={() => {
                          setVersion(row);
                          setKind("entries");
                          setOffset(0);
                          setQuery("");
                        }}
                      >
                        查看词条
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!data.items.length && (
          <div className="empty">
            {loading ? "正在查询数据…" : "没有匹配的数据。"}
          </div>
        )}
        <div className="form-bottom">
          <span>共 {data.total.toLocaleString()} 条</span>
          <div>
            <button
              className="btn secondary"
              disabled={loading || !offset}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              上一页
            </button>{" "}
            <button
              className="btn secondary"
              disabled={loading || offset + 20 >= data.total}
              onClick={() => setOffset(offset + 20)}
            >
              下一页
            </button>
          </div>
        </div>
      </section>
      {bill && (
        <section className="panel form-panel">
          <h3>bill要素明细 · {bill.code_ts}</h3>
          <p>
            原始行：{bill.raw.source_file || "合成数据"} / {bill.source_key} ·{" "}
            {bill.alignment_status === "aligned"
              ? "字段数量对齐"
              : "编码和值未对齐，禁止直接采用对应关系"}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>位置</th>
                  <th>要素编码</th>
                  <th>原始值</th>
                </tr>
              </thead>
              <tbody>
                {bill.elements.map((e) => (
                  <tr key={e.position}>
                    <td>{e.position + 1}</td>
                    <td>{e.code || "缺失"}</td>
                    <td>{e.value || "缺失"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="text-btn" onClick={() => setBill(null)}>
            收起明细
          </button>
        </section>
      )}
      {bill && writer && (
        <BillRevision
          key={bill.id}
          bill={bill}
          notify={notify}
          onSaved={(row) => {
            setBill(null);
            setRefresh((x) => x + 1);
            notify("已生成独立映射修订版本，原始bill保留");
            detail(row);
          }}
        />
      )}
      {version && reviewer && version.kind !== "risk" && (
        <DictionaryOperations
          version={version}
          selected={selected}
          notify={notify}
          onSaved={(row) => openDictionary(row.id)}
        />
      )}
      {version && reviewer && (
        <DictionaryRevision
          key={version.id}
          version={version}
          notify={notify}
          onSaved={() => {
            setRefresh((x) => x + 1);
            setVersion(null);
            setKind("dictionaries");
          }}
        />
      )}
      <BusinessBatches
        actor={actor}
        notify={notify}
        version={version}
        assetKind={kind}
        onImported={openDictionary}
      />
    </>
  );
}

function BillRevision({ bill, notify, onSaved }) {
  const [values, setValues] = useState(
      bill.elements.map((e) => ({
        name: e.name || "要素" + e.code,
        code: e.code,
        value: e.value,
      })),
    ),
    [code, setCode] = useState(bill.code_ts),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const result = await post(`/v1/assets/bill/${bill.id}/revisions`, {
        declared_code: code,
        elements: values,
        reason,
      });
      onSaved(result);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel form-panel">
      <h3>人工修订要素映射</h3>
      <p>
        请核对原始编码集合与值集合，明确每项对应关系；保存为新bill，原记录保留。
      </p>
      <form onSubmit={save}>
        <label>
          修订税号
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            pattern="\d{6}(\d{2}){0,2}"
            required
          />
        </label>
        <ElementEditor values={values} setValues={setValues} />
        <label>
          修订依据
          <input
            minLength={3}
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <button
          className="btn primary"
          disabled={busy || reason.trim().length < 3}
        >
          保存映射修订
        </button>
      </form>
    </section>
  );
}

function DictionaryRevision({ version, notify, onSaved }) {
  const [text, setText] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(publish = false) {
    setBusy(true);
    try {
      if (publish) {
        await post(`/v1/assets/dictionaries/${version.id}/publish`, { reason });
      } else {
        const value = text.trim() ? JSON.parse(text) : {};
        await post(`/v1/assets/dictionaries/${version.id}/revisions`, {
          reason,
          ...value,
        });
      }
      notify(publish ? "词典已审核发布" : "已保存独立修订版本");
      onSaved();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel form-panel">
      <h3>词典修订与审核</h3>
      <p>
        保存为新版本；历史原始词典须审核后才参与业务处理。合成词典禁止发布为正式词典。
      </p>
      <details>
        <summary>高级词典配置与完整条目修订</summary>
        <label>
          修订内容（JSON，可省略以复制当前版本）
          <textarea
            aria-label="词典修订内容"
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              '{"entries":[{"term":"别名","canonical":"规范词","scope":"02"}]}；风险策略只修改config.coverage与element_names'
            }
          />
        </label>
      </details>
      <label>
        审核或修订理由
        <input
          aria-label="词典修订理由"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      <div className="form-bottom">
        <button
          className="btn secondary"
          disabled={busy || reason.trim().length < 3}
          onClick={() => save(false)}
        >
          保存修订版本
        </button>
        <button
          className="btn primary"
          disabled={
            busy || reason.trim().length < 3 || Boolean(version.synthetic)
          }
          onClick={() => save(true)}
        >
          审核发布此版本
        </button>
      </div>
    </section>
  );
}
