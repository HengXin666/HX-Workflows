import { useMemo, useState, type DragEvent } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import {
  Bot, Braces, Clock3, Copy, CreditCard, Database, GitBranch, Globe, Image,
  Link, Menu, MessageSquare, Play, Plus, Send, ShieldCheck, ShoppingBag,
  Square, Trash2, UserCog, Users, Webhook,
} from "lucide-react";
import {
  BOT_NODE_CATALOG,
  createNodeData,
  deleteBlueprintElement,
  validateNodeData,
  type BlueprintData,
  type BlueprintGraph,
  type BlueprintValue,
  type BotNodeSpec,
} from "../lib/botBlueprintCatalog";

export { blueprintFromSteps, defaultBotBlueprint } from "../lib/botBlueprintCatalog";
export type { BlueprintData, BlueprintGraph } from "../lib/botBlueprintCatalog";

const icons: Record<string, typeof Bot> = {
  command: Play, message: MessageSquare, callback: Link, member: Users, schedule: Clock3,
  inline: Globe, send: Send, media: Image, menu: Menu, edit: Braces, forward: Copy,
  webapp: Globe, condition: GitBranch, parse: Braces, delay: Clock3, random: GitBranch,
  end: Square, checkin: ShieldCheck, database: Database, group: UserCog, invoice: CreditCard,
  payment_event: CreditCard, subscription: ShoppingBag, http: Globe, api: Bot, webhook: Webhook,
};

const fieldLabels: Record<string, string> = {
  commands: "匹配命令", scope: "生效会话", message_types: "消息类型", pattern: "匹配规则",
  answer_immediately: "立即响应按钮", events: "成员事件", cron: "Cron 表达式", timezone: "时区",
  query_pattern: "查询匹配", cache_time: "缓存秒数", text: "消息文本", parse_mode: "解析格式",
  target: "发送目标", media_type: "媒体类型", source: "媒体来源", caption: "媒体说明",
  buttons: "菜单按钮", operation: "操作", message_id: "消息 ID",
  mode: "模式", target_chat_id: "目标会话", button_text: "按钮文本", url: "URL",
  expression: "判断表达式", save_as: "保存变量名", seconds: "等待秒数",
  cooldown_key: "冷却键", on_cooldown: "冷却中行为", weights: "分支权重", reason: "结束原因",
  reward_points: "签到奖励积分", challenge_ttl_seconds: "题目有效秒数", wrong_policy: "答错策略",
  difficulty: "验证码难度（easy / normal / hard）",
  max_attempts: "最多尝试（0=不限）", table: "数据表", fields: "字段 / SQL 语义",
  action: "群管理动作", target_user_id: "目标用户", duration_seconds: "持续秒数",
  title: "商品标题", description: "商品说明", price_xtr: "Stars 价格（XTR）", payload: "业务 Payload",
  expected_payload: "期望 Payload", credit_field: "入账字段", credit_amount: "入账数量",
  plan: "订阅套餐", grace_seconds: "宽限秒数", method: "请求 Method", body: "JSON Body",
  timeout_seconds: "超时秒数", params: "JSON 参数", event_name: "事件名称", secret: "签名密钥",
};

function FlowNode({ data, selected }: NodeProps<Node<BlueprintData>>) {
  const Icon = icons[data.kind] || Bot;
  const errors = validateNodeData(data);
  return (
    <div className={`blueprint-node kind-${data.kind}${selected ? " selected" : ""}${errors.length ? " invalid" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="blueprint-title"><Icon /><div><small>{data.category}</small><strong>{data.label}</strong></div></div>
      <span>{data.detail}</span>
      <code className="node-runtime">{data.runtime}</code>
      {errors.length ? <small className="node-error">缺少必填配置</small> : null}
      {(data.branches || []).map((branch, index) => (
        <div className="branch-port" key={`${branch}-${index}`}><small>{branch}</small><Handle id={`branch-${index}`} type="source" position={Position.Right} /></div>
      ))}
      {!data.branches?.length && data.kind !== "end" ? <Handle type="source" position={Position.Right} /> : null}
    </div>
  );
}

const nodeTypes = { blueprint: FlowNode };

export function BotBlueprint({ graph, onChange }: { graph: BlueprintGraph; onChange: (graph: BlueprintGraph) => void }) {
  const { screenToFlowPosition } = useReactFlow();
  const { nodes, edges } = graph;
  const [selected, setSelected] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number; kind: "node" | "edge" | "pane"; id?: string; position?: { x: number; y: number } } | null>(null);
  const current = useMemo(() => nodes.find((node) => node.id === selected), [nodes, selected]);
  const errors = current ? validateNodeData(current.data) : [];

  function updateNode(id: string, patch: Partial<BlueprintData>) {
    onChange({ ...graph, nodes: nodes.map((node) => node.id === id ? { ...node, data: { ...node.data, ...patch } } : node) });
  }

  function updateConfig(id: string, key: string, value: BlueprintValue) {
    const node = nodes.find((candidate) => candidate.id === id);
    if (node) {
      const patch: Partial<BlueprintData> = { config: { ...node.data.config, [key]: value } };
      if (key === "buttons" && Array.isArray(value)) {
        patch.branches = value.map((row, index) => typeof row === "object" && !Array.isArray(row) ? String(row.label || `按钮 ${index + 1}`) : String(row));
      }
      updateNode(id, patch);
    }
  }

  function add(item: BotNodeSpec, position = { x: 300, y: 160 }) {
    const id = `${item.kind}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    onChange({ ...graph, nodes: [...nodes, { id, type: "blueprint", position, data: createNodeData(item.kind) }] });
    setSelected(id);
    setMenu(null);
  }

  function remove(kind: "node" | "edge", id: string) {
    onChange(deleteBlueprintElement(graph, kind, id));
    if (kind === "node" && selected === id) setSelected("");
    setMenu(null);
  }

  function handleNodeChanges(changes: NodeChange[]) {
    const removed = changes.filter((change) => change.type === "remove").map((change) => change.id);
    let next = graph;
    for (const id of removed) next = deleteBlueprintElement(next, "node", id);
    const visualChanges = changes.filter((change) => change.type !== "remove");
    onChange({ ...next, nodes: applyNodeChanges(visualChanges, next.nodes) as typeof nodes });
  }

  function handleEdgeChanges(changes: EdgeChange[]) {
    onChange({ ...graph, edges: applyEdgeChanges(changes, edges) });
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    const kind = event.dataTransfer.getData("application/bot-node");
    const item = BOT_NODE_CATALOG.find((candidate) => candidate.kind === kind);
    if (item) add(item, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  }

  const groups = ["触发", "消息", "逻辑", "数据", "支付", "扩展"];
  return (
    <section className="blueprint-shell" onClick={() => setMenu(null)}>
      <aside className="blueprint-palette">
        <h3>机器人节点</h3>
        {groups.map((group) => <div className="palette-group" key={group}>
          <span>{group}</span>
          {BOT_NODE_CATALOG.filter((item) => item.category === group).map((item) => {
            const Icon = icons[item.kind] || Bot;
            return <button key={item.kind} draggable onDragStart={(event) => { event.dataTransfer.setData("application/bot-node", item.kind); event.dataTransfer.effectAllowed = "copy"; }} onClick={() => add(item)} title={`${item.input} → ${item.output}`}>
              <Icon /><span><b>{item.label}</b><small>{item.detail}</small><em>{item.runtime}</em></span>
            </button>;
          })}
        </div>)}
      </aside>

      <div className="blueprint-canvas" onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }} onDrop={drop}>
        <ReactFlow
          nodes={nodes} edges={edges} nodeTypes={nodeTypes}
          onNodesChange={handleNodeChanges} onEdgesChange={handleEdgeChanges}
          onConnect={(connection: Connection) => onChange({ ...graph, edges: addEdge(connection, edges) })}
          onNodeClick={(_, node) => setSelected(node.id)}
          onNodeContextMenu={(event, node) => { event.preventDefault(); setMenu({ x: event.clientX, y: event.clientY, kind: "node", id: node.id }); }}
          onEdgeContextMenu={(event, edge) => { event.preventDefault(); setMenu({ x: event.clientX, y: event.clientY, kind: "edge", id: edge.id }); }}
          onPaneContextMenu={(event) => { event.preventDefault(); setMenu({ x: event.clientX, y: event.clientY, kind: "pane", position: screenToFlowPosition({ x: event.clientX, y: event.clientY }) }); }}
          fitView colorMode="dark"
        ><MiniMap /><Controls /><Background /></ReactFlow>
      </div>

      <aside className="blueprint-inspector">
        <h3>节点配置</h3>
        {current ? <>
          <div className="node-kind-card"><b>{current.data.category} · {current.data.kind}</b><strong>{current.data.detail}</strong><span><i>输入</i>{current.data.input}</span><span><i>输出</i>{current.data.output}</span><code>{current.data.runtime}</code></div>
          <label><span>节点名称</span><input value={current.data.label} onChange={(event) => updateNode(current.id, { label: event.target.value })} /></label>
          {Object.entries(current.data.config).map(([key, value]) => <label key={key}>
            <span>{fieldLabels[key] || key}{current.data.required.includes(key) ? " *" : ""}</span>
            {Array.isArray(value)
              ? <div className="config-list-editor">{value.map((row, index) => <div className="config-list-row" key={index}>
                {typeof row === "object" && !Array.isArray(row)
                  ? <input aria-label={`${key} 第 ${index + 1} 行名称`} value={String(row.label || "")} placeholder="按钮名称" onChange={(event) => { const next = [...value]; next[index] = { label: event.target.value }; updateConfig(current.id, key, next); }} />
                  : <input aria-label={`${key} 第 ${index + 1} 行`} type={typeof row === "number" ? "number" : "text"} value={String(row)} onChange={(event) => { const next = [...value]; next[index] = typeof row === "number" ? Number(event.target.value) : event.target.value; updateConfig(current.id, key, next); }} />}
                <button type="button" aria-label={`删除第 ${index + 1} 行`} onClick={() => updateConfig(current.id, key, value.filter((_, rowIndex) => rowIndex !== index))}><Trash2 /></button>
              </div>)}<button type="button" className="add-config-row" onClick={() => { const sample = value[0]; const nextRow: BlueprintValue = key === "buttons" || (typeof sample === "object" && !Array.isArray(sample)) ? { label: `按钮 ${value.length + 1}` } : key === "weights" || typeof sample === "number" ? 0 : ""; updateConfig(current.id, key, [...value, nextRow]); }}><Plus />新增一行</button></div>
              : typeof value === "boolean"
              ? <input type="checkbox" checked={value} onChange={(event) => updateConfig(current.id, key, event.target.checked)} />
              : typeof value === "number"
                ? <input type="number" value={value} onChange={(event) => updateConfig(current.id, key, Number(event.target.value))} />
                : key === "difficulty"
                  ? <select value={String(value)} onChange={(event) => updateConfig(current.id, key, event.target.value)}><option value="easy">简单 · 1–10 加法</option><option value="normal">普通 · 加减法</option><option value="hard">困难 · 乘法</option></select>
                : String(value).length > 42 || String(value).includes("\n")
                  ? <textarea value={String(value)} onChange={(event) => updateConfig(current.id, key, event.target.value)} />
                  : <input value={String(value)} onChange={(event) => updateConfig(current.id, key, event.target.value)} />}
          </label>)}
          <div className="node-semantics"><b>{current.data.branches?.length ? `分支出口：${current.data.branches.join(" / ")}` : "完成后从右侧出口继续"}</b>{errors.map((error) => <span className="node-error" key={error}>{error}</span>)}</div>
          <button className="danger" onClick={() => remove("node", current.id)}><Trash2 />删除节点</button>
        </> : <p>选择节点后，这里会显示它真实接收什么、产出什么、调用哪个运行时，以及所有必填配置。</p>}
      </aside>

      {menu ? <div className="context-menu" style={{ left: menu.x, top: menu.y }} onClick={(event) => event.stopPropagation()}>
        {menu.kind !== "pane" ? <>
          <button onClick={() => menu.id && remove(menu.kind === "node" ? "node" : "edge", menu.id)}><Trash2 /> 删除{menu.kind === "node" ? "节点" : "连线"}</button>
          {menu.kind === "node" ? <button onClick={() => { const node = nodes.find((candidate) => candidate.id === menu.id); if (node) add({ ...node.data, label: `${node.data.label} 副本` }, { x: node.position.x + 40, y: node.position.y + 40 }); }}><Copy /> 复制节点</button> : null}
        </> : BOT_NODE_CATALOG.slice(0, 10).map((item) => <button key={item.kind} onClick={() => add(item, menu.position)}><Plus /> 创建{item.label}</button>)}
      </div> : null}
    </section>
  );
}
