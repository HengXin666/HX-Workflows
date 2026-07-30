type FlowNodeLike = {
  id: string;
  data?: Record<string, unknown>;
};

type FlowEdgeLike = {
  source: string;
  target: string;
};

export type AutomaticInputKind = "preview_catalog" | "resource_catalog" | "resource_pool";

const LEGACY_DEFAULTS: Record<AutomaticInputKind, Set<string>> = {
  preview_catalog: new Set(["resource_responses", "last_parse"]),
  resource_catalog: new Set(["resource_records", "last_links"]),
  resource_pool: new Set(["resource_responses", "last_parse"]),
};

export function effectiveInputMode(
  data: Record<string, unknown>,
  kind: AutomaticInputKind,
): "auto" | "variable" {
  const mode = String(data.inputMode || "").trim();
  if (mode === "auto" || mode === "variable") return mode;
  const source = String(data.source || "").trim();
  return source && !LEGACY_DEFAULTS[kind].has(source) ? "variable" : "auto";
}

function outputForNode(data: Record<string, unknown>, targetKind: AutomaticInputKind): string {
  const kind = String(data.kind || "");
  if (targetKind === "resource_catalog") {
    if (kind === "continuous_request") return String(data.linksSaveAs || "resource_records");
    if (kind === "resource_pool") return "resource_records";
    if (kind === "parse" && ["links", "join_links"].includes(String(data.extract || "messages"))) {
      return String(data.saveAs || "last_links");
    }
    return "";
  }
  if (targetKind === "preview_catalog") {
    if (kind === "continuous_request") return String(data.saveAs || "resource_responses");
    if (kind === "parse") return "last_parse";
    return "";
  }
  if (kind === "continuous_request") return String(data.saveAs || "resource_responses");
  if (kind === "parse") return "last_parse";
  return "";
}

export function inferUpstreamInput(
  nodeId: string,
  targetKind: AutomaticInputKind,
  nodes: FlowNodeLike[],
  edges: FlowEdgeLike[],
): string {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const visited = new Set<string>();
  let current = nodeId;
  while (current && !visited.has(current)) {
    visited.add(current);
    const previous = edges.find((edge) => edge.target === current)?.source || "";
    if (!previous) break;
    const output = outputForNode(byId.get(previous)?.data || {}, targetKind);
    if (output) return output;
    current = previous;
  }
  return targetKind === "resource_catalog" ? "resource_records" : targetKind === "preview_catalog" ? "resource_responses" : "last_parse";
}
