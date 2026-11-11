import React, { useEffect, useState } from "react";
import { Activity, Users, Plus } from "lucide-react";
import { api, post } from "../api";
import { Empty } from "../components/ui";
import { roleText } from "../domain";

function System({ summary, readiness, actor, notify }) {
  const [audit, setAudit] = useState([]),
    [users, setUsers] = useState([]);
  useEffect(() => {
    if (actor.role === "admin") {
      api("/v1/audit")
        .then(setAudit)
        .catch(() => {});
      api("/v1/users")
        .then(setUsers)
        .catch(() => {});
    }
  }, [actor]);
  const checks = [
    ["业务服务", true, "API 已连接"],
    [
      "任务执行器",
      summary.worker_online,
      summary.worker_online ? "心跳正常" : "未检测到近期心跳",
    ],
    ["生成模型", readiness?.checks?.deepseek_key_present, "DeepSeek 配置状态"],
    ["向量与精排", readiness?.checks?.vector_gateway, "本地检索服务状态"],
  ];
  return (
    <>
      <section className="panel form-panel">
        <div className="section-heading">
          <div>
            <h2>服务依赖</h2>
            <p>配置存在与真实可用性分别验证；模型费用在任务事件中记录。</p>
          </div>
          <Activity size={22} />
        </div>
        <div className="service-grid">
          {checks.map(([label, ok, description]) => (
            <div className="service-card" key={label}>
              <span className={`service-dot ${ok ? "ok" : ""}`} />
              <div>
                <b>{label}</b>
                <p>{description}</p>
              </div>
              <span>{ok ? "已连接" : "待就绪"}</span>
            </div>
          ))}
        </div>
        <div className="system-meta">
          数据库后端：{summary.database_backend || "—"}
          <span>审核操作保留操作者与时间</span>
        </div>
      </section>
      {actor.role === "admin" && (
        <>
          <section className="panel form-panel">
            <h2>
              <Users size={18} />
              账号与角色
            </h2>
            <p>创建账号使用独立密码，角色仅授予需要的业务能力。</p>
            <UserForm
              notify={notify}
              onCreated={() => api("/v1/users").then(setUsers)}
            />
            <div className="user-list">
              {users.map((u) => (
                <span key={u.username}>
                  <b>{u.username}</b>
                  {roleText[u.role]}
                </span>
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>最近操作审计</h2>
                <p>日志记录操作信息，不记录密码和模型密钥</p>
              </div>
            </div>
            {audit.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>操作者</th>
                      <th>操作</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.map((x) => (
                      <tr key={x.seq}>
                        <td>{x.actor}</td>
                        <td>{x.action}</td>
                        <td>{new Date(x.created * 1000).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="暂无审计记录" />
            )}
          </section>
        </>
      )}
    </>
  );
}

function UserForm({ notify, onCreated }) {
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    try {
      await post("/v1/users", Object.fromEntries(data));
      form.reset();
      onCreated();
      notify("账号已创建");
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="user-create" onSubmit={submit}>
      <input
        aria-label="新账号"
        name="username"
        placeholder="账号（至少3位）"
        minLength={3}
        required
      />
      <input
        aria-label="初始密码"
        name="password"
        type="password"
        placeholder="初始密码（至少12位）"
        minLength={12}
        required
        autoComplete="new-password"
      />
      <select aria-label="账号角色" name="role">
        <option value="operator">业务操作员</option>
        <option value="reviewer">归类专家</option>
        <option value="admin">系统管理员</option>
      </select>
      <button className="btn secondary" disabled={busy}>
        <Plus size={15} />
        创建账号
      </button>
    </form>
  );
}

export { System, UserForm };
