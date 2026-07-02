import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type CompositionEvent, type DragEvent } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  Handle,
  Position,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

type Page = "home" | "chat" | "flow" | "run";
type Role = "main" | "alt";

type Account = {
  id: string;
  role: Role;
  name: string;
  username: string;
  telegram_id: string;
  avatar: string;
  source: string;
  masked_session: string;
};

type QrJob = {
  id: string;
  role: Role;
  status: string;
  url?: string;
  qr_image?: string;
  account?: Account;
  error?: string;
};

type Workflow = {
  id: string;
  name: string;
  nodes: Node[];
  edges: Edge[];
  updated_at?: number;
};

type CompileOutput = {
  signins: string;
  tasks: string;
  github?: string;
};

type CompileIssue = {
  level: "error" | "warning";
  message: string;
  nodeId?: string;
};

type RunJob = {
  id: string;
  status: string;
  output?: string;
  active_node?: string | null;
  completed_nodes?: string[];
  failed_node?: string | null;
  error?: string;
};

type Dialog = {
  id: string;
  title: string;
  username: string;
  type: string;
  unread_count: number;
  date: string;
  last_message: string;
};

type ChatMessage = {
  id: string;
  date: string;
  out: boolean;
  sender: string;
  text: string;
};

type BotCommand = {
  command: string;
  description: string;
};

type BotCommandPayload = {
  commands: BotCommand[];
  is_bot?: boolean;
  source?: string;
};

const nodeCatalog = [
  { type: "task", title: "任务定义", desc: "开始一个顺序任务段", icon: "task" },
  { type: "open", title: "打开对话", desc: "选择 bot、群或频道", icon: "chat" },
  { type: "send", title: "发送消息", desc: "文本、命令或按钮", icon: "send" },
  { type: "parse", title: "解析/提取", desc: "提取消息和按钮链接", icon: "filter" },
  { type: "join", title: "添加账号/群组", desc: "加入链接或变量目标", icon: "plus" },
  { type: "forward", title: "转发消息", desc: "转发解析结果", icon: "forward" },
  { type: "link", title: "打开链接", desc: "保存或打开外部链接", icon: "link" },
];

const labels: Record<string, string> = {
  task: "任务定义",
  open: "打开对话",
  send: "发送消息",
  parse: "解析/提取",
  forward: "转发消息",
  join: "添加账号/群组",
  link: "打开链接",
};

const nextNodeKinds: Record<string, string[]> = {
  task: ["task", "open", "join", "link"],
  open: ["send", "parse", "forward", "join", "link", "task"],
  send: ["parse", "forward", "open", "join", "link", "task"],
  parse: ["forward", "join", "open", "send", "link", "task"],
  join: ["open", "send", "parse", "link", "task"],
  link: ["join", "open", "send", "parse", "task"],
  forward: ["task"],
};

const API = import.meta.env.VITE_TG_WEB_API || "http://127.0.0.1:8765";
const pagePaths: Record<Page, string> = {
  home: "/home",
  chat: "/chat",
  flow: "/flow",
  run: "/run",
};

function pageFromPath(pathname: string): Page {
  if (pathname.startsWith("/chat")) return "chat";
  if (pathname.startsWith("/flow")) return "flow";
  if (pathname.startsWith("/run")) return "run";
  return "home";
}

const initialNodes: Node[] = [
  {
    id: "task",
    type: "default",
    position: { x: 40, y: 80 },
    data: { nodeId: "task", kind: "task", label: "任务定义", taskId: "daily-sign", name: "每日签到" },
  },
  {
    id: "open-a",
    type: "default",
    position: { x: 310, y: 80 },
    data: { nodeId: "open-a", kind: "open", label: "打开对话", peer: "@example_bot", regex: false },
  },
  {
    id: "send-a",
    type: "default",
    position: { x: 580, y: 80 },
    data: { nodeId: "send-a", kind: "send", label: "发送消息", sendMode: "text", command: "", text: "签到" },
  },
  {
    id: "parse-a",
    type: "default",
    position: { x: 850, y: 80 },
    data: { nodeId: "parse-a", kind: "parse", label: "解析回执", parseMode: "after_send", pattern: "签到成功|积分", regex: true, limit: 5, saveAs: "last_parse" },
  },
  {
    id: "forward-a",
    type: "default",
    position: { x: 1120, y: 80 },
    data: { nodeId: "forward-a", kind: "forward", label: "转发消息", toPeer: "me", source: "last_parse" },
  },
];

const initialEdges: Edge[] = [
  { id: "e1", source: "task", target: "open-a" },
  { id: "e2", source: "open-a", target: "send-a" },
  { id: "e3", source: "send-a", target: "parse-a" },
  { id: "e4", source: "parse-a", target: "forward-a" },
];

function createWorkflowId() {
  return `flow-${Date.now().toString(36)}`;
}

function cloneNodes(items: Node[]): Node[] {
  return items.map((node) => ({ ...node, position: { ...node.position }, data: { ...node.data } }));
}

function cloneEdges(items: Edge[]): Edge[] {
  return items.map((edge) => ({ ...edge, data: edge.data ? { ...edge.data } : edge.data }));
}

function nodeKind(node?: Node) {
  return String((node?.data as Record<string, unknown> | undefined)?.kind || "");
}

function createNode(type: string, position: { x: number; y: number }): Node {
  const id = `${type}-${Date.now()}`;
  return {
    id,
    type: "default",
    position,
    data: {
      nodeId: id,
      kind: type,
      label: labels[type] || type,
      taskId: type === "task" ? `task-${Date.now().toString(36)}` : "",
      name: type === "task" ? "新任务" : "",
      peer: "@example_bot",
      sendMode: "text",
      command: "",
      text: "签到",
      pattern: "签到成功|积分",
      parseMode: type === "parse" ? "after_send" : "match",
      extract: "messages",
      saveAs: type === "parse" ? "last_parse" : "last_links",
      regex: type === "parse",
      toPeer: "me",
      sourceMode: "manual",
      source: "last_links",
      target: "",
      url: "",
    },
  };
}

function allowedTargets(sourceKind: string) {
  return nextNodeKinds[sourceKind] || nodeCatalog.map((item) => item.type);
}

function validateWorkflow(flow: Workflow): CompileIssue[] {
  const issues: CompileIssue[] = [];
  const byId = new Map(flow.nodes.map((node) => [node.id, node]));
  const taskNodes = flow.nodes.filter((node) => nodeKind(node) === "task");
  if (!flow.id.trim()) issues.push({ level: "error", message: "工作流 ID 为空" });
  if (!flow.name.trim()) issues.push({ level: "warning", message: "工作流名称为空" });
  if (flow.nodes.length === 0) {
    issues.push({ level: "error", message: "工作流没有节点" });
  } else if (taskNodes.length === 0) {
    issues.push({ level: "warning", message: "没有任务定义节点，将从第一个节点开始，并使用工作流 ID 作为任务 ID" });
  }

  const outgoing = new Map<string, Edge[]>();
  const incoming = new Map<string, Edge[]>();
  for (const edge of flow.edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) {
      issues.push({ level: "error", message: "存在连接到已删除节点的线" });
      continue;
    }
    outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge]);
    incoming.set(edge.target, [...(incoming.get(edge.target) || []), edge]);
    const sourceKind = nodeKind(source);
    const targetKind = nodeKind(target);
    if (!allowedTargets(sourceKind).includes(targetKind)) {
      issues.push({ level: "error", nodeId: source.id, message: `${String(source.data.label || sourceKind)} 不能连接到 ${String(target.data.label || targetKind)}` });
    }
  }

  for (const node of flow.nodes) {
    const kind = nodeKind(node);
    const data = node.data as Record<string, unknown>;
    if ((outgoing.get(node.id) || []).length > 1) {
      issues.push({ level: "error", nodeId: node.id, message: `${String(data.label || kind)} 只能连出一条执行线` });
    }
    if ((incoming.get(node.id) || []).length > 1) {
      issues.push({ level: "error", nodeId: node.id, message: `${String(data.label || kind)} 只能有一个上游节点` });
    }
    if (kind === "task" && !String(data.taskId || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "任务 ID 为空" });
    if (kind === "open" && !String(data.peer || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "打开对话节点缺少 peer" });
    if (kind === "send" && String(data.sendMode || "text") !== "command" && !String(data.text || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "发送消息节点缺少文本" });
    if (kind === "send" && String(data.sendMode) === "command" && !String(data.command || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "发送消息节点缺少命令/按钮" });
    if (kind === "parse" && !String(data.saveAs || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "解析节点缺少保存变量" });
    if (kind === "forward" && !String(data.toPeer || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "转发节点缺少目标会话" });
    if (kind === "join" && String(data.sourceMode || "manual") === "variable" && !String(data.source || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "添加节点缺少来源变量" });
    if (kind === "join" && String(data.sourceMode || "manual") !== "variable" && !String(data.target || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "添加节点缺少链接或用户名" });
    if (kind === "link" && !String(data.url || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "打开链接节点缺少链接" });
  }

  const start = taskNodes[0] || flow.nodes[0];
  const reachable = new Set<string>();
  let current = start?.id || "";
  while (current && !reachable.has(current)) {
    reachable.add(current);
    current = (outgoing.get(current) || [])[0]?.target || "";
  }
  for (const node of flow.nodes) {
    if (!reachable.has(node.id)) {
      issues.push({ level: "warning", nodeId: node.id, message: `${String((node.data as Record<string, unknown>).label || node.id)} 不在执行链路上` });
    }
  }
  return issues;
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

function Avatar({ account }: { account: Account }) {
  if (account.avatar) {
    return <img className="avatar" src={account.avatar} alt="" />;
  }
  return <div className="avatar fallback">{account.role === "main" ? "M" : "A"}</div>;
}

function NodeTextInput(props: {
  value: string;
  onCommit: (value: string) => void;
  multiline?: boolean;
}) {
  const [draft, setDraft] = useState(props.value);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    if (!composing && document.activeElement?.getAttribute("data-node-input") !== "true") {
      setDraft(props.value);
    }
  }, [props.value, composing]);

  const commit = (value = draft) => props.onCommit(value);
  const common = {
    value: draft,
    "data-node-input": "true",
    onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const next = event.target.value;
      setDraft(next);
      if (!composing) {
        props.onCommit(next);
      }
    },
    onBlur: () => commit(),
    onCompositionStart: () => setComposing(true),
    onCompositionEnd: (event: CompositionEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setComposing(false);
      const next = event.currentTarget.value;
      setDraft(next);
      props.onCommit(next);
    },
  };

  return props.multiline ? <textarea {...common} /> : <input {...common} />;
}

function FlowNode({ data, selected }: NodeProps) {
  const nodeData = data as Record<string, unknown>;
  const kind = String(nodeData.kind || "node");
  const readonly = Boolean(nodeData.readonly);
  const runState = String(nodeData.runState || "idle");
  const title = String(nodeData.label || "节点");
  const parseMode = String(nodeData.parseMode || "match");
  const extractMode = String(nodeData.extract || "messages");
  const detail =
    kind === "task"
      ? String(nodeData.name || nodeData.taskId || "")
      : kind === "open"
        ? String(nodeData.peer || "")
          : kind === "send"
          ? String(nodeData.sendMode) === "command"
            ? String(nodeData.command || "未选择命令/按钮")
            : String(nodeData.text || "")
          : kind === "parse"
            ? parseMode === "after_send"
              ? `发送后的消息 -> ${String(nodeData.saveAs || "last_parse")}`
              : extractMode === "messages" ? String(nodeData.pattern || "") : String(nodeData.saveAs || "last_links")
            : kind === "forward"
              ? String(nodeData.toPeer || "")
              : kind === "join"
                ? String(nodeData.sourceMode) === "variable" ? String(nodeData.source || "") : String(nodeData.target || "")
              : String(nodeData.url || "");
  const emitChange = (patch: Record<string, unknown>) => {
    window.dispatchEvent(new CustomEvent("tg-node-change", { detail: { id: nodeData.nodeId, patch } }));
  };
  const emitText = (key: string, value: string) => emitChange({ [key]: value });
  return (
    <div className={selected ? `flow-node kind-${kind} run-${runState} selected` : `flow-node kind-${kind} run-${runState}`}>
      <Handle type="target" position={Position.Left} />
      <div className="node-title">
        <div className="node-kind">{kind}</div>
        <strong>{title}</strong>
      </div>
      {readonly ? <div className="run-badge">{runState}</div> : null}
      {kind === "parse" ? <div className={`parse-mode-badge ${parseMode === "after_send" ? "after" : "match"}`}>{parseMode === "after_send" ? "解析发送后的消息" : "解析最近消息"}</div> : null}
      <span>{detail || "未配置"}</span>
      {!readonly ? <div className="node-inline nodrag nopan">
        {kind === "task" ? (
          <>
            <NodeTextInput value={String(nodeData.taskId || "")} onCommit={(value) => emitText("taskId", value)} />
            <NodeTextInput value={String(nodeData.name || "")} onCommit={(value) => emitText("name", value)} />
          </>
        ) : null}
        {kind === "open" ? (
          <>
            <NodeTextInput value={String(nodeData.peer || "")} onCommit={(value) => emitText("peer", value)} />
            <label>
              <input type="checkbox" checked={Boolean(nodeData.regex)} onChange={(event) => emitChange({ regex: event.target.checked })} />
              正则
            </label>
            {Boolean(nodeData.regex) ? (
              <div className="regex-test">
                <span>测试对话名</span>
                <NodeTextInput value={String(nodeData.testText || "")} onCommit={(value) => emitText("testText", value)} />
                {(() => {
                  const result = testPattern(String(nodeData.peer || ""), String(nodeData.testText || ""), true);
                  return (
                    <>
                      <b className={result.ok ? "match-ok" : "match-bad"}>{result.error || (result.ok ? "匹配" : "不匹配")}</b>
                      <HighlightText text={String(nodeData.testText || "")} ranges={result.ranges} />
                    </>
                  );
                })()}
              </div>
            ) : null}
          </>
        ) : null}
        {kind === "send" ? (
          String(nodeData.sendMode) === "command" ? (
            <NodeTextInput value={String(nodeData.command || "")} onCommit={(value) => emitText("command", value)} />
          ) : (
            <NodeTextInput multiline value={String(nodeData.text || "")} onCommit={(value) => emitText("text", value)} />
          )
        ) : null}
        {kind === "parse" ? (
          <>
            {parseMode === "after_send" ? (
              <NodeTextInput value={String(nodeData.saveAs || "last_parse")} onCommit={(value) => emitText("saveAs", value)} />
            ) : (
              <>
                <NodeTextInput value={String(nodeData.pattern || "")} onCommit={(value) => emitText("pattern", value)} />
                <label>
                  <input type="checkbox" checked={Boolean(nodeData.regex)} onChange={(event) => emitChange({ regex: event.target.checked })} />
                  正则匹配
                </label>
                <div className="regex-test">
                  <span>测试回执</span>
                  <NodeTextInput multiline value={String(nodeData.testText || "")} onCommit={(value) => emitText("testText", value)} />
                  {(() => {
                    const result = testPattern(String(nodeData.pattern || ""), String(nodeData.testText || ""), Boolean(nodeData.regex));
                    return (
                      <>
                        <b className={result.ok ? "match-ok" : "match-bad"}>{result.error || (result.ok ? "匹配" : "不匹配")}</b>
                        <HighlightText text={String(nodeData.testText || "")} ranges={result.ranges} />
                      </>
                    );
                  })()}
                </div>
              </>
            )}
          </>
        ) : null}
        {kind === "forward" ? <NodeTextInput value={String(nodeData.toPeer || "")} onCommit={(value) => emitText("toPeer", value)} /> : null}
        {kind === "join" ? <NodeTextInput value={String(nodeData.sourceMode) === "variable" ? String(nodeData.source || "") : String(nodeData.target || "")} onCommit={(value) => emitText(String(nodeData.sourceMode) === "variable" ? "source" : "target", value)} /> : null}
        {kind === "link" ? <NodeTextInput value={String(nodeData.url || "")} onCommit={(value) => emitText("url", value)} /> : null}
      </div> : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { default: FlowNode };

function NodeIcon({ icon }: { icon: string }) {
  if (icon === "task") return <svg viewBox="0 0 24 24"><path d="M5 4h14v16H5V4Zm3 4v2h8V8H8Zm0 4v2h8v-2H8Zm0 4v2h5v-2H8Z" /></svg>;
  if (icon === "send") return <svg viewBox="0 0 24 24"><path d="M3 11.5 20.5 3 17 21l-5.2-6-5.8 4 2.4-6.8L3 11.5Z" /></svg>;
  if (icon === "filter") return <svg viewBox="0 0 24 24"><path d="M4 5h16l-6.2 7.1V19l-3.6-1.8v-5.1L4 5Z" /></svg>;
  if (icon === "plus") return <svg viewBox="0 0 24 24"><path d="M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7V4Z" /></svg>;
  if (icon === "forward") return <svg viewBox="0 0 24 24"><path d="M13 5v5.2C7.6 10.8 4.4 13.7 3 19c2.6-3.2 5.7-4.4 10-4v5l8-7.5L13 5Z" /></svg>;
  if (icon === "link") return <svg viewBox="0 0 24 24"><path d="M9.3 14.7a1 1 0 0 1 0-1.4l4-4a1 1 0 1 1 1.4 1.4l-4 4a1 1 0 0 1-1.4 0Zm-3.6 3.6a4 4 0 0 1 0-5.7l2.2-2.2 1.4 1.4-2.2 2.2a2 2 0 1 0 2.8 2.8l2.2-2.2 1.4 1.4-2.2 2.2a4 4 0 0 1-5.6.1Zm8.9-8.9-1.4-1.4 2.2-2.2a2 2 0 1 1 2.8 2.8L16 10.8l1.4 1.4 2.2-2.2a4 4 0 0 0-5.7-5.7l-2.2 2.2Z" /></svg>;
  return <svg viewBox="0 0 24 24"><path d="M4 5h16v10H8l-4 4V5Zm3 3v2h10V8H7Zm0 4v2h7v-2H7Z" /></svg>;
}

function NodePalette({ onAdd }: { onAdd: (type: string) => void }) {
  return (
    <div className="node-palette">
      <div className="node-palette-head">
        <strong>添加节点</strong>
        <span>点击添加到视野中心，也可拖到画布</span>
      </div>
      <div className="node-toolbox">
        {nodeCatalog.map((item) => (
          <button
            className={`node-tool kind-${item.type}`}
            key={item.type}
            draggable
            onClick={() => onAdd(item.type)}
            onDragStart={(event) => {
              event.dataTransfer.setData("application/tg-node-kind", item.type);
              event.dataTransfer.effectAllowed = "copy";
            }}
          >
            <i><NodeIcon icon={item.icon} /></i>
            <span>
              <b>{item.title}</b>
              <small>{item.desc}</small>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function testPattern(pattern: string, sample: string, regex: boolean): { ok: boolean; error?: string; ranges: Array<[number, number]> } {
  if (!pattern) return { ok: false, error: "未填写表达式", ranges: [] };
  try {
    if (!sample) return { ok: false, ranges: [] };
    if (!regex) {
      const index = sample.indexOf(pattern);
      return { ok: index >= 0, ranges: index >= 0 ? [[index, index + pattern.length]] : [] };
    }
    const flags = pattern.includes("(?") ? "gi" : "gi";
    const expression = new RegExp(pattern, flags);
    const ranges: Array<[number, number]> = [];
    for (const match of sample.matchAll(expression)) {
      const start = match.index ?? -1;
      const text = match[0] || "";
      if (start >= 0 && text) {
        ranges.push([start, start + text.length]);
      }
      if (text.length === 0) break;
    }
    return { ok: ranges.length > 0, ranges };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "表达式错误", ranges: [] };
  }
}

function HighlightText({ text, ranges }: { text: string; ranges: Array<[number, number]> }) {
  if (!text) return <p className="match-preview muted">没有测试文本</p>;
  if (!ranges.length) return <p className="match-preview">{text}</p>;
  const chunks: Array<{ text: string; hit: boolean }> = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) chunks.push({ text: text.slice(cursor, start), hit: false });
    chunks.push({ text: text.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < text.length) chunks.push({ text: text.slice(cursor), hit: false });
  return (
    <p className="match-preview">
      {chunks.map((chunk, index) => (chunk.hit ? <mark key={index}>{chunk.text}</mark> : <span key={index}>{chunk.text}</span>))}
    </p>
  );
}

function Field(props: { label: string; value: string | number | boolean; onChange: (value: string) => void; type?: string }) {
  return (
    <label className="field">
      <span>{props.label}</span>
      <input type={props.type || "text"} value={String(props.value)} onChange={(event) => props.onChange(event.target.value)} />
    </label>
  );
}

function TextArea(props: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>{props.label}</span>
      <textarea value={props.value} onChange={(event) => props.onChange(event.target.value)} />
    </label>
  );
}

function SelectField(props: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return (
    <label className="field">
      <span>{props.label}</span>
      <select value={props.value} onChange={(event) => props.onChange(event.target.value)}>
        {props.options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}

function AccountList({ accounts, role }: { accounts: Account[]; role: Role }) {
  const filtered = accounts.filter((account) => account.role === role);
  return (
    <div className="account-list">
      {filtered.length === 0 ? <div className="empty">暂无账号</div> : null}
      {filtered.map((account) => (
        <article className="account" key={account.id}>
          <Avatar account={account} />
          <div>
            <b>{account.name}</b>
            <span>{account.username || account.telegram_id || account.masked_session}</span>
            <code>{account.source}</code>
          </div>
        </article>
      ))}
    </div>
  );
}

function formatMessageTime(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function ChatDebugger(props: {
  accounts: Account[];
  proxy: string;
  onRefreshAccounts: () => void;
}) {
  const [accountId, setAccountId] = useState("");
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [selectedPeer, setSelectedPeer] = useState("");
  const [selectedTitle, setSelectedTitle] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [dialogQuery, setDialogQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [botCommands, setBotCommands] = useState<BotCommand[]>([]);
  const [loadingDialogs, setLoadingDialogs] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState("");
  const messageListRef = useRef<HTMLDivElement | null>(null);

  const selectedAccount = useMemo(() => props.accounts.find((account) => account.id === accountId), [accountId, props.accounts]);

  useEffect(() => {
    if (!accountId && props.accounts.length) {
      const main = props.accounts.find((account) => account.role === "main");
      setAccountId((main || props.accounts[0]).id);
    }
  }, [accountId, props.accounts]);

  const loadDialogs = useCallback(async () => {
    if (!accountId) return;
    setLoadingDialogs(true);
    setError("");
    try {
      const payload = await apiGet<{ dialogs: Dialog[] }>(
        `/api/dialogs?account=${encodeURIComponent(accountId)}&proxy=${encodeURIComponent(props.proxy)}&q=${encodeURIComponent(dialogQuery)}&limit=60`,
      );
      setDialogs(payload.dialogs);
      if (!selectedPeer && payload.dialogs[0]) {
        setSelectedPeer(payload.dialogs[0].id);
        setSelectedTitle(payload.dialogs[0].title);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载对话失败");
    } finally {
      setLoadingDialogs(false);
    }
  }, [accountId, dialogQuery, props.proxy, selectedPeer]);

  const loadMessages = useCallback(async () => {
    if (!accountId || !selectedPeer) return;
    setLoadingMessages(true);
    setError("");
    try {
      const payload = await apiGet<{ messages: ChatMessage[] }>(
        `/api/messages?account=${encodeURIComponent(accountId)}&peer=${encodeURIComponent(selectedPeer)}&proxy=${encodeURIComponent(props.proxy)}&limit=80`,
      );
      setMessages(payload.messages);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载消息失败");
    } finally {
      setLoadingMessages(false);
    }
  }, [accountId, props.proxy, selectedPeer]);

  const loadBotCommands = useCallback(async () => {
    if (!accountId || !selectedPeer) {
      setBotCommands([]);
      return;
    }
    try {
      const payload = await apiGet<BotCommandPayload>(
        `/api/bot/commands?account=${encodeURIComponent(accountId)}&peer=${encodeURIComponent(selectedPeer)}&proxy=${encodeURIComponent(props.proxy)}`,
      );
      setBotCommands(payload.commands);
    } catch {
      setBotCommands([]);
    }
  }, [accountId, props.proxy, selectedPeer]);

  useEffect(() => {
    void loadDialogs();
  }, [loadDialogs]);

  useEffect(() => {
    void loadMessages();
  }, [loadMessages]);

  useEffect(() => {
    void loadBotCommands();
  }, [loadBotCommands]);

  useEffect(() => {
    const element = messageListRef.current;
    if (element) {
      element.scrollTop = element.scrollHeight;
    }
  }, [messages, selectedPeer]);

  async function sendMessage(textOverride?: string) {
    const text = (textOverride ?? draft).trim();
    if (!accountId || !selectedPeer || !text) return;
    setDraft("");
    setError("");
    try {
      const payload = await apiPost<{ message: ChatMessage }>("/api/messages/send", {
        account: accountId,
        peer: selectedPeer,
        text,
        proxy: props.proxy,
      });
      setMessages((items) => [...items, payload.message]);
      window.setTimeout(() => void loadMessages(), 800);
    } catch (caught) {
      setDraft(text);
      setError(caught instanceof Error ? caught.message : "发送失败");
    }
  }

  return (
    <section className="chat-shell">
      <aside className="chat-column account-column">
        <div className="chat-column-head">
          <h2>账号</h2>
          <button onClick={props.onRefreshAccounts}>刷新</button>
        </div>
        <div className="chat-account-list">
          {props.accounts.length === 0 ? <div className="empty">暂无账号，先在主页导入 session</div> : null}
          {props.accounts.map((account) => (
            <button
              className={account.id === accountId ? "chat-account active" : "chat-account"}
              key={account.id}
              onClick={() => {
                setAccountId(account.id);
                setSelectedPeer("");
                setSelectedTitle("");
                setMessages([]);
                setBotCommands([]);
              }}
            >
              <Avatar account={account} />
              <span>
                <b>{account.name}</b>
                <small>{account.username || account.telegram_id || account.masked_session}</small>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <aside className="chat-column dialog-column">
        <div className="chat-column-head">
          <h2>对话</h2>
          <button onClick={() => loadDialogs()}>{loadingDialogs ? "读取中" : "刷新"}</button>
        </div>
        <input className="dialog-search" value={dialogQuery} onChange={(event) => setDialogQuery(event.target.value)} placeholder="搜索会话名或用户名" />
        <div className="dialog-list">
          {dialogs.length === 0 ? <div className="empty">{selectedAccount ? "没有读取到对话" : "选择账号后读取对话"}</div> : null}
          {dialogs.map((dialog) => (
            <button
              className={dialog.id === selectedPeer ? "dialog-item active" : "dialog-item"}
              key={dialog.id}
              onClick={() => {
                setSelectedPeer(dialog.id);
                setSelectedTitle(dialog.title);
              }}
            >
              <span>
                <b>{dialog.title}</b>
                <small>{dialog.username || dialog.type}</small>
              </span>
              {dialog.unread_count ? <i>{dialog.unread_count}</i> : null}
              <p>{dialog.last_message || "无文本消息"}</p>
            </button>
          ))}
        </div>
      </aside>

      <section className="chat-panel">
        <header className="chat-header">
          <div>
            <h2>{selectedTitle || "选择一个对话"}</h2>
            <span>{selectedAccount ? `${selectedAccount.name} · ${selectedPeer || "未选择 peer"}` : "未选择账号"}</span>
          </div>
          <button onClick={() => loadMessages()} disabled={!selectedPeer}>{loadingMessages ? "同步中" : "同步消息"}</button>
        </header>
        {error ? <div className="chat-error">{error}</div> : null}
        <div className="message-list" ref={messageListRef}>
          {messages.length === 0 ? <div className="empty">{selectedPeer ? "暂无消息" : "选择对话后查看消息"}</div> : null}
          {messages.map((message) => (
            <article className={message.out ? "message-bubble outgoing" : "message-bubble incoming"} key={message.id}>
              {!message.out && message.sender ? <strong>{message.sender}</strong> : null}
              <p>{message.text || "[空消息]"}</p>
              <time>{formatMessageTime(message.date)}</time>
            </article>
          ))}
        </div>
        {botCommands.length ? (
          <div className="bot-command-strip">
            {botCommands.map((item) => (
              <button
                key={item.command}
                title={item.description || item.command}
                onClick={() => setDraft(item.command)}
                onDoubleClick={() => {
                  setDraft(item.command);
                  void sendMessage(item.command);
                }}
              >
                <b>{item.command}</b>
                {item.description ? <span>{item.description}</span> : null}
              </button>
            ))}
          </div>
        ) : null}
        <footer className="composer">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            placeholder="输入调试消息，Ctrl+Enter 发送"
            disabled={!selectedPeer}
          />
          <button className="primary" onClick={() => sendMessage()} disabled={!selectedPeer || !draft.trim()}>发送</button>
        </footer>
      </section>
    </section>
  );
}

function NodeEditor({
  node,
  updateNode,
  botCommands,
  commandPeer,
  commandStatus,
  dialogs,
  dialogsStatus,
  onLoadDialogs,
}: {
  node?: Node;
  updateNode: (id: string, data: Record<string, unknown>) => void;
  botCommands?: BotCommand[];
  commandPeer?: string;
  commandStatus?: string;
  dialogs?: Dialog[];
  dialogsStatus?: string;
  onLoadDialogs?: () => void;
}) {
  if (!node) return <div className="empty">选择一个节点进行配置</div>;
  const data = node.data as Record<string, unknown>;
  const kind = String(data.kind || "task");
  const parseMode = String(data.parseMode || "match");
  const set = (key: string, value: string) => updateNode(node.id, { [key]: value });
  return (
    <div className="node-editor">
      <h3>{String(data.label || node.type)}</h3>
      {kind === "task" ? (
        <>
          <Field label="任务 ID" value={String(data.taskId || "")} onChange={(value) => set("taskId", value)} />
          <Field label="任务名称" value={String(data.name || "")} onChange={(value) => set("name", value)} />
        </>
      ) : null}
      {kind === "open" ? (
        <>
          <Field label="对话名 / 群名" value={String(data.peer || "")} onChange={(value) => set("peer", value)} />
          <div className="node-inline-actions">
            <button onClick={onLoadDialogs} disabled={!onLoadDialogs}>{dialogsStatus || "读取当前账号对话"}</button>
          </div>
          {dialogs?.length ? (
            <SelectField
              label="选择对话"
              value={String(data.peer || "")}
              onChange={(value) => set("peer", value)}
              options={[
                { value: String(data.peer || ""), label: String(data.peer || "手动输入") },
                ...dialogs.map((dialog) => ({ value: dialog.username || dialog.id, label: `${dialog.title}${dialog.username ? ` (${dialog.username})` : ""}` })),
              ]}
            />
          ) : null}
          <label className="switch inline">
            <input
              type="checkbox"
              checked={Boolean(data.regex)}
              onChange={(event) => updateNode(node.id, { regex: event.target.checked })}
            />
            <span>按正则表达式匹配</span>
          </label>
          {Boolean(data.regex) ? (
            <>
              <TextArea label="测试对话名" value={String(data.testText || "")} onChange={(value) => set("testText", value)} />
              {(() => {
                const result = testPattern(String(data.peer || ""), String(data.testText || ""), true);
                return (
                  <div className={result.ok ? "test-result ok" : "test-result bad"}>
                    {result.error || (result.ok ? "正则匹配成功" : "正则未匹配")}
                    <HighlightText text={String(data.testText || "")} ranges={result.ranges} />
                  </div>
                );
              })()}
            </>
          ) : null}
        </>
      ) : null}
      {kind === "send" ? (
        <>
          <SelectField
            label="发送方式"
            value={String(data.sendMode || "text")}
            onChange={(value) => updateNode(node.id, { sendMode: value })}
            options={[
              { value: "text", label: "输入内容" },
              { value: "command", label: botCommands?.length ? "选择命令/按钮" : "选择命令/按钮（未读取到）" },
            ]}
          />
          {commandPeer ? (
            <div className="node-context-hint">当前对话：{commandPeer}{commandStatus ? ` · ${commandStatus}` : ""}</div>
          ) : (
            <div className="node-context-hint">未连接到打开对话节点</div>
          )}
          {String(data.sendMode || "text") === "command" ? (
            botCommands?.length ? (
              <SelectField
                label="Bot 命令/按钮"
                value={String(data.command || botCommands[0]?.command || "")}
                onChange={(value) => updateNode(node.id, { command: value })}
                options={botCommands.map((item) => ({ value: item.command, label: `${item.command}${item.description ? ` - ${item.description}` : ""}` }))}
              />
            ) : (
              <Field label="Bot 命令/按钮" value={String(data.command || "")} onChange={(value) => set("command", value)} />
            )
          ) : (
            <TextArea label="消息内容" value={String(data.text || "")} onChange={(value) => set("text", value)} />
          )}
        </>
      ) : null}
      {kind === "parse" ? (
        <>
          <SelectField
            label="解析范围"
            value={parseMode}
            onChange={(value) => updateNode(node.id, { parseMode: value })}
            options={[
              { value: "after_send", label: "解析发送后的消息" },
              { value: "match", label: "按表达式匹配最近消息" },
            ]}
          />
          {parseMode === "after_send" ? (
            <div className="node-context-hint strong">只处理当前发送节点之后收到的新消息，不按最近消息条数扫描。</div>
          ) : null}
          <SelectField
            label="提取模式"
            value={String(data.extract || "messages")}
            onChange={(value) => updateNode(node.id, { extract: value, saveAs: value === "messages" ? String(data.saveAs || "last_parse") : "last_links" })}
            options={[
              { value: "messages", label: "匹配消息" },
              { value: "links", label: "提取消息/按钮链接" },
              { value: "join_links", label: "提取加群/账号链接" },
            ]}
          />
          {parseMode === "match" ? (
            <>
              <Field label="判断表达式" value={String(data.pattern || "")} onChange={(value) => set("pattern", value)} />
              <Field label="读取消息数" type="number" value={Number(data.limit || 5)} onChange={(value) => updateNode(node.id, { limit: Number(value) || 5 })} />
            </>
          ) : null}
          <Field label="保存变量" value={String(data.saveAs || (String(data.extract || "messages") === "messages" ? "last_parse" : "last_links"))} onChange={(value) => set("saveAs", value)} />
          {parseMode === "match" ? (
            <>
              <label className="switch inline">
                <input
                  type="checkbox"
                  checked={Boolean(data.regex)}
                  onChange={(event) => updateNode(node.id, { regex: event.target.checked })}
                />
                <span>使用正则表达式</span>
              </label>
              <TextArea label="测试回执消息" value={String(data.testText || "")} onChange={(value) => set("testText", value)} />
              {(() => {
                const result = testPattern(String(data.pattern || ""), String(data.testText || ""), Boolean(data.regex));
                return (
                  <div className={result.ok ? "test-result ok" : "test-result bad"}>
                    {result.error || (result.ok ? "表达式匹配成功" : "表达式未匹配")}
                    <HighlightText text={String(data.testText || "")} ranges={result.ranges} />
                  </div>
                );
              })()}
            </>
          ) : null}
        </>
      ) : null}
      {kind === "forward" ? (
        <>
          <Field label="转发到会话" value={String(data.toPeer || "")} onChange={(value) => set("toPeer", value)} />
          <Field label="来源变量" value={String(data.source || "last_parse")} onChange={(value) => set("source", value)} />
        </>
      ) : null}
      {kind === "join" ? (
        <>
          <SelectField
            label="目标来源"
            value={String(data.sourceMode || "manual")}
            onChange={(value) => updateNode(node.id, { sourceMode: value })}
            options={[
              { value: "manual", label: "指定链接/用户名" },
              { value: "variable", label: "使用上游提取变量" },
            ]}
          />
          {String(data.sourceMode || "manual") === "variable" ? (
            <Field label="来源变量" value={String(data.source || "last_links")} onChange={(value) => set("source", value)} />
          ) : (
            <Field label="链接 / @用户名" value={String(data.target || "")} onChange={(value) => set("target", value)} />
          )}
        </>
      ) : null}
      {kind === "link" ? <Field label="链接" value={String(data.url || "")} onChange={(value) => set("url", value)} /> : null}
    </div>
  );
}

function WorkflowApp() {
  const { screenToFlowPosition, getViewport } = useReactFlow();
  const [page, setPageState] = useState<Page>(() => pageFromPath(window.location.pathname));
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [qrJob, setQrJob] = useState<QrJob | null>(null);
  const [qrOpen, setQrOpen] = useState(false);
  const [proxy, setProxyState] = useState(() => localStorage.getItem("tg.proxy") || "http://127.0.0.1:2334");
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  const [edges, setEdges] = useState<Edge[]>(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string>("task");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowId, setWorkflowId] = useState("daily-sign");
  const [savedWorkflowId, setSavedWorkflowId] = useState("");
  const [workflowName, setWorkflowName] = useState("每日签到");
  const [compileOutput, setCompileOutput] = useState<CompileOutput>({ signins: "", tasks: "", github: "" });
  const [compileStatus, setCompileStatus] = useState("等待编译");
  const [compileIssues, setCompileIssues] = useState<CompileIssue[]>([]);
  const [runJob, setRunJob] = useState<RunJob | null>(null);
  const [accountLimit, setAccountLimit] = useState(() => Number(localStorage.getItem("tg.accountLimit") || "0"));
  const [accountLimitSavedAt, setAccountLimitSavedAt] = useState(() => (localStorage.getItem("tg.accountLimit") ? "已加载上次设置" : "0 表示全部账号"));
  const [flowBotCommands, setFlowBotCommands] = useState<BotCommand[]>([]);
  const [flowCommandStatus, setFlowCommandStatus] = useState("");
  const [flowAccountId, setFlowAccountId] = useState(() => localStorage.getItem("tg.flowAccountId") || "");
  const [flowDialogs, setFlowDialogs] = useState<Dialog[]>([]);
  const [flowDialogsStatus, setFlowDialogsStatus] = useState("读取当前账号对话");
  const [contextMenu, setContextMenu] = useState<
    | { kind: "node"; x: number; y: number; nodeId: string }
    | { kind: "edge"; x: number; y: number; edgeId: string }
    | { kind: "pane"; x: number; y: number; position: { x: number; y: number } }
    | { kind: "connect"; x: number; y: number; position: { x: number; y: number }; sourceId: string; options: string[]; replaceExisting: boolean }
    | null
  >(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState("");
  const connectingNodeId = useRef<string | null>(null);
  const suppressNextPaneClick = useRef(false);
  const runOutputRef = useRef<HTMLPreElement | null>(null);

  const selectedNode = useMemo(() => nodes.find((node) => node.id === selectedNodeId), [nodes, selectedNodeId]);
  const currentWorkflow = useMemo<Workflow>(
    () => ({ id: workflowId, name: workflowName, nodes, edges }),
    [edges, nodes, workflowId, workflowName],
  );
  const localCompileIssues = useMemo(() => validateWorkflow(currentWorkflow), [currentWorkflow]);
  const hasCompileErrors = localCompileIssues.some((issue) => issue.level === "error");
  const flowAccount = useMemo(
    () => accounts.find((account) => account.id === flowAccountId) || accounts.find((account) => account.role === "alt") || accounts.find((account) => account.role === "main") || accounts[0],
    [accounts, flowAccountId],
  );
  const selectedSendPeer = useMemo(() => {
    if (!selectedNode || String((selectedNode.data as Record<string, unknown>).kind || "") !== "send") return "";
    const byId = new Map(nodes.map((node) => [node.id, node]));
    let current = selectedNode.id;
    const visited = new Set<string>();
    while (current && !visited.has(current)) {
      visited.add(current);
      const previousEdge = edges.find((edge) => edge.target === current);
      if (!previousEdge) break;
      const previous = byId.get(previousEdge.source);
      if (!previous) break;
      const data = previous.data as Record<string, unknown>;
      if (String(data.kind || "") === "open") return String(data.peer || "");
      current = previous.id;
    }
    return "";
  }, [edges, nodes, selectedNode]);

  const setPage = useCallback((next: Page) => {
    setPageState(next);
    const path = pagePaths[next];
    if (window.location.pathname !== path) {
      window.history.pushState({ page: next }, "", path);
    }
  }, []);

  const loadAccounts = useCallback(async (refresh = false, proxyValue = proxy) => {
    const path = refresh ? `/api/accounts/refresh?proxy=${encodeURIComponent(proxyValue)}` : "/api/accounts";
    const payload = await apiGet<{ accounts: Account[] }>(path);
    setAccounts(payload.accounts);
  }, [proxy]);

  const loadWorkflows = useCallback(async () => {
    const payload = await apiGet<{ workflows: Workflow[] }>("/api/workflows");
    setWorkflows(payload.workflows);
  }, []);

  useEffect(() => {
    void loadAccounts();
    void loadWorkflows();
  }, [loadAccounts, loadWorkflows]);

  useEffect(() => {
    if (!flowAccountId && accounts.length) {
      const next = accounts.find((account) => account.role === "alt") || accounts.find((account) => account.role === "main") || accounts[0];
      setFlowAccountId(next.id);
      localStorage.setItem("tg.flowAccountId", next.id);
    }
  }, [accounts, flowAccountId]);

  useEffect(() => {
    const onPopState = () => setPageState(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    if (window.location.pathname === "/") {
      window.history.replaceState({ page: "home" }, "", "/home");
    }
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!selectedEdgeId || !["Delete", "Backspace"].includes(event.key)) return;
      const active = document.activeElement;
      if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement || active instanceof HTMLSelectElement) return;
      event.preventDefault();
      setEdges((items) => items.filter((edge) => edge.id !== selectedEdgeId));
      setSelectedEdgeId("");
      setContextMenu(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedEdgeId]);

  useEffect(() => {
    const element = runOutputRef.current;
    if (element) {
      element.scrollTop = element.scrollHeight;
    }
  }, [runJob?.output]);

  useEffect(() => {
    const onNodeChange = (event: Event) => {
      const detail = (event as CustomEvent<{ id: string; patch: Record<string, unknown> }>).detail;
      if (detail?.id) {
        updateNode(detail.id, detail.patch);
      }
    };
    window.addEventListener("tg-node-change", onNodeChange);
    return () => window.removeEventListener("tg-node-change", onNodeChange);
  }, []);

  useEffect(() => {
    setCompileIssues(localCompileIssues);
    if (hasCompileErrors) {
      setCompileStatus("图中存在错误，未生成 YAML");
      setCompileOutput({ signins: "", tasks: "", github: "" });
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setCompileStatus("正在编译");
      fetch(`${API}/api/workflows/compile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentWorkflow),
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) throw new Error(await response.text());
          return response.json() as Promise<CompileOutput>;
        })
        .then((output) => {
          setCompileOutput(output);
          setCompileStatus(localCompileIssues.length ? "已编译，有提示" : "已编译");
        })
        .catch((error) => {
          if (!controller.signal.aborted) {
            setCompileStatus(error instanceof Error ? error.message : "编译失败");
          }
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [currentWorkflow, hasCompileErrors, localCompileIssues]);

  useEffect(() => {
    const commandAccount = flowAccount;
    if (!commandAccount || !selectedSendPeer || String((selectedNode?.data as Record<string, unknown> | undefined)?.kind || "") !== "send") {
      setFlowBotCommands([]);
      setFlowCommandStatus(!commandAccount ? "没有可用于读取命令的账号" : "");
      return;
    }
    let cancelled = false;
    setFlowCommandStatus(`正在用 ${commandAccount.name} 读取命令`);
    apiGet<BotCommandPayload>(
      `/api/bot/commands?account=${encodeURIComponent(commandAccount.id)}&peer=${encodeURIComponent(selectedSendPeer)}&proxy=${encodeURIComponent(proxy)}`,
    )
      .then((payload) => {
        if (!cancelled) {
          setFlowBotCommands(payload.commands);
          setFlowCommandStatus(
            payload.commands.length
              ? `已读取 ${payload.commands.length} 个命令/按钮（${payload.source === "history" ? "历史消息" : payload.source === "keyboard" ? "键盘按钮" : "官方菜单"}）`
              : payload.is_bot === false
                ? "当前对话不是 bot"
                : "该 bot 没有公开命令/键盘按钮，也未在最近消息中发现 /xxx",
          );
        }
      })
      .catch((error) => {
        if (!cancelled) setFlowBotCommands([]);
        if (!cancelled) setFlowCommandStatus(error instanceof Error ? error.message : "命令读取失败");
      });
    return () => {
      cancelled = true;
    };
  }, [flowAccount, proxy, selectedNode, selectedSendPeer]);

  function updateAccountLimit(value: string) {
    const next = Math.max(0, Number(value) || 0);
    setAccountLimit(next);
    localStorage.setItem("tg.accountLimit", String(next));
    setAccountLimitSavedAt(`已保存：${next === 0 ? "全部账号" : `${next} 个账号`}`);
  }

  function updateProxy(value: string) {
    setProxyState(value);
    localStorage.setItem("tg.proxy", value);
  }

  function updateFlowAccount(value: string) {
    setFlowAccountId(value);
    localStorage.setItem("tg.flowAccountId", value);
    setFlowDialogs([]);
    setFlowDialogsStatus("读取当前账号对话");
  }

  async function loadFlowDialogs() {
    if (!flowAccount) {
      setFlowDialogsStatus("没有可用账号");
      return;
    }
    setFlowDialogsStatus("读取中");
    try {
      const payload = await apiGet<{ dialogs: Dialog[] }>(
        `/api/dialogs?account=${encodeURIComponent(flowAccount.id)}&proxy=${encodeURIComponent(proxy)}&limit=80`,
      );
      setFlowDialogs(payload.dialogs);
      setFlowDialogsStatus(`已读取 ${payload.dialogs.length} 个对话`);
    } catch (error) {
      setFlowDialogs([]);
      setFlowDialogsStatus(error instanceof Error ? error.message : "读取失败");
    }
  }

  function resetWorkflow(nextId = createWorkflowId(), nextName = "未命名执行流") {
    setWorkflowId(nextId);
    setSavedWorkflowId("");
    setWorkflowName(nextName);
    setNodes(cloneNodes(initialNodes).map((node) => ({ ...node, data: { ...node.data, nodeId: node.id } })));
    setEdges(cloneEdges(initialEdges));
    setSelectedNodeId("task");
    setCompileOutput({ signins: "", tasks: "", github: "" });
    setCompileStatus("等待编译");
    setCompileIssues([]);
    setRunJob(null);
  }

  function duplicateWorkflow(flow: Workflow = currentWorkflow) {
    setWorkflowId(createWorkflowId());
    setSavedWorkflowId("");
    setWorkflowName(`${flow.name || "执行流"} copy`);
    setNodes(cloneNodes(flow.nodes).map((node) => ({ ...node, data: { ...node.data, nodeId: node.id } })));
    setEdges(cloneEdges(flow.edges));
    setSelectedNodeId(flow.nodes[0]?.id || "task");
    setPage("flow");
  }

  async function startQr(role: Role) {
    const job = await apiPost<QrJob>("/api/qr/start", { role, proxy });
    setQrJob({ ...job, role });
    setQrOpen(true);
    const timer = window.setInterval(async () => {
      const status = await apiGet<QrJob>(`/api/qr/status?id=${job.id}`);
      setQrJob({ ...status, role });
      if (["done", "error", "needs_password", "not_found"].includes(status.status)) {
        window.clearInterval(timer);
        if (status.status === "done") {
          await loadAccounts(true, proxy);
          setQrOpen(false);
          setQrJob(null);
        } else {
          void loadAccounts(true, proxy);
        }
      }
    }, 1500);
  }

  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((items) => applyNodeChanges(changes, items)), []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges((items) => applyEdgeChanges(changes, items)), []);
  const onConnect = useCallback((connection: Connection) => {
    connectingNodeId.current = null;
    const source = nodes.find((node) => node.id === connection.source);
    const target = nodes.find((node) => node.id === connection.target);
    if (!source || !target) return;
    if (!allowedTargets(nodeKind(source)).includes(nodeKind(target))) return;
    if (edges.some((edge) => edge.source === connection.source)) return;
    if (nodeKind(target) !== "task" && edges.some((edge) => edge.target === connection.target)) return;
    setEdges((items) => addEdge(connection, items));
  }, [edges, nodes]);

  function addNode(type: string) {
    const viewport = getViewport();
    const rect = document.querySelector(".flow-canvas")?.getBoundingClientRect();
    const center = rect
      ? screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
      : { x: -viewport.x / viewport.zoom + 120, y: -viewport.y / viewport.zoom + 120 };
    addNodeAt(type, center);
  }

  function addNodeAt(type: string, position: { x: number; y: number }) {
    const node = createNode(type, position);
    setNodes((items) => [
      ...items,
      node,
    ]);
    setSelectedNodeId(node.id);
    setContextMenu(null);
    return node.id;
  }

  function addConnectedNode(type: string, position: { x: number; y: number }, sourceId: string, replaceExisting = false) {
    const node = createNode(type, position);
    setNodes((items) => [...items, node]);
    setEdges((items) => {
      const next = replaceExisting ? items.filter((edge) => edge.source !== sourceId) : items;
      return addEdge({ source: sourceId, sourceHandle: null, target: node.id, targetHandle: null }, next);
    });
    setSelectedNodeId(node.id);
    setContextMenu(null);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const type = event.dataTransfer.getData("application/tg-node-kind");
    if (!type) return;
    addNodeAt(type, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  }

  function handleConnectEnd(event: MouseEvent | TouchEvent) {
    const sourceId = connectingNodeId.current;
    connectingNodeId.current = null;
    if (!sourceId) return;
    const target = event.target as Element | null;
    if (target?.closest(".react-flow__handle") || target?.closest(".react-flow__node")) return;
    suppressNextPaneClick.current = true;
    window.setTimeout(() => {
      suppressNextPaneClick.current = false;
    }, 0);
    const point =
      "changedTouches" in event
        ? { x: event.changedTouches[0]?.clientX || 0, y: event.changedTouches[0]?.clientY || 0 }
        : { x: event.clientX, y: event.clientY };
    const source = nodes.find((node) => node.id === sourceId);
    const options = allowedTargets(nodeKind(source));
    if (!options.length) return;
    setContextMenu({
      kind: "connect",
      x: point.x,
      y: point.y,
      position: screenToFlowPosition(point),
      sourceId,
      options,
      replaceExisting: edges.some((edge) => edge.source === sourceId),
    });
  }

  function updateNode(id: string, patch: Record<string, unknown>) {
    setNodes((items) =>
      items.map((node) => (node.id === id ? { ...node, data: { ...node.data, nodeId: node.id, ...patch } } : { ...node, data: { ...node.data, nodeId: node.id } })),
    );
  }

  async function saveWorkflow() {
    const saved = await apiPost<{ workflow: Workflow }>("/api/workflows", { ...currentWorkflow, previous_id: savedWorkflowId });
    setWorkflowId(saved.workflow.id);
    setSavedWorkflowId(saved.workflow.id);
    setWorkflowName(saved.workflow.name);
    await loadWorkflows();
  }

  async function compileWorkflow() {
    if (hasCompileErrors) {
      setCompileIssues(localCompileIssues);
      setCompileStatus("图中存在错误，不能生成 YAML");
      setPage("flow");
      return;
    }
    const output = await apiPost<CompileOutput>("/api/workflows/compile", currentWorkflow);
    setCompileOutput(output);
    setCompileStatus("已编译");
    setPage("run");
  }

  async function startRun(flow: Workflow = currentWorkflow) {
    const job = await apiPost<RunJob>("/api/run/start", { workflow: flow, account_limit: accountLimit || undefined, proxy });
    setRunJob(job);
    const stream = new EventSource(`${API}/api/run/stream?id=${job.id}`);
    stream.onmessage = (event) => {
      const status = JSON.parse(event.data) as RunJob;
      setRunJob(status);
      if (["done", "error", "not_found"].includes(status.status)) {
        stream.close();
      }
    };
    stream.onerror = () => {
      stream.close();
    };
  }

  function loadWorkflow(flow: Workflow) {
    setWorkflowId(flow.id);
    setSavedWorkflowId(flow.id);
    setWorkflowName(flow.name);
    setNodes(cloneNodes(flow.nodes).map((node) => ({ ...node, data: { ...node.data, nodeId: node.id } })));
    setEdges(cloneEdges(flow.edges));
    setSelectedNodeId(flow.nodes[0]?.id || "");
    setPage("flow");
  }

  function runPreviewNodes(flow: Workflow = currentWorkflow): Node[] {
    return flow.nodes.map((node) => {
      const state =
        runJob?.failed_node === node.id
          ? "failed"
          : runJob?.active_node === node.id
            ? "running"
            : runJob?.completed_nodes?.includes(node.id)
              ? "done"
              : "idle";
      return {
        ...node,
        draggable: false,
        selectable: false,
        data: { ...node.data, nodeId: node.id, readonly: true, runState: state },
      };
    });
  }

  return (
    <main className={`app page-${page}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="mark">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M21.8 3.2 18.4 20c-.2 1.1-.9 1.4-1.8.9l-5-3.7-2.4 2.3c-.3.3-.5.5-1 .5l.4-5.1 9.3-8.4c.4-.4-.1-.6-.6-.3L5.8 13.4.9 11.9c-1.1-.3-1.1-1.1.2-1.6L20.2 3c.9-.3 1.7.2 1.6.2Z" />
            </svg>
          </div>
          <div>
            <strong>Local Workflow</strong>
            <span>Web only</span>
          </div>
        </div>
        <nav>
          <a className={page === "home" ? "active" : ""} href="/home" onClick={(event) => { event.preventDefault(); setPage("home"); }}>主页配置</a>
          <a href="/chat" onClick={(event) => { event.preventDefault(); setPage("chat"); }}>对话调试</a>
          <a className={page === "flow" ? "active" : ""} href="/flow" onClick={(event) => { event.preventDefault(); setPage("flow"); }}>编排流</a>
          <a className={page === "run" ? "active" : ""} href="/run" onClick={(event) => { event.preventDefault(); setPage("run"); }}>本地执行</a>
        </nav>
      </aside>

      <section className={page === "flow" ? "content flow-content" : page === "chat" ? "content chat-content" : "content"}>
        {page === "home" ? (
          <>
            <header className="topbar">
              <div>
                <h1>账号与小号池</h1>
                <p>所有账号导入、头像与名称读取都在 Web 页面发起；小号池来自本地 sessions/tg_session_strings.txt。</p>
              </div>
              <div className="actions">
                <Field label="代理" value={proxy} onChange={updateProxy} />
                <button className="primary" onClick={() => loadAccounts(true)}>刷新账号资料</button>
              </div>
            </header>
            <section className="grid two">
              <div className="panel">
                <div className="panel-head">
                  <h2>主账号</h2>
                  <button onClick={() => startQr("main")}>添加主账号</button>
                </div>
                <AccountList accounts={accounts} role="main" />
              </div>
              <div className="panel">
                <div className="panel-head">
                  <h2>小号 Pool</h2>
                  <button onClick={() => startQr("alt")}>添加小号</button>
                </div>
                <AccountList accounts={accounts} role="alt" />
              </div>
            </section>
            {qrOpen ? (
              <div className="modal-backdrop" onClick={() => setQrOpen(false)}>
                <div className="modal" onClick={(event) => event.stopPropagation()}>
                  <div className="panel-head">
                    <h2>扫码导入账号</h2>
                    <button onClick={() => setQrOpen(false)}>关闭</button>
                  </div>
                  <div className="qr-modal-body">
                    <span className="status">{qrJob?.status || "等待二维码"}</span>
                    {qrJob?.qr_image ? <img className="qr-image" src={qrJob.qr_image} alt="" /> : <div className="qr-placeholder">二维码生成中</div>}
                    {qrJob?.url ? <a href={qrJob.url}>{qrJob.url}</a> : null}
                    <p>使用 Telegram 移动端扫描二维码完成登录。</p>
                    {qrJob?.error ? <div className="error-box">{qrJob.error}</div> : null}
                  </div>
                </div>
              </div>
            ) : null}
          </>
        ) : null}

        {page === "chat" ? (
          <>
            <header className="chat-window-bar">
              <div>
                <strong>TG 对话调试</strong>
                <span>真实会话预览与发送测试</span>
              </div>
              <div className="actions">
                <Field label="代理" value={proxy} onChange={updateProxy} />
              </div>
            </header>
            <ChatDebugger accounts={accounts} proxy={proxy} onRefreshAccounts={() => loadAccounts(true, proxy)} />
          </>
        ) : null}

        {page === "flow" ? (
          <>
            <header className="topbar">
              <div>
                <h1>小号执行流编排</h1>
                <p>该图代表所有小号都会执行的流程，可串联多个对话、发送、解析和转发节点。</p>
              </div>
              <div className="actions">
                <button onClick={() => resetWorkflow()}>新建执行流</button>
                <button onClick={() => duplicateWorkflow()}>复制当前</button>
                <button onClick={saveWorkflow}>保存工作流</button>
                <button className="primary" onClick={compileWorkflow} disabled={hasCompileErrors}>生成 YAML</button>
              </div>
            </header>
            <section className="flow-shell">
              <div className="flow-toolbar">
                <Field label="工作流 ID" value={workflowId} onChange={setWorkflowId} />
                <Field label="工作流名称" value={workflowName} onChange={setWorkflowName} />
                <SelectField
                  label="调试账号"
                  value={flowAccount?.id || ""}
                  onChange={updateFlowAccount}
                  options={accounts.map((account) => ({ value: account.id, label: `${account.role === "main" ? "主账号" : "小号"} · ${account.name}` }))}
                />
                <div className="workflow-picker">
                  <span>复用执行流</span>
                  {workflows.length === 0 ? <em>暂无保存项</em> : null}
                  {workflows.map((flow) => (
                    <button key={flow.id} onClick={() => loadWorkflow(flow)} title={flow.id}>{flow.name || flow.id}</button>
                  ))}
                </div>
              </div>
              <div className="flow-canvas">
                <ReactFlow
                  nodes={nodes.map((node) => ({ ...node, data: { ...node.data, nodeId: node.id } }))}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  onConnectStart={(_, params) => {
                    connectingNodeId.current = params.nodeId;
                    setContextMenu(null);
                  }}
                  onConnectEnd={handleConnectEnd}
                  onEdgeClick={(_, edge) => {
                    setSelectedEdgeId(edge.id);
                    setSelectedNodeId("");
                    setContextMenu(null);
                  }}
                  onEdgeContextMenu={(event, edge) => {
                    event.preventDefault();
                    setSelectedEdgeId(edge.id);
                    setSelectedNodeId("");
                    setContextMenu({ kind: "edge", x: event.clientX, y: event.clientY, edgeId: edge.id });
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                  }}
                  onDrop={handleDrop}
                  onNodeClick={(_, node) => setSelectedNodeId(node.id)}
                  onNodeContextMenu={(event, node) => {
                    event.preventDefault();
                    setSelectedNodeId(node.id);
                    setContextMenu({ kind: "node", x: event.clientX, y: event.clientY, nodeId: node.id });
                  }}
                  onPaneContextMenu={(event) => {
                    event.preventDefault();
                    setContextMenu({
                      kind: "pane",
                      x: event.clientX,
                      y: event.clientY,
                      position: screenToFlowPosition({ x: event.clientX, y: event.clientY }),
                    });
                  }}
                  onPaneClick={() => {
                    if (suppressNextPaneClick.current) return;
                    setContextMenu(null);
                    setSelectedNodeId("");
                    setSelectedEdgeId("");
                  }}
                  nodeTypes={nodeTypes}
                  connectionRadius={34}
                  fitView
                  colorMode="dark"
                >
                  <MiniMap />
                  <Controls />
                  <Background />
                </ReactFlow>
              </div>
              <aside className="panel inspector">
                {selectedNode ? (
                  <>
                    <div className={hasCompileErrors ? "compile-status bad" : compileIssues.length ? "compile-status warn" : "compile-status ok"}>
                      <strong>{compileStatus}</strong>
                      {compileIssues.length ? (
                        <div className="compile-issues">
                          {compileIssues.map((issue, index) => (
                            <button
                              key={`${issue.nodeId || "flow"}-${index}`}
                              className={issue.level}
                              onClick={() => issue.nodeId && setSelectedNodeId(issue.nodeId)}
                            >
                              {issue.message}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <NodeEditor
                      node={selectedNode}
                      updateNode={updateNode}
                      botCommands={flowBotCommands}
                      commandPeer={selectedSendPeer}
                      commandStatus={flowCommandStatus}
                      dialogs={flowDialogs}
                      dialogsStatus={flowDialogsStatus}
                      onLoadDialogs={loadFlowDialogs}
                    />
                    <button onClick={() => selectedNodeId && setNodes((items) => items.filter((node) => node.id !== selectedNodeId))}>删除节点</button>
                  </>
                ) : (
                  <>
                    <div className={hasCompileErrors ? "compile-status bad" : compileIssues.length ? "compile-status warn" : "compile-status ok"}>
                      <strong>{compileStatus}</strong>
                      {compileIssues.length ? (
                        <div className="compile-issues">
                          {compileIssues.map((issue, index) => (
                            <button
                              key={`${issue.nodeId || "flow"}-${index}`}
                              className={issue.level}
                              onClick={() => issue.nodeId && setSelectedNodeId(issue.nodeId)}
                            >
                              {issue.message}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <NodePalette onAdd={addNode} />
                  </>
                )}
              </aside>
              {contextMenu ? (
                <div className="context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onContextMenu={(event) => event.preventDefault()}>
                  {contextMenu.kind === "node" ? (
                    <button
                      onClick={() => {
                        setNodes((items) => items.filter((node) => node.id !== contextMenu.nodeId));
                        setEdges((items) => items.filter((edge) => edge.source !== contextMenu.nodeId && edge.target !== contextMenu.nodeId));
                        setContextMenu(null);
                      }}
                    >
                      删除节点
                    </button>
                  ) : null}
                  {contextMenu.kind === "edge" ? (
                    <button
                      onClick={() => {
                        setEdges((items) => items.filter((edge) => edge.id !== contextMenu.edgeId));
                        setSelectedEdgeId("");
                        setContextMenu(null);
                      }}
                    >
                      删除连线
                    </button>
                  ) : null}
                  {contextMenu.kind === "pane" ? (
                    <>
                      <button onClick={() => addNodeAt("task", contextMenu.position)}>创建任务定义</button>
                      <button onClick={() => addNodeAt("open", contextMenu.position)}>创建打开对话</button>
                      <button onClick={() => addNodeAt("send", contextMenu.position)}>创建发送消息</button>
                      <button onClick={() => addNodeAt("parse", contextMenu.position)}>创建解析/提取</button>
                      <button onClick={() => addNodeAt("forward", contextMenu.position)}>创建转发消息</button>
                      <button onClick={() => addNodeAt("join", contextMenu.position)}>创建添加账号/群组</button>
                      <button onClick={() => addNodeAt("link", contextMenu.position)}>创建打开链接</button>
                    </>
                  ) : null}
                  {contextMenu.kind === "connect" ? (
                    <>
                      {contextMenu.replaceExisting ? <div className="context-menu-hint">该节点已有出线，创建后会替换当前出线</div> : null}
                      {contextMenu.options.map((type) => {
                        const item = nodeCatalog.find((catalogItem) => catalogItem.type === type);
                        return (
                          <button key={type} onClick={() => addConnectedNode(type, contextMenu.position, contextMenu.sourceId, contextMenu.replaceExisting)}>
                            创建{item?.title || type}
                          </button>
                        );
                      })}
                    </>
                  ) : null}
                </div>
              ) : null}
            </section>
          </>
        ) : null}

        {page === "run" ? (
          <>
            <header className="topbar">
              <div>
                <h1>本地执行工作流</h1>
                <p>选择保存的工作流本地执行，也可以直接执行当前编排。执行使用小号池中的所有账号。</p>
              </div>
              <button className="primary" onClick={() => startRun()}>执行当前编排</button>
            </header>
            <section className="grid two">
              <div className="panel">
                <div className="panel-head">
                  <h2>已保存工作流</h2>
                  <button onClick={loadWorkflows}>刷新</button>
                </div>
                <div className="workflow-list">
                  {workflows.map((flow) => (
                    <article className="workflow-item" key={flow.id}>
                      <div>
                        <b>{flow.name}</b>
                        <span>{flow.id}</span>
                      </div>
                      <button onClick={() => loadWorkflow(flow)}>编辑</button>
                      <button onClick={() => startRun(flow)}>执行</button>
                    </article>
                  ))}
                </div>
              </div>
              <div className="panel">
                <div className="panel-head">
                  <h2>运行状态</h2>
                  <span className="status">{runJob?.status || "未运行"}</span>
                </div>
                <Field label="账号数量 0 为全部" type="number" value={accountLimit} onChange={updateAccountLimit} />
                <div className="save-hint">{accountLimitSavedAt}</div>
                <pre className="code" ref={runOutputRef}>{runJob?.output || runJob?.error || "暂无输出"}</pre>
              </div>
            </section>
            <section className="panel run-flow-panel">
              <div className="panel-head">
                <h2>只读执行图</h2>
              </div>
              <div className="run-flow-canvas">
                <ReactFlow
                  nodes={runPreviewNodes()}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  elementsSelectable={false}
                  panOnDrag
                  zoomOnScroll
                  connectionRadius={34}
                  fitView
                  colorMode="dark"
                >
                  <MiniMap />
                  <Controls />
                  <Background />
                </ReactFlow>
              </div>
            </section>
            <section className="grid two">
              <div className="panel">
                <div className="panel-head">
                  <h2>signins.yml</h2>
                  <button onClick={compileWorkflow}>重新生成</button>
                </div>
                <pre className="code large">{compileOutput.signins || "在编排流页面点击生成 YAML"}</pre>
              </div>
              <div className="panel">
                <div className="panel-head">
                  <h2>tasks.yml</h2>
                </div>
                <pre className="code large">{compileOutput.tasks || "在编排流页面点击生成 YAML"}</pre>
              </div>
              <div className="panel">
                <div className="panel-head">
                  <h2>.github/workflows/tg-orchestrator.yml</h2>
                </div>
                <pre className="code large">{compileOutput.github || "预览或生成 YAML 后显示当前 GitHub Actions 入口"}</pre>
              </div>
            </section>
          </>
        ) : null}
      </section>
    </main>
  );
}

export function App() {
  return (
    <ReactFlowProvider>
      <WorkflowApp />
    </ReactFlowProvider>
  );
}
