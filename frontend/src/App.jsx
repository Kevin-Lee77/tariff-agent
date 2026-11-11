import React, { useEffect, useState } from "react";
import {
  Layers,
  ChevronRight,
  RefreshCw,
  Plus,
  LogOut,
  CircleAlert,
  ArrowRight,
  Files,
  ShieldCheck,
  Clock,
  Check,
  BookOpen,
} from "lucide-react";
import { api, post, setCsrf } from "./api";
import { menus, roleText, stages } from "./domain";
import { TaskTable, TaskPanel, Pager } from "./components/ui";
import { Login } from "./pages/Login";
import { CaseForm } from "./pages/Classification";
import { TermForm } from "./pages/Terms";
import { Governance, DeclarationForm } from "./pages/Governance";
import { Knowledge } from "./pages/Knowledge";
import { System } from "./pages/System";
import { TaskDetail } from "./components/TaskDetail";
import { Assets } from "./pages/Assets";
import { Evaluation } from "./pages/Evaluation";
import { Memory } from './pages/Memory';

function App() {
  const [actor, setActor] = useState(null),
    [initial, setInitial] = useState(true),
    [page, setPage] = useState("overview");
  const [summary, setSummary] = useState({ counts: {} }),
    [tasks, setTasks] = useState({ items: [], total: 0 });
  const [knowledge, setKnowledge] = useState(null),
    [readiness, setReadiness] = useState(null);
  const [filter, setFilter] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState(null);
  const [notification, setNotification] = useState(null),
    [refresh, setRefresh] = useState(0);
  const notify = (text, error = false) => setNotification({ text, error });
  const reviewer = actor && ["admin", "reviewer"].includes(actor.role);
  const writer = actor && ["admin", "operator"].includes(actor.role);
  useEffect(() => {
    api("/auth/me")
      .then((a) => {
        setActor(a);
        setCsrf(a.csrf);
      })
      .catch(() => {})
      .finally(() => setInitial(false));
  }, []);
  useEffect(() => {
    if (!notification) return;
    const timer = setTimeout(() => setNotification(null), 5000);
    return () => clearTimeout(timer);
  }, [notification]);
  useEffect(() => {
    if (!actor) return;
    let current = true;
    const load = async () => {
      const params = new URLSearchParams({
        offset: String(offset),
        limit: "12",
      });
      const state = page === "review" ? "awaiting_review" : filter;
      if (state) params.set("status", state);
      if (page === "classification" || page === "review")
        params.set("kind", "case");
      if (page === "terms") params.set("kind", "terms");
      const result = await Promise.allSettled([
        api("/v1/dashboard"),
        api("/v1/tasks?" + params),
        api("/health/ready"),
        api("/v1/knowledge/summary"),
      ]);
      if (!current) return;
      if (result[0].status === "fulfilled") setSummary(result[0].value);
      if (result[1].status === "fulfilled") setTasks(result[1].value);
      else if (result[1].reason.status === 401) {
        setActor(null);
        notify("登录已过期，请重新登录", true);
      }
      if (result[2].status === "fulfilled") setReadiness(result[2].value);
      else setReadiness(result[2].reason.data);
      if (result[3].status === "fulfilled") setKnowledge(result[3].value);
    };
    load().catch((e) => notify(e.message, true));
    const timer = setInterval(() => load().catch(() => {}), 5000);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [actor, page, filter, offset, refresh]);
  const changePage = (next) => {
    setPage(next);
    setOffset(0);
    setFilter("");
  };
  const submitted = (task) => {
    setSelected(task.id);
    setRefresh((n) => n + 1);
    notify("任务已提交，可在任务中心查看处理进度");
  };
  const doLogout = async () => {
    try {
      await post("/auth/logout", {});
    } finally {
      setActor(null);
      setCsrf("");
      setSelected(null);
    }
  };
  if (initial) return <div className="loading-screen">正在连接工作台…</div>;
  if (!actor)
    return (
      <Login
        onLogin={(a) => {
          setActor(a);
          setCsrf(a.csrf);
        }}
      />
    );
  const currentMenu = menus.find((m) => m[0] === page);
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={() => changePage("overview")}>
          <span className="brand-mark">
            <Layers size={23} />
          </span>
          <span>
            税智<span className="brand-en">CUSTOMS INTELLIGENCE</span>
          </span>
        </a>
        <div className="workspace-label">商品归类与申报治理</div>
        <div className="nav-caption">业务工作台</div>
        <nav>
          {menus
            .filter((m) => m[0] !== "review" || reviewer)
            .map(([id, Icon, label]) => (
              <button
                key={id}
                aria-label={label}
                title={label}
                className={`nav-item ${page === id ? "active" : ""}`}
                onClick={() => changePage(id)}
              >
                <Icon size={18} />
                <span>{label}</span>
                {id === "review" && summary.counts.awaiting_review > 0 && (
                  <b>{summary.counts.awaiting_review}</b>
                )}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="online-dot" />
          独立业务工作空间<div>目录与证据按版本追溯</div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            海关业务 <ChevronRight size={14} />
            <b>{currentMenu?.[2]}</b>
          </div>
          <div className="topbar-right">
            <span className="workspace-pill">
              {knowledge?.test_only ? "隔离测试工作台" : "内部工作台"}
            </span>
            <div className="avatar">{actor.username[0].toUpperCase()}</div>
            <div className="user-caption">
              <b>{actor.username}</b>
              <small>{roleText[actor.role]}</small>
            </div>
            <button
              className="icon-btn"
              onClick={doLogout}
              aria-label="退出登录"
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <p className="eyebrow">业务工作空间 / {currentMenu?.[2]}</p>
              <h1>{currentMenu?.[2]}</h1>
              <p>
                {page === "overview"
                  ? "从申报数据治理到商品归类，让每一项判断有据可查。"
                  : page === "review"
                    ? "查看归类路径与引用证据，确认最终编码并记录复核依据。"
                    : "统一管理业务输入、执行过程与可追溯结果。"}
              </p>
            </div>
            <div className="heading-actions">
              {reviewer && page === "review" && (
                <a className="btn secondary" href="/v1/feedback/export">
                  <Check size={15} />
                  导出确认样本
                </a>
              )}
              <button
                className="btn secondary"
                onClick={() => setRefresh((n) => n + 1)}
              >
                <RefreshCw size={15} />
                刷新
              </button>
              {writer && page === "overview" && (
                <button
                  className="btn primary"
                  onClick={() => changePage("classification")}
                >
                  <Plus size={16} />
                  新建归类
                </button>
              )}
            </div>
          </div>
          {readiness?.status === "not_ready" && (
            <div className="notice">
              <CircleAlert size={18} />
              <div>
                <b>归类服务等待就绪</b>
                <span>
                  {!readiness.checks?.deepseek_key_present
                    ? "生成服务尚未配置。 "
                    : ""}
                  {!readiness.checks?.vector_gateway
                    ? "混合检索服务待就绪，可在商品归类页选择明确标注的BM25诊断模式。"
                    : "请在运行状态查看依赖。"}
                  现有规则检查仍可使用。
                </span>
              </div>
              <button onClick={() => changePage("system")}>
                查看状态 <ArrowRight size={14} />
              </button>
            </div>
          )}
          {page === "overview" && (
            <>
              <section className="stats-grid">
                {[
                  ["业务任务", summary.total || 0, Files, "blue"],
                  [
                    "等待专家复核",
                    summary.counts.awaiting_review || 0,
                    ShieldCheck,
                    "amber",
                  ],
                  [
                    "处理中",
                    (summary.counts.queued || 0) +
                      (summary.counts.running || 0),
                    Clock,
                    "indigo",
                  ],
                  ["已确认归类", summary.counts.approved || 0, Check, "green"],
                ].map(([label, value, Icon, color]) => (
                  <div className="stat-card" key={label}>
                    <div>
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                    <span className={`stat-icon ${color}`}>
                      <Icon size={21} />
                    </span>
                  </div>
                ))}
              </section>
              <section className="panel pipeline-panel">
                <div className="section-heading">
                  <div>
                    <h2>归类协同流程</h2>
                    <p>逐层收敛候选，保留原文证据，交由专家确认</p>
                  </div>
                  <span className="muted-label">两项前置工具 + 六阶段归类</span>
                </div>
                <div className="pipeline">
                  {stages.map(([id, label], i) => (
                    <React.Fragment key={id}>
                      <div>
                        <span>{String(i + 1).padStart(2, "0")}</span>
                        <b>{label}</b>
                      </div>
                      {i < stages.length - 1 && <ChevronRight size={17} />}
                    </React.Fragment>
                  ))}
                </div>
              </section>
              <section className="overview-grid">
                <div className="panel">
                  <div className="section-heading">
                    <div>
                      <h2>最近任务</h2>
                      <p>当前工作空间的实际处理记录</p>
                    </div>
                    <button
                      className="text-btn"
                      onClick={() => changePage("tasks")}
                    >
                      全部任务 <ArrowRight size={14} />
                    </button>
                  </div>
                  <TaskTable
                    data={{ ...tasks, items: tasks.items.slice(0, 5) }}
                    onSelect={setSelected}
                    compact
                  />
                </div>
                <div className="panel source-summary">
                  <span className="card-kicker">知识版本</span>
                  <BookOpen size={28} />
                  <h2>中国商品归类目录</h2>
                  <p>关联品目、子目结构与可定位的注释来源。</p>
                  <div className="source-counts">
                    <div>
                      <b>{knowledge?.headings?.toLocaleString() || "—"}</b>
                      <span>品目</span>
                    </div>
                    <div>
                      <b>{knowledge?.entries?.toLocaleString() || "—"}</b>
                      <span>目录条目</span>
                    </div>
                  </div>
                  <span className="version-text">
                    {knowledge?.version || "语料未就绪"}
                  </span>
                  <button
                    className="text-btn"
                    onClick={() => changePage("knowledge")}
                  >
                    查看目录与来源 <ArrowRight size={14} />
                  </button>
                </div>
              </section>
            </>
          )}
          {page === "classification" && (
            <>
              <CaseForm
                disabled={!writer}
                hybridReady={readiness?.status === "ready"}
                hasGeneration={readiness?.checks?.deepseek_key_present === true}
                onSubmit={submitted}
                notify={notify}
              />
              <TaskPanel
                title="归类任务"
                tasks={tasks}
                onSelect={setSelected}
                filter={filter}
                setFilter={setFilter}
              />
            </>
          )}
          {(page === "tasks" || page === "review") && (
            <>
              <TaskPanel
                title={page === "review" ? "待复核归类" : "全部业务任务"}
                tasks={tasks}
                onSelect={setSelected}
                filter={filter}
                setFilter={setFilter}
                hideFilter={page === "review"}
              />
              <Pager {...tasks} offset={offset} onChange={setOffset} />
            </>
          )}
          {page === "terms" && (
            <>
              <DeclarationForm
                language
                writer={writer}
                onSubmit={submitted}
                notify={notify}
              />
              <details>
                <summary>词典维护：未知词聚类与审核发布</summary>
                <TermForm
                  writer={writer}
                  onSubmit={submitted}
                  notify={notify}
                />
              </details>
              <TaskPanel
                title="术语治理记录"
                tasks={tasks}
                onSelect={setSelected}
                filter={filter}
                setFilter={setFilter}
              />
              <Pager {...tasks} offset={offset} onChange={setOffset} />
            </>
          )}
          {page === "risk" && (
            <Governance notify={notify} onSubmit={submitted} writer={writer} />
          )}
          {page === "knowledge" && (
            <Knowledge summary={knowledge} notify={notify} />
          )}
          {page === "system" && (
            <System
              summary={summary}
              readiness={readiness}
              actor={actor}
              notify={notify}
            />
          )}
          {page === "assets" && (
            <Assets actor={actor} notify={notify} onSubmit={submitted} />
          )}
          {page === "evaluation" && <Evaluation notify={notify} />}
          {page === 'memory' && <Memory actor={actor} notify={notify} />}
          <footer>
            税智 · 商品归类与申报治理
            <span>机器提供研判建议，最终结论由专家确认</span>
          </footer>
        </main>
      </div>
      {selected && (
        <TaskDetail
          taskId={selected}
          actor={actor}
          onClose={() => setSelected(null)}
          onSelect={setSelected}
          notify={notify}
          refresh={() => setRefresh((n) => n + 1)}
        />
      )}
      {notification && (
        <div
          role="status"
          className={`toast ${notification.error ? "error" : ""}`}
        >
          {notification.error ? <CircleAlert size={17} /> : <Check size={17} />}{" "}
          {notification.text}
        </div>
      )}
    </div>
  );
}

export { App };
