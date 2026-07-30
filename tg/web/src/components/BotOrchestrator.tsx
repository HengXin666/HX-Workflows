import { useCallback, useEffect, useState } from "react";
import { Activity, Bot, Play, Plus, Save, ShieldCheck, Trash2 } from "lucide-react";
import { apiDelete, apiGet, apiPost } from "../lib/api";
import {
  BOT_BLUEPRINT_TEMPLATES,
  defaultBotBlueprint,
  normalizeBlueprint,
  type BlueprintGraph,
  type BlueprintTemplate,
} from "../lib/botBlueprintCatalog";
import { Badge, Card, UiButton, UiSelect } from "./ui";
import { BotBlueprint } from "./BotBlueprint";

type ManagedBot = { id: string; label: string; name: string; username: string; token_hint: string };
type BotWorkflow = { api_version: 2; id: string; name: string; bot_id: string; blueprint: BlueprintGraph };
type LiveLog = { sequence: number; time: string; level: "info" | "success" | "warning" | "error"; event: string; message: string; details?: Record<string, unknown> };
type LiveSession = { id: string; bot_id: string; bot_username: string; status: "running" | "stopping" | "stopped" | "error"; created_at: number; logs: LiveLog[] };

function newWorkflow(botId = ""): BotWorkflow {
  return {
    api_version: 2,
    id: `bot-flow-${Date.now().toString(36)}`,
    name: "机器人编排",
    bot_id: botId,
    blueprint: defaultBotBlueprint(),
  };
}

function graphSignature(graph: BlueprintGraph): string {
  return JSON.stringify({
    nodes: graph.nodes.map((node) => ({ id: node.id, type: node.type, position: node.position, data: node.data })),
    edges: graph.edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle, targetHandle: edge.targetHandle, label: edge.label })),
  });
}

export function BotOrchestrator() {
  const [bots, setBots] = useState<ManagedBot[]>([]);
  const [workflows, setWorkflows] = useState<BotWorkflow[]>([]);
  const [workflow, setWorkflow] = useState<BotWorkflow>(() => newWorkflow());
  const [token, setToken] = useState("");
  const [label, setLabel] = useState("");
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pendingWorkflow, setPendingWorkflow] = useState<BotWorkflow | null>(null);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugging, setDebugging] = useState(false);
  const [liveSession, setLiveSession] = useState<LiveSession | null>(null);

  const load = useCallback(async () => {
    const [botPayload, flowPayload] = await Promise.all([
      apiGet<{ bots: ManagedBot[] }>("/api/bots"),
      apiGet<{ workflows: BotWorkflow[] }>("/api/bot-workflows"),
    ]);
    setBots(botPayload.bots);
    setWorkflows(flowPayload.workflows);
    setWorkflow((current) => !current.bot_id && botPayload.bots[0] ? { ...current, bot_id: botPayload.bots[0].id } : current);
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!liveSession || !["running", "stopping"].includes(liveSession.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const payload = await apiGet<{ session: LiveSession }>(`/api/bot-workflows/live-debug/${encodeURIComponent(liveSession.id)}`);
        setLiveSession(payload.session);
      } catch (error) { setStatus(error instanceof Error ? error.message : "读取实时日志失败"); }
    }, 800);
    return () => window.clearInterval(timer);
  }, [liveSession?.id, liveSession?.status]);

  async function addBot() {
    setStatus("正在通过 getMe 验证机器人…");
    try {
      await apiPost("/api/bots", { token, label });
      setToken(""); setLabel(""); setStatus("机器人已安全保存"); await load();
    } catch (error) { setStatus(error instanceof Error ? error.message : "添加失败"); }
  }

  async function save(): Promise<boolean> {
    try {
      const saved = await apiPost<{ workflow: BotWorkflow }>("/api/bot-workflows", workflow);
      const normalized = { ...saved.workflow, blueprint: normalizeBlueprint(saved.workflow.blueprint) };
      setWorkflow(normalized); setDirty(false); setStatus("蓝图已保存");
      setWorkflows((items) => [...items.filter((item) => item.id !== normalized.id), normalized]);
      return true;
    } catch (error) { setStatus(error instanceof Error ? error.message : "保存失败"); return false; }
  }

  function activateWorkflow(flow: BotWorkflow) {
    setWorkflow({ ...flow, blueprint: normalizeBlueprint(flow.blueprint) });
    setDirty(false); setPendingWorkflow(null); setStatus("");
  }

  function requestWorkflow(value: string) {
    const flow = workflows.find((item) => item.id === value);
    if (!flow || flow.id === workflow.id) return;
    if (dirty) setPendingWorkflow(flow); else activateWorkflow(flow);
  }

  function createFromTemplate(template: BlueprintTemplate) {
    setWorkflow({
      api_version: 2,
      id: `bot-flow-${Date.now().toString(36)}`,
      name: template.name,
      bot_id: bots[0]?.id || workflow.bot_id,
      blueprint: structuredClone(template.graph),
    });
    setDirty(true); setTemplateOpen(false); setStatus("");
  }

  async function deleteSavedWorkflow() {
    try {
      const deletedId = workflow.id;
      const result = await apiDelete<{ deleted: boolean }>(`/api/bot-workflows/${encodeURIComponent(deletedId)}`);
      if (!result.deleted) throw new Error("编排不存在或已经删除");
      const remaining = workflows.filter((item) => item.id !== deletedId);
      setWorkflows(remaining);
      if (remaining[0]) activateWorkflow(remaining[0]);
      else { setWorkflow(newWorkflow(bots[0]?.id || workflow.bot_id)); setDirty(false); }
      setDeleteOpen(false); setStatus("已删除机器人编排");
    } catch (error) { setDeleteOpen(false); setStatus(error instanceof Error ? error.message : "删除失败"); }
  }

  async function startLiveDebug() {
    setDebugging(true); setLiveSession(null);
    try {
      const result = await apiPost<{ session: LiveSession }>("/api/bot-workflows/live-debug/start", { workflow });
      setLiveSession(result.session);
    } catch (error) { setStatus(error instanceof Error ? error.message : "实时调试启动失败"); }
    finally { setDebugging(false); }
  }

  async function stopLiveDebug() {
    if (!liveSession) return;
    try {
      const result = await apiPost<{ session: LiveSession }>(`/api/bot-workflows/live-debug/${encodeURIComponent(liveSession.id)}/stop`, {});
      setLiveSession(result.session);
    } catch (error) { setStatus(error instanceof Error ? error.message : "停止实时调试失败"); }
  }

  const isSaved = workflows.some((item) => item.id === workflow.id);

  return (
    <div className="bot-orchestrator">
      <header className="topbar bot-hero">
        <div><div className="eyebrow"><Bot /> Telegram Bot Blueprint API v2</div><h1>机器人编排</h1><p>蓝图是唯一数据源：保存节点、连线、配置与分支，不再维护隐藏的旧版顺序 steps。</p></div>
        <div className="actions"><UiButton onClick={() => setTemplateOpen(true)}><Plus /> 新建</UiButton><UiButton onClick={() => setDebugOpen(true)}><Play /> 真人调试</UiButton><UiButton onClick={() => void save()}><Save /> 保存</UiButton></div>
      </header>

      <section className="bot-layout">
        <aside className="bot-compact-toolbar">
          <div className="bot-add-compact"><input aria-label="机器人备注" placeholder="备注" value={label} onChange={(event) => setLabel(event.target.value)} /><input aria-label="Bot Token" type="password" autoComplete="off" placeholder="BotFather Token" value={token} onChange={(event) => setToken(event.target.value)} /><UiButton variant="primary" disabled={!token} onClick={() => void addBot()}><Plus /> 添加机器人</UiButton></div>
          <label><span>当前机器人</span><UiSelect value={workflow.bot_id} placeholder="选择机器人" options={bots.map((bot) => ({ value: bot.id, label: `${bot.label} ${bot.username}` }))} onChange={(value) => { setWorkflow({ ...workflow, bot_id: value }); setDirty(true); }} /></label>
          <label><span>已保存编排</span><div className="saved-flow-control"><UiSelect value={isSaved ? workflow.id : ""} placeholder="选择编排" options={workflows.map((flow) => ({ value: flow.id, label: `${flow.name} · ${flow.blueprint.nodes.length} 节点` }))} onChange={requestWorkflow} /><UiButton variant="danger" aria-label="删除当前编排" title="删除当前已保存编排" disabled={!isSaved} onClick={() => setDeleteOpen(true)}><Trash2 /></UiButton></div></label>
        </aside>

        <main className="bot-builder">
          <Card className="overflow-hidden"><div className="bot-flow-head"><label><span>编排名称</span><input value={workflow.name} onChange={(event) => { setWorkflow({ ...workflow, name: event.target.value }); setDirty(true); }} /></label><Badge>{dirty ? "未保存" : `${workflow.blueprint.nodes.length} 节点`}</Badge></div></Card>
          <BotBlueprint key={workflow.id} graph={workflow.blueprint} onChange={(blueprint) => { const changed = graphSignature(workflow.blueprint) !== graphSignature(blueprint); setWorkflow((value) => ({ ...value, blueprint })); if (changed) setDirty(true); }} />
          {status ? <div className="bot-status"><ShieldCheck /> {status}</div> : null}
        </main>
      </section>

      {templateOpen ? <div className="modal-backdrop"><div className="modal template-dialog"><div className="panel-head"><div><h2>新建机器人蓝图</h2><p>选择可直接编辑的业务模板；创建后仍需保存。</p></div><button className="icon-button" aria-label="关闭" onClick={() => setTemplateOpen(false)}>×</button></div><div className="template-grid">{BOT_BLUEPRINT_TEMPLATES.map((template) => <button className="template-card" key={template.id} onClick={() => createFromTemplate(template)}><span><Bot /></span><strong>{template.name}</strong><p>{template.description}</p><small>{template.graph.nodes.length} 个节点 · {template.graph.edges.length} 条连线</small></button>)}</div>{dirty ? <p className="template-warning">当前蓝图尚未保存。选择模板会创建新蓝图并替换当前未保存内容。</p> : null}</div></div> : null}

      {deleteOpen ? <div className="modal-backdrop"><div className="modal unsaved-dialog"><div className="panel-head"><h2>删除机器人编排</h2></div><div className="unsaved-dialog-body"><p>确定永久删除“{workflow.name}”吗？蓝图的全部节点、连线和未保存修改都会被移除。</p><div className="actions"><UiButton variant="danger" onClick={() => void deleteSavedWorkflow()}><Trash2 />确认删除</UiButton><UiButton onClick={() => setDeleteOpen(false)}>取消</UiButton></div></div></div></div> : null}

      {debugOpen ? <div className="modal-backdrop debug-backdrop"><div className="modal debug-dialog"><div className="panel-head"><div><h2>Telegram 真人调试</h2><p>临时使用 getUpdates 连接当前机器人。启动后请直接去 Telegram 发命令、点菜单、完成支付；这里实时显示真实 Update 和 Bot API 日志。</p></div><button className="icon-button" aria-label="关闭调试" onClick={() => setDebugOpen(false)}>×</button></div><div className="debug-workspace"><aside className="debug-inputs"><h3>调试会话</h3><div className="live-debug-guide"><span>1</span><p>确认当前蓝图选择了要测试的机器人。</p><span>2</span><p>启动连接，然后打开 <strong>{liveSession?.bot_username || bots.find((bot) => bot.id === workflow.bot_id)?.username || "当前机器人"}</strong>。</p><span>3</span><p>在 Telegram 中亲自发送命令和点击按钮。</p></div>{liveSession ? <div className="live-session-info"><Badge tone={liveSession.status === "running" ? "good" : liveSession.status === "error" ? "warn" : "neutral"}>{liveSession.status}</Badge><code>{liveSession.id}</code></div> : null}<UiButton variant="primary" disabled={debugging || !workflow.bot_id || liveSession?.status === "running"} onClick={() => void startLiveDebug()}><Play />{debugging ? "正在连接 Telegram…" : "启动真人调试"}</UiButton>{liveSession && ["running", "stopping"].includes(liveSession.status) ? <UiButton variant="danger" onClick={() => void stopLiveDebug()}><Trash2 />停止调试</UiButton> : null}<small>若机器人已经部署 Webhook，系统会拒绝启动，不会擅自删除生产 Webhook。</small></aside><main className="debug-logs"><div className="debug-log-head"><div><h3>实时运行日志</h3><p>{liveSession ? `${liveSession.bot_username} · ${liveSession.logs.length} 条事件` : "尚未连接 Telegram"}</p></div>{liveSession ? <Badge tone={liveSession.status === "running" ? "good" : "warn"}>{liveSession.status === "running" ? "监听中" : liveSession.status}</Badge> : null}</div>{liveSession?.logs.length ? <div className="debug-log-list">{liveSession.logs.map((log) => <article className={`debug-log ${log.level}`} key={log.sequence}><span className="debug-sequence">{log.sequence}</span><Activity /><div><div><strong>{log.message}</strong><code>{log.time} · {log.event}</code></div>{log.details && Object.keys(log.details).length ? <p>{JSON.stringify(log.details)}</p> : null}</div><Badge tone={log.level === "success" ? "good" : log.level === "error" || log.level === "warning" ? "warn" : "neutral"}>{log.level}</Badge></article>)}</div> : <div className="debug-empty"><Activity /><strong>等待 Telegram 操作</strong><span>启动后，你在机器人里进行的真实操作会逐条出现在这里。</span></div>}</main></div></div></div> : null}

      {pendingWorkflow ? <div className="modal-backdrop"><div className="modal unsaved-dialog"><div className="panel-head"><h2>当前蓝图尚未保存</h2></div><div className="unsaved-dialog-body"><p>切换到“{pendingWorkflow.name}”会离开当前修改。请选择如何处理。</p><div className="actions"><UiButton variant="primary" onClick={async () => { if (await save()) activateWorkflow(pendingWorkflow); }}><Save />保存并切换</UiButton><UiButton variant="danger" onClick={() => activateWorkflow(pendingWorkflow)}>放弃修改</UiButton><UiButton onClick={() => setPendingWorkflow(null)}>取消</UiButton></div></div></div></div> : null}
    </div>
  );
}
