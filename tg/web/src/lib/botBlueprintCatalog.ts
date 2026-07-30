import type { Edge, Node } from "@xyflow/react";

export type BlueprintScalar = string | number | boolean;
export type BlueprintValue = BlueprintScalar | BlueprintValue[] | { [key: string]: BlueprintValue };
export type BlueprintData = {
  label: string;
  kind: string;
  detail: string;
  category: string;
  branches?: string[];
  config: Record<string, BlueprintValue>;
  required: string[];
  input: string;
  output: string;
  runtime: string;
};
export type BlueprintGraph = { nodes: Node<BlueprintData>[]; edges: Edge[] };

export type BotNodeSpec = Omit<BlueprintData, "config"> & {
  config: Record<string, BlueprintValue>;
};

export type BlueprintTemplate = {
  id: string;
  name: string;
  description: string;
  graph: BlueprintGraph;
};

export const BOT_NODE_CATALOG: BotNodeSpec[] = [
  { kind: "command", label: "命令入口", detail: "匹配 /start、/menu 等命令", category: "触发", config: { commands: ["/start"], scope: ["private", "group"] }, required: ["commands"], input: "Telegram message.text", output: "命令、参数、用户与会话", runtime: "Update.message → CommandHandler" },
  { kind: "message", label: "消息入口", detail: "接收文本、图片、文件或位置", category: "触发", config: { message_types: ["text", "photo", "document", "location"] }, required: ["message_types"], input: "Telegram message", output: "标准化消息与发送者", runtime: "Update.message → MessageHandler" },
  { kind: "callback", label: "按钮回调", detail: "匹配 Inline Keyboard 的 callback_data", category: "触发", config: { pattern: "menu:*", answer_immediately: true }, required: ["pattern"], input: "callback_query", output: "callback_data、消息与用户", runtime: "Update.callback_query" },
  { kind: "member", label: "成员事件", detail: "监听入群、退群与权限变化", category: "触发", config: { events: ["joined", "left", "restricted", "administrator"] }, required: ["events"], input: "chat_member update", output: "成员、旧状态与新状态", runtime: "Update.chat_member" },
  { kind: "schedule", label: "定时触发", detail: "按 Cron 时间主动开始分支", category: "触发", config: { cron: "0 9 * * *", timezone: "Asia/Shanghai" }, required: ["cron", "timezone"], input: "调度器时钟", output: "计划时间与本次任务 ID", runtime: "Persistent scheduler" },
  { kind: "inline", label: "Inline 查询", detail: "处理用户输入的 @bot query", category: "触发", config: { query_pattern: ".*", cache_time: 10 }, required: ["query_pattern"], input: "inline_query", output: "查询文本与用户", runtime: "answerInlineQuery" },

  { kind: "send", label: "发送消息", detail: "向当前或指定会话发送格式化文本", category: "消息", config: { text: "请输入消息内容", parse_mode: "HTML", target: "current_chat" }, required: ["text", "target"], input: "文本、模板变量与目标会话", output: "sent_message 与 message_id", runtime: "Bot API · sendMessage" },
  { kind: "media", label: "发送媒体", detail: "发送图片、视频、文件或相册", category: "消息", config: { media_type: "photo", source: "https://example.com/image.jpg", caption: "图片说明" }, required: ["media_type", "source"], input: "媒体 URL/file_id 与说明", output: "sent_message", runtime: "sendPhoto / sendVideo / sendDocument" },
  { kind: "menu", label: "按钮菜单", detail: "显示按钮，并从每个按钮出口继续", category: "消息", branches: ["按钮 1", "按钮 2", "返回"], config: { text: "请选择操作", buttons: [{ label: "按钮 1" }, { label: "按钮 2" }, { label: "返回" }] }, required: ["text", "buttons"], input: "按钮名称；内部路由 ID 由系统生成", output: "InlineKeyboardMarkup", runtime: "sendMessage + inline_keyboard" },
  { kind: "edit", label: "编辑或删除消息", detail: "更新菜单文本、按钮，或清理旧消息", category: "消息", config: { operation: "edit", message_id: "{{trigger.message_id}}", text: "已更新" }, required: ["operation", "message_id"], input: "chat_id、message_id 与新内容", output: "更新后的消息或删除结果", runtime: "editMessageText / deleteMessage" },
  { kind: "forward", label: "转发或复制消息", detail: "把一条消息发送到另一个会话", category: "消息", config: { mode: "copy", target_chat_id: "{{context.target_chat_id}}", message_id: "{{trigger.message_id}}" }, required: ["mode", "target_chat_id", "message_id"], input: "来源消息与目标 chat_id", output: "新消息 ID", runtime: "copyMessage / forwardMessage" },
  { kind: "webapp", label: "打开 Mini App", detail: "发送可打开 Telegram Web App 的按钮", category: "消息", config: { button_text: "打开应用", url: "https://example.com/app" }, required: ["button_text", "url"], input: "HTTPS Web App URL", output: "web_app 按钮", runtime: "InlineKeyboardButton.web_app" },

  { kind: "condition", label: "条件判断", detail: "按用户、会话或上游字段选择分支", category: "逻辑", branches: ["符合", "不符合"], config: { expression: "{{user.points}} >= 100" }, required: ["expression"], input: "上下文与布尔表达式", output: "符合 / 不符合", runtime: "Safe expression evaluator" },
  { kind: "parse", label: "解析输入", detail: "用正则提取文本并保存为变量", category: "逻辑", config: { pattern: "^ref_(\\d+)$", save_as: "inviter_id" }, required: ["pattern", "save_as"], input: "消息文本或 callback_data", output: "命名变量", runtime: "Regex parser" },
  { kind: "delay", label: "等待或冷却", detail: "延迟执行，或限制用户重复操作", category: "逻辑", config: { seconds: 3, cooldown_key: "{{user.id}}:action", on_cooldown: "等待剩余时间后重试" }, required: ["seconds", "cooldown_key"], input: "用户、冷却键与时间", output: "可继续时间", runtime: "Persistent cooldown in SQLite" },
  { kind: "random", label: "随机分支", detail: "按权重随机选择一个出口", category: "逻辑", branches: ["A", "B"], config: { weights: [50, 50] }, required: ["weights"], input: "与出口对应的权重列表", output: "被选中的分支", runtime: "Weighted random router" },
  { kind: "end", label: "结束分支", detail: "停止当前更新的执行链", category: "逻辑", config: { reason: "流程已完成" }, required: [], input: "任意上下文", output: "终止状态", runtime: "Stop propagation" },

  { kind: "checkin", label: "验证码签到", detail: "生成四选一计算题；答错重出，答对入库", category: "数据", branches: ["正确", "错误", "过期"], config: { reward_points: 10, challenge_ttl_seconds: 120, difficulty: "normal", wrong_policy: "regenerate", max_attempts: 0 }, required: ["reward_points", "challenge_ttl_seconds", "difficulty", "wrong_policy"], input: "用户 ID 与按钮答案", output: "签到记录、积分、连续天数", runtime: "SQLite checkin_challenges + checkins" },
  { kind: "database", label: "用户数据", detail: "在 SQLite 查询或持久化积分、库存和关系", category: "数据", config: { operation: "upsert", table: "users", fields: "points={{user.points}}" }, required: ["operation", "table", "fields"], input: "参数化字段与当前用户", output: "记录、影响行数或聚合结果", runtime: "SQLite transaction" },
  { kind: "group", label: "群组管理", detail: "执行禁言、移除、置顶或入群审核", category: "数据", config: { action: "restrict", target_user_id: "{{trigger.user_id}}", duration_seconds: 3600 }, required: ["action", "target_user_id"], input: "chat_id、用户 ID 与权限", output: "Bot API 操作结果", runtime: "restrictChatMember / banChatMember / pinChatMessage" },
  { kind: "invoice", label: "创建 Stars 发票", detail: "生成 Telegram Stars 支付发票并等待付款", category: "支付", branches: ["发票已发送", "发送失败"], config: { title: "积分套餐", description: "购买 100 积分", price_xtr: 50, payload: "points_100" }, required: ["title", "description", "price_xtr", "payload"], input: "商品、XTR 金额与业务 payload", output: "invoice message 与 payload", runtime: "Bot API · sendInvoice (currency=XTR)" },
  { kind: "payment_event", label: "确认支付结果", detail: "校验预结账并处理 successful_payment", category: "支付", branches: ["支付成功", "校验失败"], config: { expected_payload: "points_100", credit_field: "points", credit_amount: 100 }, required: ["expected_payload", "credit_field", "credit_amount"], input: "pre_checkout_query / successful_payment", output: "付款人、金额、charge_id 与入账结果", runtime: "answerPreCheckoutQuery + SQLite idempotency" },
  { kind: "subscription", label: "订阅状态校验", detail: "从 SQLite 校验套餐与到期时间", category: "支付", branches: ["订阅有效", "已过期", "未订阅"], config: { plan: "vip_monthly", grace_seconds: 0, table: "subscriptions" }, required: ["plan", "table"], input: "用户 ID、套餐与当前时间", output: "订阅状态与 expires_at", runtime: "SQLite subscriptions lookup" },

  { kind: "http", label: "HTTP 请求", detail: "调用外部 JSON API 并保存响应", category: "扩展", config: { method: "POST", url: "https://example.com/api", body: "{}", timeout_seconds: 10 }, required: ["method", "url"], input: "URL、Headers 与 JSON Body", output: "status、headers 与 JSON", runtime: "Server-side HTTP client" },
  { kind: "api", label: "原始 Bot API", detail: "调用任意 Telegram Bot API method", category: "扩展", config: { method: "getMe", params: "{}" }, required: ["method", "params"], input: "method 与 JSON 参数", output: "Telegram API result", runtime: "https://api.telegram.org/bot…/{method}" },
  { kind: "webhook", label: "Webhook 事件", detail: "接收并校验外部系统回调", category: "扩展", config: { event_name: "order.paid", secret: "change-me" }, required: ["event_name", "secret"], input: "签名、Headers 与 JSON Body", output: "已验证的事件上下文", runtime: "Authenticated HTTP webhook" },
];

const specs = new Map(BOT_NODE_CATALOG.map((item) => [item.kind, item]));

export function nodeSpec(kind: string): BotNodeSpec {
  const spec = specs.get(kind);
  if (!spec) throw new Error(`未知机器人节点：${kind}`);
  return spec;
}

export function createNodeData(kind: string, patch: Partial<BlueprintData> = {}): BlueprintData {
  const spec = nodeSpec(kind);
  const incoming = patch.config || {};
  const config = Object.fromEntries(Object.entries(spec.config).map(([key, defaultValue]) => {
    const value = incoming[key] ?? structuredClone(defaultValue);
    if (!Array.isArray(defaultValue) || Array.isArray(value)) return [key, value];
    const rows = String(value).split(/[\n,]+/).map((row) => row.trim()).filter(Boolean);
    if (defaultValue[0] && typeof defaultValue[0] === "object") {
      return [key, rows.map((row) => { const [label] = row.split("|"); return { label }; })];
    }
    return [key, rows.map((row) => typeof defaultValue[0] === "number" ? Number(row) : row)];
  }));
  if (kind === "menu" && Array.isArray(config.buttons)) {
    config.buttons = config.buttons.map((row) => typeof row === "object" && !Array.isArray(row) ? { label: String(row.label || "按钮") } : { label: String(row) });
  }
  if (kind === "send" && typeof config.text === "string") {
    config.text = config.text.replace("?start=ref_", "?start=inv_");
  }
  return {
    ...structuredClone(spec),
    ...patch,
    config: { ...structuredClone(spec.config), ...config },
    required: patch.required || [...spec.required],
    branches: patch.branches || (spec.branches ? [...spec.branches] : undefined),
  };
}

export function validateNodeData(data: BlueprintData): string[] {
  return data.required.flatMap((key) => {
    const value = data.config[key];
    return value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length) ? [`${key} 为必填配置`] : [];
  });
}

export function deleteBlueprintElement(graph: BlueprintGraph, kind: "node" | "edge", id: string): BlueprintGraph {
  if (kind === "edge") return { ...graph, edges: graph.edges.filter((edge) => edge.id !== id) };
  return {
    nodes: graph.nodes.filter((node) => node.id !== id),
    edges: graph.edges.filter((edge) => edge.source !== id && edge.target !== id),
  };
}

export function normalizeBlueprint(graph: BlueprintGraph): BlueprintGraph {
  return {
    nodes: graph.nodes.map((node) => {
      const legacy = node.data as Partial<BlueprintData>;
      const kind = specs.has(String(legacy.kind)) ? String(legacy.kind) : "api";
      return {
        ...node,
        type: "blueprint",
        data: createNodeData(kind, {
          ...legacy,
          kind,
          config: legacy.config || (kind === "api" ? { method: legacy.detail || "getMe", params: "{}" } : undefined),
        }),
      };
    }),
    edges: graph.edges.map((item) => ({ ...item })),
  };
}

function flowNode(id: string, kind: string, x: number, y: number, label?: string, config?: Record<string, BlueprintValue>, branches?: string[]): Node<BlueprintData> {
  return { id, type: "blueprint", position: { x, y }, data: createNodeData(kind, { label: label || nodeSpec(kind).label, config, branches }) };
}

function edge(id: string, source: string, target: string, label?: string, branch?: number): Edge {
  return { id, source, target, label, sourceHandle: branch === undefined ? undefined : `branch-${branch}` };
}

export function checkinBlueprint(): BlueprintGraph {
  return {
    nodes: [
      flowNode("checkin-start", "command", 20, 160, "/签到", { commands: "/签到,/checkin" }),
      flowNode("checkin-state", "condition", 270, 160, "今天签过了吗", { expression: "{{user.checked_in_today}} == false" }),
      flowNode("checkin-quiz", "checkin", 540, 60),
      flowNode("checkin-save", "database", 820, 20, "发放签到积分", { operation: "transaction", table: "checkins,users", fields: "insert checkin; points += 10" }),
      flowNode("checkin-retry", "delay", 820, 180, "答错后冷却", { seconds: 3, cooldown_key: "{{user.id}}:checkin", on_cooldown: "等待后重新生成题目" }),
      flowNode("checkin-done", "send", 540, 330, "显示签到状态", { text: "今日已签到，当前积分 {{user.points}}", target: "current_chat" }),
    ],
    edges: [
      edge("c1", "checkin-start", "checkin-state"), edge("c2", "checkin-state", "checkin-quiz", "未签到", 0),
      edge("c3", "checkin-state", "checkin-done", "已签到", 1), edge("c4", "checkin-quiz", "checkin-save", "正确", 0),
      edge("c5", "checkin-quiz", "checkin-retry", "错误", 1), edge("c6", "checkin-retry", "checkin-quiz", "重新出题"),
      edge("c7", "checkin-save", "checkin-done"),
    ],
  };
}

export function communityBotBlueprint(): BlueprintGraph {
  const nodes = [
    flowNode("start", "command", 0, 300, "/start 与主菜单", { commands: "/start,/menu" }),
    flowNode("main-menu", "menu", 260, 300, "社区主菜单", { text: "欢迎回来，请选择功能", buttons: [{ label: "签到" }, { label: "积分商店" }, { label: "邀请好友" }, { label: "排行榜" }] }, ["签到", "积分商店", "邀请好友", "排行榜"]),

    flowNode("check-state", "condition", 570, 0, "检查今日签到", { expression: "{{user.checked_in_today}} == false" }),
    flowNode("check-quiz", "checkin", 850, -60),
    flowNode("check-award", "database", 1130, -100, "签到入库并加积分", { operation: "transaction", table: "checkins,users", fields: "insert today; points += 10; streak += 1" }),
    flowNode("check-retry", "delay", 1130, 50, "答错冷却后重试", { seconds: 3, cooldown_key: "{{user.id}}:checkin", on_cooldown: "重新生成验证码" }),
    flowNode("check-result", "send", 1410, -40, "签到结果", { text: "签到成功 +10 积分，连续 {{user.streak}} 天", target: "current_chat" }),
    flowNode("checked", "send", 850, 130, "今日已签到", { text: "今天已经签到，当前 {{user.points}} 积分", target: "current_chat" }),

    flowNode("shop-menu", "menu", 570, 270, "积分商店", { text: "积分 {{user.points}}，请选择商品", buttons: [{ label: "补签卡·100分" }, { label: "VIP月卡·300分" }, { label: "返回" }] }),
    flowNode("shop-points", "condition", 850, 250, "积分是否足够", { expression: "{{user.points}} >= {{selected_item.price}}" }),
    flowNode("shop-redeem", "database", 1130, 220, "扣积分并发放商品", { operation: "transaction", table: "users,inventory,orders", fields: "points -= price; inventory += item; insert order" }),
    flowNode("shop-success", "send", 1410, 220, "兑换成功", { text: "兑换成功：{{selected_item.name}}，剩余 {{user.points}} 积分", target: "current_chat" }),
    flowNode("shop-low", "send", 1130, 370, "积分不足", { text: "积分不足，还差 {{selected_item.missing_points}} 分", target: "current_chat" }),

    flowNode("invite-parse", "parse", 570, 540, "识别邀请参数", { pattern: "^ref_(\\d+)$", save_as: "inviter_id" }),
    flowNode("invite-save", "database", 850, 500, "绑定邀请关系", { operation: "insert_once", table: "referrals", fields: "inviter_id,new_user_id,created_at; both points += 20" }),
    flowNode("invite-link", "send", 1130, 520, "发送邀请链接与统计", { text: "🔗 邀请链接：\nhttps://t.me/{{bot.username}}?start=inv_{{user.id}}\n\n已邀请 {{user.referral_count}} 人", target: "current_chat" }),

    flowNode("rank-query", "database", 570, 750, "查询积分排行榜", { operation: "select", table: "users", fields: "id,name,points ORDER BY points DESC LIMIT 10" }),
    flowNode("rank-send", "send", 850, 750, "显示排行榜", { text: "🏆 积分榜\n{{ranking.lines}}\n你的排名：{{user.rank}}", target: "current_chat" }),

    flowNode("invoice", "invoice", 570, 980, "购买 Stars 积分包"),
    flowNode("payment", "payment_event", 850, 950, "支付校验与幂等入账"),
    flowNode("sub-check", "subscription", 1130, 930, "VIP 订阅状态"),
  ];
  return {
    nodes,
    edges: [
      edge("e1", "start", "main-menu"),
      edge("e2", "main-menu", "check-state", "签到", 0), edge("e3", "main-menu", "shop-menu", "商店", 1),
      edge("e4", "main-menu", "invite-parse", "邀请", 2), edge("e5", "main-menu", "rank-query", "排行", 3),
      edge("e6", "check-state", "check-quiz", "未签到", 0), edge("e7", "check-state", "checked", "已签到", 1),
      edge("e8", "check-quiz", "check-award", "答对", 0), edge("e9", "check-quiz", "check-retry", "答错", 1),
      edge("e10", "check-retry", "check-quiz", "重新出题"), edge("e11", "check-award", "check-result"),
      edge("e12", "shop-menu", "shop-points", "选择商品", 0), edge("e13", "shop-points", "shop-redeem", "足够", 0),
      edge("e14", "shop-points", "shop-low", "不足", 1), edge("e15", "shop-redeem", "shop-success"),
      edge("e16", "invite-parse", "invite-save"), edge("e17", "invite-save", "invite-link"),
      edge("e18", "rank-query", "rank-send"), edge("e19", "invoice", "payment", "等待支付", 0), edge("e20", "payment", "sub-check", "已入账", 0),
      edge("e21", "shop-menu", "invoice", "VIP Stars 购买", 1), edge("e22", "shop-menu", "main-menu", "返回", 2),
    ],
  };
}

export const BOT_BLUEPRINT_TEMPLATES: BlueprintTemplate[] = [
  { id: "community", name: "社区积分机器人", description: "完整包含验证码签到、积分商店、邀请奖励、排行榜，以及 Stars 支付和订阅校验。", graph: communityBotBlueprint() },
  { id: "checkin", name: "验证码签到机器人", description: "四选一计算题、答错冷却重试、每日去重、积分与连续签到。", graph: checkinBlueprint() },
  { id: "blank", name: "空白蓝图", description: "从空画布开始，按需拖入节点。", graph: { nodes: [], edges: [] } },
];

export function defaultBotBlueprint(): BlueprintGraph {
  return structuredClone(checkinBlueprint());
}

export function blueprintFromSteps(steps: Array<{ id: string; name: string; method: string }>): BlueprintGraph {
  if (!steps.length) return defaultBotBlueprint();
  const nodes = steps.map((step, index) => flowNode(step.id, "api", 40 + index * 260, 180, step.name, { method: step.method, params: "{}" }));
  const edges = steps.slice(1).map((step, index) => edge(`legacy-${index}`, steps[index].id, step.id));
  return { nodes, edges };
}
