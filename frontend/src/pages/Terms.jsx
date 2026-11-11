import React, { useState } from "react";
import { ScanText, ArrowRight } from "lucide-react";
import { api, post } from "../api";

function TermForm({ writer, onSubmit, notify }) {
  const [pending, setPending] = useState(""),
    [threshold, setThreshold] = useState(0.9),
    [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const known = await api("/v1/terms");
      onSubmit(
        await post(
          "/v1/governance/terms/jobs",
          {
            known: known.groups || {},
            pending: pending
              .split(/[\n,，]/)
              .map((x) => x.trim())
              .filter(Boolean),
            threshold: Number(threshold),
          },
          true,
        ),
      );
      setPending("");
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel form-panel">
      <div className="section-heading">
        <div>
          <h2>术语归一化</h2>
          <p>优先匹配已发布词簇，再处理未知词；否定表达单独保留。</p>
        </div>
        <ScanText size={22} />
      </div>
      <form onSubmit={submit}>
        <label>
          待处理术语
          <textarea
            rows={4}
            value={pending}
            onChange={(e) => setPending(e.target.value)}
            placeholder="每行一个术语，或使用逗号分隔"
            required
            maxLength={50000}
          />
        </label>
        <div className="form-bottom">
          <label className="inline-field">
            聚类相似度阈值
            <input
              type="number"
              min="0"
              max="1"
              step="0.01"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </label>
          <button className="btn primary" disabled={!writer || busy}>
            {busy ? "提交中…" : "提交术语治理"}
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
    </section>
  );
}

export { TermForm };
