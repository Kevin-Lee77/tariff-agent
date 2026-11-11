import React from "react";
import { Files, ChevronRight, ChevronLeft, Plus, X } from "lucide-react";
import { statusText, kindText } from "../domain";

function Status({ value }) {
  return (
    <span className={`badge status-${value}`}>
      {statusText[value] || value}
    </span>
  );
}

function Empty({
  title = "暂无记录",
  description = "提交任务后，处理结果会显示在这里。",
}) {
  return (
    <div className="empty">
      <Files size={30} />
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

function TaskTable({ data, onSelect, compact = false }) {
  if (!data.items.length) return <Empty />;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务 / 业务内容</th>
            {!compact && <th>类型</th>}
            <th>状态</th>
            <th>提交时间</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.items.map((row) => (
            <tr key={row.id} onClick={() => onSelect(row.id)}>
              <td>
                <b className="row-title">
                  {row.payload.description ||
                    (row.kind === "terms"
                      ? `${row.payload.pending?.length || 0} 个待处理术语`
                      : `申报税号 ${row.payload.code || "—"}`)}
                </b>
                <span className="row-meta">
                  {row.id.slice(0, 12)}
                  {row.payload.synthetic && " · 联调样例"}
                </span>
              </td>
              {!compact && <td>{kindText[row.kind]}</td>}
              <td>
                <Status value={row.status} />
              </td>
              <td className="date-cell">
                {new Date(row.created * 1000).toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </td>
              <td>
                <button
                  className="text-btn"
                  aria-label={`查看任务 ${row.id.slice(0, 8)}`}
                >
                  <ChevronRight size={17} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TaskPanel({
  title,
  tasks,
  onSelect,
  filter,
  setFilter,
  hideFilter = false,
}) {
  return (
    <section className="panel task-panel">
      <div className="section-heading">
        <div>
          <h2>
            {title}
            <span className="count-pill">{tasks.total}</span>
          </h2>
          <p>查看进度、处理结果与协作记录</p>
        </div>
        {!hideFilter && (
          <select
            aria-label="筛选任务状态"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="">全部状态</option>
            {Object.entries(statusText).map(([id, text]) => (
              <option key={id} value={id}>
                {text}
              </option>
            ))}
          </select>
        )}
      </div>
      <TaskTable data={tasks} onSelect={onSelect} />
    </section>
  );
}

function Pager({ total, offset, onChange }) {
  return (
    <div className="pager">
      <span>共 {total} 条记录</span>
      <button
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - 12))}
      >
        <ChevronLeft size={15} />
      </button>
      <span>{Math.floor(offset / 12) + 1}</span>
      <button
        disabled={offset + 12 >= total}
        onClick={() => onChange(offset + 12)}
      >
        <ChevronRight size={15} />
      </button>
    </div>
  );
}

function ElementEditor({ values, setValues }) {
  return (
    <div className="elements">
      <div className="element-head">
        <span>申报要素</span>
        <button
          type="button"
          className="text-btn"
          onClick={() =>
            setValues([...values, { name: "", value: "", code: "" }])
          }
        >
          <Plus size={14} />
          添加要素
        </button>
      </div>
      {values.map((v, i) => (
        <div className="element-row" key={i}>
          <input
            aria-label={`要素名称 ${i + 1}`}
            placeholder="要素名称，如材质"
            value={v.name}
            onChange={(e) =>
              setValues(
                values.map((x, j) =>
                  j === i ? { ...x, name: e.target.value } : x,
                ),
              )
            }
          />
          <input
            aria-label={`要素值 ${i + 1}`}
            placeholder="要素内容"
            value={v.value}
            onChange={(e) =>
              setValues(
                values.map((x, j) =>
                  j === i ? { ...x, value: e.target.value } : x,
                ),
              )
            }
          />
          <input
            className="element-code"
            aria-label={`要素编码 ${i + 1}`}
            placeholder="编码（选填）"
            value={v.code}
            onChange={(e) =>
              setValues(
                values.map((x, j) =>
                  j === i ? { ...x, code: e.target.value } : x,
                ),
              )
            }
          />
          <button
            type="button"
            className="icon-btn"
            aria-label={`移除要素 ${i + 1}`}
            onClick={() => setValues(values.filter((_, j) => j !== i))}
          >
            <X size={15} />
          </button>
        </div>
      ))}
    </div>
  );
}

function EvidenceSource({ sourceId }) {
  const match = sourceId.match(/^notes2022:.*:p(\d+):/);
  return (
    <>
      {sourceId}
      {match && (
        <a
          className="text-btn"
          href={`/v1/knowledge/documents/notes2022.pdf#page=${match[1]}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          查看原文第 {match[1]} 页 <ChevronRight size={12} />
        </a>
      )}
    </>
  );
}
export {
  Status,
  Empty,
  TaskTable,
  TaskPanel,
  Pager,
  ElementEditor,
  EvidenceSource,
};
