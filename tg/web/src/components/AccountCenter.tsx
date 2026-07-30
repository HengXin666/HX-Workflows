import { Clock3, RefreshCw, ShieldCheck, Smartphone, UserPlus, Users } from "lucide-react";
import type { Account, QrJob, Role } from "../lib/accounts";
import { AccountAvatar } from "./AccountAvatar";

function AccountList({ accounts, role }: { accounts: Account[]; role: Role }) {
  const filtered = accounts.filter((account) => account.role === role);
  return (
    <div className="account-list">
      {filtered.length === 0 ? <div className="empty">暂无账号</div> : null}
      {filtered.map((account) => (
        <article className="account" key={account.id}>
          <AccountAvatar account={account} />
          <div><b>{account.name}</b><span>{account.username || account.telegram_id || account.masked_session}</span><code>{account.source}</code></div>
        </article>
      ))}
    </div>
  );
}

export function AccountCenter(props: {
  accounts: Account[];
  proxy: string;
  qrJob: QrJob | null;
  qrOpen: boolean;
  twoFactorPassword: string;
  onProxyChange: (value: string) => void;
  onRefresh: () => void;
  onStartQr: (role: Role) => void;
  onCloseQr: () => void;
  onPasswordChange: (value: string) => void;
  onSubmitPassword: () => void;
}) {
  return (
    <>
      <header className="topbar account-hero">
        <div><div className="eyebrow"><Users /> Account vault</div><h1>账号与小号池</h1><p>在本地安全导入 Telegram 会话，统一管理主账号和自动化小号池。</p></div>
        <div className="actions">
          <label className="field"><span>代理</span><input value={props.proxy} onChange={(event) => props.onProxyChange(event.target.value)} /></label>
          <button className="primary icon-button" onClick={props.onRefresh}><RefreshCw /> 刷新账号资料</button>
        </div>
      </header>
      <section className="account-info-strip">
        <div><ShieldCheck /><span><strong>本地加密边界</strong><small>session 文件权限为 0600</small></span></div>
        <div><Clock3 /><span><strong>无固定有效期</strong><small>撤销或过期后需重新登录</small></span></div>
        <div><Smartphone /><span><strong>支持 2FA</strong><small>扫码后可继续输入云密码</small></span></div>
      </section>
      <section className="grid two">
        <div className="panel account-panel"><div className="panel-head"><div><h2>主账号</h2><span>调试、邀请与管理入口</span></div><button className="icon-button" onClick={() => props.onStartQr("main")}><UserPlus /> 添加主账号</button></div><AccountList accounts={props.accounts} role="main" /></div>
        <div className="panel account-panel"><div className="panel-head"><div><h2>小号 Pool</h2><span>由自动化工作流批量执行</span></div><button className="icon-button" onClick={() => props.onStartQr("alt")}><UserPlus /> 添加小号</button></div><AccountList accounts={props.accounts} role="alt" /></div>
      </section>
      {props.qrOpen ? (
        <div className="modal-backdrop" onClick={props.onCloseQr}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-head"><div><h2>扫码导入账号</h2><span>全程在本机完成</span></div><button onClick={props.onCloseQr}>关闭</button></div>
            <div className="qr-modal-body">
              <span className="status">{props.qrJob?.status || "等待二维码"}</span>
              {props.qrJob?.qr_image ? <img className="qr-image" src={props.qrJob.qr_image} alt="Telegram 登录二维码" /> : <div className="qr-placeholder">二维码生成中</div>}
              {props.qrJob?.url ? <a href={props.qrJob.url}>{props.qrJob.url}</a> : null}
              <p>{props.qrJob?.status === "needs_password" ? "该账号已开启两步验证，请在下方输入 Telegram 云密码继续。" : "使用 Telegram 移动端扫描二维码完成登录。二维码有效期约 30 秒，过期请重新打开。"}</p>
              {props.qrJob?.status === "needs_password" ? (
                <div className="two-factor-form">
                  <label><span>两步验证密码</span><input autoFocus type="password" autoComplete="current-password" value={props.twoFactorPassword} onChange={(event) => props.onPasswordChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") props.onSubmitPassword(); }} /></label>
                  <button className="primary" disabled={!props.twoFactorPassword} onClick={props.onSubmitPassword}>验证并完成登录</button>
                  <small>密码只用于当前登录任务，不会写入磁盘、日志或 GitHub。</small>
                </div>
              ) : null}
              {props.qrJob?.error ? <div className="error-box">{props.qrJob.error}</div> : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
