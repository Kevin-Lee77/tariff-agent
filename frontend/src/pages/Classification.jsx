import React, { useState } from "react";
import { GitBranch, ArrowRight } from "lucide-react";
import { post } from "../api";
import { ElementEditor } from "../components/ui";

function CaseForm({ disabled, hybridReady, hasGeneration, onSubmit, notify }) {
  const [profile, setProfile] = useState("hybrid");
  const [governance, setGovernance] = useState("standard"),
    [declared, setDeclared] = useState(""),
    [digits, setDigits] = useState(10);
  const [description, setDescription] = useState(""),
    [elements, setElements] = useState([]),
    [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      onSubmit(
        await post(
          "/v1/cases",
          {
            description,
            declared_code: declared || null,
            target_digits: Number(digits),
            governance_profile: governance,
            retrieval_profile: profile,
            elements: elements.filter((x) => x.name && x.value),
          },
          true,
        ),
      );
      setDescription("");
      setElements([]);
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
          <h2>新建商品归类</h2>
          <p>
            税智源风险校验 → 税智语文本处理 →
            多智能体归类。风险命中即停止并生成企业反馈。
          </p>
        </div>
        <GitBranch size={21} />
      </div>
      <form onSubmit={submit}>
        <div className="three-fields">
          <label>
            申报税号
            <input
              value={declared}
              onChange={(e) => setDeclared(e.target.value)}
              pattern="\d{2,10}"
              placeholder="或在原文填写：税号：…"
            />
          </label>
          <label>
            词典环境
            <select
              aria-label="词典环境"
              value={governance}
              onChange={(e) => setGovernance(e.target.value)}
            >
              <option value="standard">业务词典</option>
              <option value="sandbox">联调词典 · 多章合成样例</option>
            </select>
          </label>
          <label>
            归类目标
            <select value={digits} onChange={(e) => setDigits(e.target.value)}>
              <option value={10}>中国十位税号</option>
              <option value={8}>八位编码</option>
              <option value={6}>HS六位子目</option>
            </select>
          </label>
        </div>
        <label className="mode-field">
          运行模式
          <select
            aria-label="运行模式"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
          >
            <option value="hybrid">标准混合检索</option>
            <option value="bm25_diagnostic">BM25诊断 · 不调用向量与精排</option>
          </select>
        </label>
        {profile === "bm25_diagnostic" && (
          <div className="notice compact">
            此模式用于验证工作流，保留诊断标记，不能作为混合检索效果评测。
          </div>
        )}
        <label>
          申报原文
          <textarea
            required
            minLength={2}
            maxLength={6000}
            rows={4}
            placeholder="例如：冷冻去骨牛肉，供人食用，未经其他加工。"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <ElementEditor values={elements} setValues={setElements} />
        <div className="form-bottom">
          <span>
            前置校验不依赖向量服务；分类依赖未就绪时保留已完成的校验结果。
          </span>
          <button
            className="btn primary"
            disabled={disabled || busy || description.trim().length < 2}
          >
            {busy ? "提交中…" : "开始归类"}
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
    </section>
  );
}

export { CaseForm };
