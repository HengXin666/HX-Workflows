import { describe, expect, it } from "vitest";
import {
  CONTINUOUS_REQUEST_LIMITS,
  validateContinuousRequest,
  validatePreviewCatalog,
  validateResourceCatalog,
} from "./continuousRequest";

const validRequest = {
  requestMode: "button",
  requestText: "随机获取资源",
  repeatCount: 20,
  intervalSeconds: 3,
  responseWaitSeconds: 2,
  responseLimit: 10,
  maxFailures: 3,
  saveAs: "resource_responses",
  linksSaveAs: "resource_records",
};

describe("continuous request node contract", () => {
  it("accepts a bounded machine request", () => {
    expect(validateContinuousRequest(validRequest)).toEqual([]);
  });

  it("rejects unbounded and malformed loops", () => {
    expect(validateContinuousRequest({
      ...validRequest,
      repeatCount: 0,
      intervalSeconds: 0,
      maxFailures: 1,
    })).toEqual(expect.arrayContaining([
      "重复次数必须在 1-1000 之间",
      "请求间隔必须在 0.5-3600 之间",
      "失败阈值不能大于重复次数",
    ]));
  });

  it("requires persistent catalog outputs", () => {
    expect(validateResourceCatalog({ inputMode: "auto", source: "", dedupeStore: "", exportPath: "" })).toHaveLength(2);
    expect(validateResourceCatalog({ inputMode: "variable", source: "", dedupeStore: "", exportPath: "" })).toHaveLength(3);
    expect(validateResourceCatalog({
      source: "resource_records",
      dedupeStore: "sessions/resource_catalog.sqlite3",
      exportPath: "sessions/resource_links.json",
    })).toEqual([]);
  });

  it("requires a persistent read-only preview index", () => {
    expect(validatePreviewCatalog({ inputMode: "auto", callbackPrefix: "", dedupeStore: "", exportPath: "" })).toHaveLength(3);
    expect(validatePreviewCatalog({
      inputMode: "auto",
      callbackPrefix: "unlock:",
      dedupeStore: "sessions/resource_previews.sqlite3",
      exportPath: "sessions/resource_previews.json",
    })).toEqual([]);
  });
});
