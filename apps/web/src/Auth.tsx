import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ArrowRight, KeyRound, LoaderCircle, LogOut, Sparkles } from "lucide-react";
import { api, ApiError, clearAuth, type AuthSession, type User } from "./api";
import PasswordField from "./PasswordField";
import "./auth.css";

function clearConversationPointers() {
  for (const key of Object.keys(sessionStorage)) if (key.startsWith("oa-assistant-conversation:")) sessionStorage.removeItem(key);
}

function AuthForm({ session, onSession, onCancel, onLogout }: {
  session: AuthSession | null; onSession: (session: AuthSession) => void;
  onCancel: () => void; onLogout: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (session && newPassword !== confirmation) { setError("两次输入的新密码不一致"); return; }
    setBusy(true); setError("");
    try {
      const result = session ? await api.changePassword(password, newPassword) : await api.login(username.trim(), password);
      setPassword(""); setNewPassword(""); setConfirmation("");
      onSession(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "请求失败，请重试"); }
    finally { setBusy(false); }
  }
  return <main className="auth-page"><div className="auth-brand"><span className="auth-mark"><Sparkles size={23} /></span><div><strong>FlowMind</strong><span>智能协同办公</span></div></div>
    <section className="auth-content">
      <h1>{session ? (session.mustChangePassword ? "设置你的新密码" : "修改密码") : "登录"}</h1>
      <p className="auth-subtitle">{session ? `${session.user.name} · ${session.user.username}` : "使用分配的账号登录"}</p>
      {session?.mustChangePassword && <p className="auth-notice">首次登录须修改初始密码。</p>}
      <form onSubmit={submit}><fieldset disabled={busy}>
        {!session && <label className="auth-username">账号<input name="username" autoComplete="username" required maxLength={64} autoFocus value={username} onChange={(event) => setUsername(event.target.value)} /></label>}
        <PasswordField label={session ? "当前密码" : "密码"} value={password} onChange={setPassword} current />
        {session && <><PasswordField label="新密码" value={newPassword} onChange={setNewPassword} /><PasswordField label="确认新密码" value={confirmation} onChange={setConfirmation} /><p className="password-policy">12 至 128 位，包含字母和数字。</p></>}
        <div className="auth-error" role={error ? "alert" : undefined}>{error}</div>
        <button className="button primary auth-submit" type="submit">{busy ? <LoaderCircle size={18} className="spin" /> : session ? <KeyRound size={18} /> : <ArrowRight size={18} />}{session ? "保存新密码" : "登录"}</button>
        {session && <button type="button" className="button ghost auth-submit" onClick={session.mustChangePassword ? onLogout : onCancel}>{session.mustChangePassword ? <><LogOut size={17} />退出登录</> : "取消"}</button>}
      </fieldset></form>
      {!session && <p className="auth-support">尚未开通账号或忘记密码，请联系 HR 或系统管理员。</p>}
    </section>
  </main>;
}

export default function AuthGate({ children }: { children: (user: User, logout: () => void, password: () => void) => ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  function expired() { clearAuth(); clearConversationPointers(); setSession(null); setEditing(false); }
  async function restore() {
    setError("");
    try { setSession(await api.session()); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 401) expired(); else setError("无法连接服务，请检查连接后重试"); }
    finally { setLoading(false); }
  }
  useEffect(() => {
    let active = true;
    api.session().then((value) => { if (active) setSession(value); }).catch((cause) => {
      if (active && !(cause instanceof ApiError && cause.status === 401)) setError("无法连接服务，请检查连接后重试");
    }).finally(() => { if (active) setLoading(false); });
    window.addEventListener("oa-session-expired", expired);
    return () => { active = false; window.removeEventListener("oa-session-expired", expired); };
  }, []);
  useEffect(() => {
    if (!session) return;
    const check = () => { if (document.visibilityState === "visible") void restore(); };
    window.addEventListener("focus", check);
    return () => window.removeEventListener("focus", check);
  }, [session?.user.id]);
  async function logout() {
    try { await api.logout(); expired(); setError(""); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) expired();
      else setError("退出失败，请重试");
    }
  }
  if (loading) return <div className="loading-screen"><LoaderCircle className="spin" />正在加载…</div>;
  if (error) return <div className="loading-screen error-screen"><p role="alert">{error}</p><button className="button primary" onClick={() => void restore()}>重试</button></div>;
  if (!session || session.mustChangePassword || editing) return <AuthForm key={`${session?.user.id ?? "login"}:${session?.mustChangePassword}`} session={session} onSession={(value) => { setSession(value); setEditing(false); }} onCancel={() => setEditing(false)} onLogout={() => void logout()} />;
  return children(session.user, () => void logout(), () => setEditing(true));
}
