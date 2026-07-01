import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type CompositionEvent } from "react";
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
    data: { nodeId: "parse-a", kind: "parse", label: "解析回执", pattern: "签到成功|积分", regex: true, limit: 5, saveAs: "last_parse" },
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
            ? String(nodeData.pattern || "")
            : kind === "forward"
              ? String(nodeData.toPeer || "")
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
        ) : null}
        {kind === "forward" ? <NodeTextInput value={String(nodeData.toPeer || "")} onCommit={(value) => emitText("toPeer", value)} /> : null}
        {kind === "link" ? <NodeTextInput value={String(nodeData.url || "")} onCommit={(value) => emitText("url", value)} /> : null}
      </div> : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { default: FlowNode };

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
}: {
  node?: Node;
  updateNode: (id: string, data: Record<string, unknown>) => void;
  botCommands?: BotCommand[];
  commandPeer?: string;
  commandStatus?: string;
}) {
  if (!node) return <div className="empty">选择一个节点进行配置</div>;
  const data = node.data as Record<string, unknown>;
  const kind = String(data.kind || "task");
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
          <Field label="判断表达式" value={String(data.pattern || "")} onChange={(value) => set("pattern", value)} />
          <Field label="读取消息数" type="number" value={Number(data.limit || 5)} onChange={(value) => updateNode(node.id, { limit: Number(value) || 5 })} />
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
      {kind === "forward" ? (
        <>
          <Field label="转发到会话" value={String(data.toPeer || "")} onChange={(value) => set("toPeer", value)} />
          <Field label="来源变量" value={String(data.source || "last_parse")} onChange={(value) => set("source", value)} />
        </>
      ) : null}
      {kind === "link" ? <Field label="链接" value={String(data.url || "")} onChange={(value) => set("url", value)} /> : null}
    </div>
  );
}

function WorkflowApp() {
  const { screenToFlowPosition } = useReactFlow();
  const [page, setPageState] = useState<Page>(() => pageFromPath(window.location.pathname));
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [qrJob, setQrJob] = useState<QrJob | null>(null);
  const [qrOpen, setQrOpen] = useState(false);
  const [proxy, setProxyState] = useState(() => localStorage.getItem("tg.proxy") || "http://127.0.0.1:2334");
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  const [edges, setEdges] = useState<Edge[]>(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string>("task");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowName, setWorkflowName] = useState("每日签到");
  const [compileOutput, setCompileOutput] = useState<CompileOutput>({ signins: "", tasks: "" });
  const [runJob, setRunJob] = useState<RunJob | null>(null);
  const [accountLimit, setAccountLimit] = useState(() => Number(localStorage.getItem("tg.accountLimit") || "0"));
  const [accountLimitSavedAt, setAccountLimitSavedAt] = useState(() => (localStorage.getItem("tg.accountLimit") ? "已加载上次设置" : "0 表示全部账号"));
  const [flowBotCommands, setFlowBotCommands] = useState<BotCommand[]>([]);
  const [flowCommandStatus, setFlowCommandStatus] = useState("");
  const [contextMenu, setContextMenu] = useState<
    | { kind: "node"; x: number; y: number; nodeId: string }
    | { kind: "pane"; x: number; y: number; position: { x: number; y: number } }
    | null
  >(null);

  const selectedNode = useMemo(() => nodes.find((node) => node.id === selectedNodeId), [nodes, selectedNodeId]);
  const currentWorkflow = useMemo<Workflow>(
    () => ({ id: String((nodes[0]?.data as Record<string, unknown>)?.taskId || "daily-sign"), name: workflowName, nodes, edges }),
    [edges, nodes, workflowName],
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
    const onPopState = () => setPageState(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    if (window.location.pathname === "/") {
      window.history.replaceState({ page: "home" }, "", "/home");
    }
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

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
    const commandAccount = accounts.find((account) => account.role === "alt") || accounts.find((account) => account.role === "main") || accounts[0];
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
  }, [accounts, proxy, selectedNode, selectedSendPeer]);

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
  const onConnect = useCallback((connection: Connection) => setEdges((items) => addEdge(connection, items)), []);

  function addNode(type: string) {
    const id = `${type}-${Date.now()}`;
    const labels: Record<string, string> = { open: "打开对话", send: "发送消息", parse: "解析回执", forward: "转发消息", link: "打开链接" };
    setNodes((items) => [
      ...items,
      {
        id,
        type: "default",
        position: { x: 160 + items.length * 60, y: 220 + items.length * 24 },
        data: { kind: type, label: labels[type], peer: "@example_bot", sendMode: "text", command: "", text: "签到", pattern: "签到成功|积分", toPeer: "me", url: "" },
      },
    ]);
    setSelectedNodeId(id);
  }

  function addNodeAt(type: string, position: { x: number; y: number }) {
    const id = `${type}-${Date.now()}`;
    const labels: Record<string, string> = { open: "打开对话", send: "发送消息", parse: "解析回执", forward: "转发消息", link: "打开链接" };
    setNodes((items) => [
      ...items,
      {
        id,
        type: "default",
        position,
        data: { nodeId: id, kind: type, label: labels[type], peer: "@example_bot", sendMode: "text", command: "", text: "签到", pattern: "签到成功|积分", regex: type === "parse", toPeer: "me", url: "" },
      },
    ]);
    setSelectedNodeId(id);
    setContextMenu(null);
  }

  function updateNode(id: string, patch: Record<string, unknown>) {
    setNodes((items) =>
      items.map((node) => (node.id === id ? { ...node, data: { ...node.data, nodeId: node.id, ...patch } } : { ...node, data: { ...node.data, nodeId: node.id } })),
    );
  }

  async function saveWorkflow() {
    const saved = await apiPost<{ workflow: Workflow }>("/api/workflows", currentWorkflow);
    setWorkflowName(saved.workflow.name);
    await loadWorkflows();
  }

  async function compileWorkflow() {
    const output = await apiPost<CompileOutput>("/api/workflows/compile", currentWorkflow);
    setCompileOutput(output);
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
    setWorkflowName(flow.name);
    setNodes(flow.nodes);
    setEdges(flow.edges);
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
                <button onClick={saveWorkflow}>保存工作流</button>
                <button className="primary" onClick={compileWorkflow}>生成 YAML</button>
              </div>
            </header>
            <section className="flow-shell">
              <div className="flow-toolbar">
                <Field label="工作流名称" value={workflowName} onChange={setWorkflowName} />
                <button onClick={() => addNode("open")}>打开对话</button>
                <button onClick={() => addNode("send")}>发送消息</button>
                <button onClick={() => addNode("parse")}>解析回执</button>
                <button onClick={() => addNode("forward")}>转发消息</button>
                <button onClick={() => addNode("link")}>打开链接</button>
              </div>
              <div className="flow-canvas">
                <ReactFlow
                  nodes={nodes.map((node) => ({ ...node, data: { ...node.data, nodeId: node.id } }))}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
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
                  onPaneClick={() => setContextMenu(null)}
                  nodeTypes={nodeTypes}
                  fitView
                  colorMode="dark"
                >
                  <MiniMap />
                  <Controls />
                  <Background />
                </ReactFlow>
              </div>
              <aside className="panel inspector">
                <NodeEditor
                  node={selectedNode}
                  updateNode={updateNode}
                  botCommands={flowBotCommands}
                  commandPeer={selectedSendPeer}
                  commandStatus={flowCommandStatus}
                />
                <button onClick={() => selectedNodeId && setNodes((items) => items.filter((node) => node.id !== selectedNodeId))}>删除节点</button>
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
                  ) : (
                    <>
                      <button onClick={() => addNodeAt("open", contextMenu.position)}>创建打开对话</button>
                      <button onClick={() => addNodeAt("send", contextMenu.position)}>创建发送消息</button>
                      <button onClick={() => addNodeAt("parse", contextMenu.position)}>创建解析回执</button>
                      <button onClick={() => addNodeAt("forward", contextMenu.position)}>创建转发消息</button>
                      <button onClick={() => addNodeAt("link", contextMenu.position)}>创建打开链接</button>
                    </>
                  )}
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
                <pre className="code">{runJob?.output || runJob?.error || "暂无输出"}</pre>
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
