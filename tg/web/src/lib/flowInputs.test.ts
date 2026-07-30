import { describe, expect, it } from "vitest";
import { effectiveInputMode, inferUpstreamInput } from "./flowInputs";

describe("automatic flow inputs", () => {
  it("routes GetLoliAV parsed links into the resource catalog", () => {
    const nodes = [
      { id: "send", data: { kind: "send" } },
      { id: "parse", data: { kind: "parse", extract: "links", saveAs: "last_links" } },
      { id: "catalog", data: { kind: "resource_catalog", source: "resource_records" } },
    ];
    const edges = [
      { source: "send", target: "parse" },
      { source: "parse", target: "catalog" },
    ];
    expect(inferUpstreamInput("catalog", "resource_catalog", nodes, edges)).toBe("last_links");
    expect(effectiveInputMode(nodes[2].data, "resource_catalog")).toBe("auto");
  });

  it("keeps explicit custom variables and finds messages through intermediate nodes", () => {
    const nodes = [
      { id: "request", data: { kind: "continuous_request", saveAs: "responses" } },
      { id: "catalog", data: { kind: "resource_catalog" } },
      { id: "pool", data: { kind: "resource_pool" } },
    ];
    const edges = [
      { source: "request", target: "catalog" },
      { source: "catalog", target: "pool" },
    ];
    expect(inferUpstreamInput("pool", "resource_pool", nodes, edges)).toBe("responses");
    expect(effectiveInputMode({ source: "my_records" }, "resource_catalog")).toBe("variable");
  });

  it("routes continuous responses into the preview index without using link output", () => {
    const nodes = [
      { id: "request", data: { kind: "continuous_request", saveAs: "preview_responses", linksSaveAs: "unused_links" } },
      { id: "previews", data: { kind: "preview_catalog", source: "resource_responses" } },
    ];
    const edges = [{ source: "request", target: "previews" }];
    expect(inferUpstreamInput("previews", "preview_catalog", nodes, edges)).toBe("preview_responses");
    expect(effectiveInputMode(nodes[1].data, "preview_catalog")).toBe("auto");
  });
});
