import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, GitBranch, KeyRound, LoaderCircle, Play, Power, RefreshCw, ShieldCheck, UploadCloud } from "lucide-react";
import { apiGet, apiPost } from "../lib/api";
import { Badge, Card, CardHeader, UiButton } from "./ui";

type SecretInfo = { name: string; updatedAt?: string };
type WorkflowInfo = { id: number; name: string; path: string; state: string };
type LocalCredential = { name: string; available: boolean; detail: string; sensitive: boolean };
type GithubStatus = {
  gh_installed: boolean;
  authenticated: boolean;
  repository: string;
  url?: string;
  permission?: string;
  secrets: SecretInfo[];
  workflows: WorkflowInfo[];
  local_credentials: LocalCredential[];
};

export function GithubManager() {
  const [repository, setRepository] = useState(() => localStorage.getItem("tg.github.repository") || "");
  const [status, setStatus] = useState<GithubStatus | null>(null);
  const [selected, setSelected] = useState<string[]>(["TG_SESSION_STRINGS", "TG_API_ID", "TG_API_HASH"]);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const remoteSecrets = useMemo(() => new Map((status?.secrets || []).map((item) => [item.name, item])), [status]);
  const load = useCallback(async () => {
    setBusy("status");
    setError("");
    try {
      const query = repository ? `?repository=${encodeURIComponent(repository)}` : "";
      const payload = await apiGet<GithubStatus>(`/api/github/status${query}`);
      setStatus(payload);
      if (payload.repository) {
        setRepository(payload.repository);
        localStorage.setItem("tg.github.repository", payload.repository);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "读取 GitHub 状态失败");
    } finally {
      setBusy("");
    }
  }, [repository]);

  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function syncSecrets() {
    if (!repository || !selected.length) return;
    setBusy("sync");
    setError("");
    setMessage("");
    try {
      const result = await apiPost<{ uploaded: string[] }>("/api/github/secrets/sync", { repository, names: selected, confirm: true });
      setMessage(`已安全同步 ${result.uploaded.length} 个 Secret；GitHub 不会回显 Secret 值。`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "同步失败");
    } finally {
      setBusy("");
    }
  }

  async function workflowAction(workflow: WorkflowInfo, action: "enable" | "disable" | "run") {
    setBusy(`${action}:${workflow.path}`);
    setError("");
    setMessage("");
    try {
      await apiPost("/api/github/workflows/action", { repository, workflow: workflow.path, action, confirm: true });
      setMessage(action === "run" ? `已触发 ${workflow.name}` : `已${action === "enable" ? "启用" : "停用"} ${workflow.name}`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "工作流操作失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="github-page">
      <header className="topbar github-hero">
        <div>
          <div className="eyebrow"><GitBranch /> GitHub control plane</div>
          <h1>GitHub 工作流与凭证管理</h1>
          <p>查看远程状态、同步本地 Telegram 凭证，并启停或手动触发工作流。Secret 值不会返回前端或写入日志。</p>
        </div>
        {status?.url ? <a className="repo-link" href={status.url} target="_blank" rel="noreferrer">打开仓库 <ExternalLink /></a> : null}
      </header>

      <div className="github-status-grid">
        <Card className="status-card"><GitBranch /><div><span>GitHub CLI</span><strong>{status?.gh_installed ? "已安装" : "未安装"}</strong></div><Badge tone={status?.gh_installed ? "good" : "warn"}>{status?.gh_installed ? "Ready" : "Missing"}</Badge></Card>
        <Card className="status-card"><ShieldCheck /><div><span>授权状态</span><strong>{status?.authenticated ? "已登录" : "未登录"}</strong></div><Badge tone={status?.authenticated ? "good" : "warn"}>{status?.permission || "Unknown"}</Badge></Card>
        <Card className="status-card"><KeyRound /><div><span>远程 Secrets</span><strong>{status?.secrets?.length || 0} 个</strong></div><Badge>{status?.repository || "未选择仓库"}</Badge></Card>
      </div>

      <Card className="mb-4 overflow-hidden">
        <CardHeader title="连接仓库" description="使用当前 gh 登录身份访问 owner/repo；不会在浏览器保存 token。" />
        <div className="github-repo-form">
          <label><span>目标仓库</span><input value={repository} placeholder="owner/repo" onChange={(event) => setRepository(event.target.value)} /></label>
          <UiButton variant="primary" disabled={busy === "status"} onClick={() => void load()}>{busy === "status" ? <LoaderCircle className="spin" /> : <RefreshCw />} 刷新状态</UiButton>
        </div>
      </Card>

      {error ? <div className="github-alert error"><AlertTriangle /> <span>{error}</span></div> : null}
      {message ? <div className="github-alert success"><CheckCircle2 /> <span>{message}</span></div> : null}

      <div className="github-columns">
        <Card className="overflow-hidden">
          <CardHeader title="本地凭证同步" description="勾选后点击同步，会用 gh secret set 覆盖远程同名 Secret。新增小号后再次同步即可。" action={<UploadCloud className="text-sky-400" />} />
          <div className="credential-list">
            {(status?.local_credentials || []).map((credential) => {
              const checked = selected.includes(credential.name);
              const remote = remoteSecrets.get(credential.name);
              return (
                <label className={credential.available ? "credential-row" : "credential-row disabled"} key={credential.name}>
                  <input type="checkbox" checked={checked} disabled={!credential.available} onChange={() => setSelected((items) => checked ? items.filter((item) => item !== credential.name) : [...items, credential.name])} />
                  <span><strong>{credential.name}</strong><small>{credential.detail}</small></span>
                  <Badge tone={remote ? "good" : "neutral"}>{remote ? "远程已存在" : "未同步"}</Badge>
                </label>
              );
            })}
          </div>
          <div className="github-card-footer">
            <p><ShieldCheck /> 凭证经 stdin 传给 gh，不会出现在进程参数中。</p>
            <UiButton variant="primary" disabled={!status?.authenticated || !selected.length || busy === "sync"} onClick={() => void syncSecrets()}>{busy === "sync" ? <LoaderCircle className="spin" /> : <UploadCloud />} 同步所选凭证</UiButton>
          </div>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader title="工作流管理" description="远程操作会立即生效；手动运行使用工作流默认输入。" action={<Power className="text-violet-400" />} />
          <div className="workflow-remote-list">
            {(status?.workflows || []).length ? status?.workflows.map((workflow) => (
              <article key={workflow.id}>
                <div><strong>{workflow.name}</strong><code>{workflow.path}</code></div>
                <Badge tone={workflow.state === "active" ? "good" : "warn"}>{workflow.state}</Badge>
                <div className="workflow-actions">
                  <UiButton disabled={Boolean(busy)} onClick={() => void workflowAction(workflow, "run")}><Play /> 运行</UiButton>
                  {workflow.state === "active" ? <UiButton variant="danger" disabled={Boolean(busy)} onClick={() => void workflowAction(workflow, "disable")}><Power /> 停用</UiButton> : <UiButton disabled={Boolean(busy)} onClick={() => void workflowAction(workflow, "enable")}><Power /> 启用</UiButton>}
                </div>
              </article>
            )) : <div className="empty">尚未读取到远程工作流</div>}
          </div>
        </Card>
      </div>

      <Card className="retention-card">
        <AlertTriangle />
        <div><strong>保活期限说明</strong><p>Telegram session 没有可承诺的固定有效期：退出所有设备、撤销会话、改安全设置或服务端判定过期后需重新登录。公开仓库若连续 60 天无仓库活动，GitHub 会自动停用 schedule 工作流；请在此页定期确认工作流为 active。</p></div>
      </Card>
    </div>
  );
}
