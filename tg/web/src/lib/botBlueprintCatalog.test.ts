import { describe, expect, test } from "vitest";
import {
  BOT_BLUEPRINT_TEMPLATES,
  BOT_NODE_CATALOG,
  communityBotBlueprint,
  createNodeData,
  deleteBlueprintElement,
  normalizeBlueprint,
  validateNodeData,
  type BlueprintGraph,
} from "./botBlueprintCatalog";

describe("机器人节点目录", () => {
  test("节点 kind 与中文名称唯一", () => {
    expect(new Set(BOT_NODE_CATALOG.map((node) => node.kind)).size).toBe(BOT_NODE_CATALOG.length);
    expect(new Set(BOT_NODE_CATALOG.map((node) => node.label)).size).toBe(BOT_NODE_CATALOG.length);
  });

  test.each(BOT_NODE_CATALOG)("$label ($kind) 有明确、可执行的节点契约", (spec) => {
    expect(spec.detail.trim()).not.toBe("");
    expect(spec.input.trim()).not.toBe("");
    expect(spec.output.trim()).not.toBe("");
    expect(spec.runtime.trim()).not.toBe("");
    expect(validateNodeData(createNodeData(spec.kind))).toEqual([]);
    for (const required of spec.required) expect(spec.config).toHaveProperty(required);
    if (spec.branches) expect(new Set(spec.branches).size).toBe(spec.branches.length);
  });

  test.each(BOT_NODE_CATALOG.flatMap((spec) => spec.required.map((field) => [spec.kind, field] as const)))(
    "%s 缺少必填配置 %s 时会被识别",
    (kind, field) => {
      const data = createNodeData(kind);
      data.config[field] = "";
      expect(validateNodeData(data)).toContain(`${field} 为必填配置`);
    },
  );

  test("支付被拆成发票、支付确认、订阅校验三个真实阶段", () => {
    const invoice = createNodeData("invoice");
    const payment = createNodeData("payment_event");
    const subscription = createNodeData("subscription");
    expect(invoice.runtime).toContain("sendInvoice");
    expect(invoice.config).toMatchObject({ price_xtr: 50, payload: "points_100" });
    expect(payment.runtime).toContain("answerPreCheckoutQuery");
    expect(payment.runtime).toContain("idempotency");
    expect(subscription.runtime).toContain("subscriptions");
    expect(subscription.branches).toEqual(["订阅有效", "已过期", "未订阅"]);
  });

  test("行式配置使用结构化数组而不是要求用户维护换行符", () => {
    expect(createNodeData("command").config.commands).toEqual(["/start"]);
    expect(createNodeData("message").config.message_types).toEqual(["text", "photo", "document", "location"]);
    expect(createNodeData("random").config.weights).toEqual([50, 50]);
    expect(createNodeData("menu").config.buttons).toEqual([
      { label: "按钮 1" },
      { label: "按钮 2" },
      { label: "返回" },
    ]);
  });

  test("旧版换行按钮配置会自动迁移为结构化行", () => {
    const data = createNodeData("menu", { config: { buttons: "签到|menu:checkin\n排行|menu:rank" } });
    expect(data.config.buttons).toEqual([
      { label: "签到" },
      { label: "排行" },
    ]);
  });

  test("旧版只有名称和说明的节点会补齐新契约", () => {
    const legacy = {
      nodes: [{ id: "legacy", type: "blueprint", position: { x: 0, y: 0 }, data: { kind: "api", label: "读取资料", detail: "getMe", category: "扩展" } }],
      edges: [],
    } as unknown as BlueprintGraph;
    const migrated = normalizeBlueprint(legacy);
    expect(migrated.nodes[0].data.config).toEqual({ method: "getMe", params: "{}" });
    expect(migrated.nodes[0].data.input).not.toBe("");
    expect(validateNodeData(migrated.nodes[0].data)).toEqual([]);
  });
});

describe("机器人蓝图模板", () => {
  test.each(BOT_BLUEPRINT_TEMPLATES)("$name 的节点、连线与分支句柄完整", (template) => {
    const ids = new Set(template.graph.nodes.map((node) => node.id));
    expect(ids.size).toBe(template.graph.nodes.length);
    for (const node of template.graph.nodes) {
      expect(BOT_NODE_CATALOG.some((spec) => spec.kind === node.data.kind)).toBe(true);
      expect(validateNodeData(node.data)).toEqual([]);
    }
    for (const edge of template.graph.edges) {
      expect(ids.has(edge.source)).toBe(true);
      expect(ids.has(edge.target)).toBe(true);
      if (edge.sourceHandle) {
        const source = template.graph.nodes.find((node) => node.id === edge.source)!;
        const index = Number(edge.sourceHandle.replace("branch-", ""));
        expect(source.data.branches?.[index]).toBeDefined();
      }
    }
  });

  test("社区积分机器人覆盖签到、商店、邀请、排行榜和支付订阅", () => {
    const graph = communityBotBlueprint();
    const labels = graph.nodes.map((node) => node.data.label).join(" ");
    const kinds = new Set(graph.nodes.map((node) => node.data.kind));
    expect(labels).toContain("签到");
    expect(labels).toContain("商店");
    expect(labels).toContain("邀请");
    expect(labels).toContain("排行榜");
    for (const kind of ["checkin", "database", "invoice", "payment_event", "subscription"]) expect(kinds.has(kind)).toBe(true);
  });
});

describe("蓝图删除", () => {
  const graph: BlueprintGraph = {
    nodes: [
      { id: "a", type: "blueprint", position: { x: 0, y: 0 }, data: createNodeData("command") },
      { id: "b", type: "blueprint", position: { x: 100, y: 0 }, data: createNodeData("send") },
      { id: "c", type: "blueprint", position: { x: 200, y: 0 }, data: createNodeData("end") },
    ],
    edges: [
      { id: "ab", source: "a", target: "b" },
      { id: "bc", source: "b", target: "c" },
      { id: "ac", source: "a", target: "c" },
    ],
  };

  test("删除节点和所有相连连线是同一次原子更新", () => {
    const next = deleteBlueprintElement(graph, "node", "b");
    expect(next.nodes.map((node) => node.id)).toEqual(["a", "c"]);
    expect(next.edges.map((edge) => edge.id)).toEqual(["ac"]);
    expect(graph.nodes.map((node) => node.id)).toEqual(["a", "b", "c"]);
  });

  test("删除连线不会影响节点或其他连线", () => {
    const next = deleteBlueprintElement(graph, "edge", "ab");
    expect(next.nodes).toHaveLength(3);
    expect(next.edges.map((edge) => edge.id)).toEqual(["bc", "ac"]);
  });
});
