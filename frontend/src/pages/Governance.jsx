import React, { useState } from "react";
import { post } from "../api";

export function DeclarationForm({
  writer,
  notify,
  onSubmit,
  language = false,
}) {
  const [description, setDescription] = useState(""),
    [code, setCode] = useState(""),
    [profile, setProfile] = useState("standard"),
    [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      onSubmit(
        await post(
          "/v1/governance/declarations/" + (language ? "normalize" : "check"),
          {
            description,
            declared_code: code || null,
            governance_profile: profile,
          },
          true,
        ),
      );
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel form-panel">
      <div className="section-heading">
        <div>
          <h2>
            {language ? "税智语 · 申报文本处理" : "税智源 · 申报风险校验"}
          </h2>
          <p>
            {language
              ? "按已审核词典处理同义词、规范词；保留否定和独立词，提示互斥与歧义。"
              : "直接输入申报原文，按税号范围和要素映射校验；命中后返回风险标注及企业整改报告。"}
          </p>
        </div>
      </div>
      <form onSubmit={submit}>
        <label>
          词典环境
          <select
            aria-label="词典环境"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
          >
            <option value="standard">业务词典</option>
            <option value="sandbox">联调词典 · 多章合成样例</option>
          </select>
        </label>
        {
          <label>
            申报税号
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              pattern="\d{2,10}"
              placeholder="可选；也可写在原文中，如：税号：0202300090"
            />
          </label>
        }
        <label>
          申报原文
          <textarea
            required
            rows={5}
            minLength={2}
            maxLength={6000}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={
              language
                ? "冻制剔骨牛肉，不含添加剂"
                : "税号：0202300090；品名：冷冻去骨牛肉；用途：食用；加工方式：未经调制"
            }
          />
        </label>
        <div className="form-bottom">
          <span>
            {language
              ? "独立使用不会自动启动归类。"
              : "未覆盖、缺项或无法比较均不视为通过。"}
          </span>
          <button className="btn primary" disabled={!writer || busy}>
            {busy ? "提交中…" : language ? "处理申报文本" : "提交风险校验"}
          </button>
        </div>
      </form>
    </section>
  );
}

export function Governance(props) {
  return <DeclarationForm {...props} />;
}
