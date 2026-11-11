import React, { useState } from "react";
import { Layers, ChevronRight, ArrowRight, ShieldCheck } from "lucide-react";
import { post } from "../api";

function Login({ onLogin }) {
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      onLogin(
        await post("/auth/login", {
          username: form.get("username"),
          password: form.get("password"),
        }),
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="login-page">
      <section className="login-story">
        <div className="brand">
          <span className="brand-mark">
            <Layers />
          </span>
          <span>
            税智<span className="brand-en">CUSTOMS INTELLIGENCE</span>
          </span>
        </div>
        <div>
          <span className="login-kicker">商品归类与申报治理</span>
          <h1>
            让归类有依据，
            <br />
            让协作有章可循。
          </h1>
          <p>
            规范申报数据，追踪每一步研判，
            <br />
            连接业务操作与专家决策。
          </p>
          <div className="login-path">
            <span>商品描述</span>
            <ChevronRight />
            <span>规则与证据</span>
            <ChevronRight />
            <span>专家确认</span>
          </div>
        </div>
        <small>内部业务工作空间 · 账号访问</small>
      </section>
      <section className="login-form">
        <div>
          <span className="eyebrow">欢迎使用税智工作台</span>
          <h2>登录工作空间</h2>
          <p>使用分配给你的业务账号继续</p>
          <form onSubmit={submit}>
            <label>
              账号
              <input
                name="username"
                autoComplete="username"
                placeholder="请输入账号"
                required
                minLength={3}
              />
            </label>
            <label>
              密码
              <input
                type="password"
                name="password"
                autoComplete="current-password"
                placeholder="请输入密码"
                required
                maxLength={256}
              />
            </label>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <button className="btn primary" disabled={loading}>
              {loading ? "正在登录…" : "登录"}
              <ArrowRight size={17} />
            </button>
          </form>
          <div className="login-hint">
            <ShieldCheck size={16} />
            账号权限由管理员分配；无需填写模型密钥。
          </div>
        </div>
      </section>
    </div>
  );
}

export { Login };
