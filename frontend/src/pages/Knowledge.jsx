import React, { useState } from "react";
import { BookOpen, Search, ChevronRight, CircleAlert } from "lucide-react";
import { api } from "../api";
import { EvidenceSource } from "../components/ui";

function Knowledge({ summary, notify }) {
  const [code, setCode] = useState(""),
    [entry, setEntry] = useState(null);
  const lookup = async (e) => {
    e?.preventDefault();
    try {
      setEntry(await api("/v1/knowledge/entries/" + encodeURIComponent(code)));
    } catch (err) {
      notify(err.message, true);
    }
  };
  return (
    <>
      <section className="panel form-panel">
        <div className="section-heading">
          <div>
            <h2>目录与来源</h2>
            <p>查看当前目录结构、条文和页面证据，不把结构索引当作法条。</p>
          </div>
          <BookOpen size={22} />
        </div>
        <div className="knowledge-metrics">
          <div>
            <span>四位品目</span>
            <b>{summary?.headings || "—"}</b>
          </div>
          <div>
            <span>目录条目</span>
            <b>{summary?.entries || "—"}</b>
          </div>
          <div>
            <span>子目分组</span>
            <b>{summary?.groups || "—"}</b>
          </div>
          <div>
            <span>证据片段</span>
            <b>{summary?.evidence_fragments || "—"}</b>
          </div>
        </div>
        <p className="version-text">{summary?.version}</p>
        <div className="notice compact">
          <CircleAlert size={17} />
          <span>
            {summary?.test_only
              ? "当前为隔离测试语料，仅用于界面与接口验收。"
              : "当前目录与注释存在年份差异，适用性需由专家核实。"}
          </span>
        </div>
        <form className="lookup" onSubmit={lookup}>
          <Search size={17} />
          <input
            aria-label="查询编码"
            placeholder="输入编码，如 010121"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
            pattern="\d{2,10}"
          />
          <button className="btn primary">查询</button>
        </form>
      </section>
      {entry && (
        <section className="panel form-panel">
          <div className="code-path">
            {entry.path.map((p) => (
              <span key={p}>
                {p}
                <ChevronRight size={13} />
              </span>
            ))}
          </div>
          <h2>
            {entry.entry.code} · {entry.entry.text}
          </h2>
          <p className="source-label">来源：{entry.entry.source_id}</p>
          {entry.entry.notes?.map((note) => (
            <blockquote key={note.id}>
              <p>{note.text}</p>
              <cite>
                <EvidenceSource sourceId={note.id} />
              </cite>
            </blockquote>
          ))}
          <h3>下级目录</h3>
          <div className="child-codes">
            {entry.children.map((c) => (
              <button
                key={c.code}
                onClick={() => {
                  setCode(c.code);
                  api("/v1/knowledge/entries/" + c.code)
                    .then(setEntry)
                    .catch((e) => notify(e.message, true));
                }}
              >
                <b>{c.code}</b>
                {c.text}
              </button>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

export { Knowledge };
