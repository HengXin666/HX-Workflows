import { describe, expect, it } from "vitest";
import { isRunActiveStatus, isRunTerminalStatus, shouldAcceptRunStatus } from "./runControl";

describe("run control status", () => {
  it.each(["running", "cancelling"])("treats %s as active", (status) => {
    expect(isRunActiveStatus(status)).toBe(true);
    expect(isRunTerminalStatus(status)).toBe(false);
  });

  it.each(["done", "error", "not_found", "cancelled"])("treats %s as terminal", (status) => {
    expect(isRunTerminalStatus(status)).toBe(true);
    expect(isRunActiveStatus(status)).toBe(false);
  });

  it("does not classify empty or unknown statuses", () => {
    expect(isRunActiveStatus(undefined)).toBe(false);
    expect(isRunTerminalStatus("queued")).toBe(false);
  });

  it("does not regress an SSE terminal status with a late cancellation response", () => {
    expect(shouldAcceptRunStatus("cancelled", "cancelling")).toBe(false);
    expect(shouldAcceptRunStatus("running", "cancelled")).toBe(true);
    expect(shouldAcceptRunStatus("cancelled", "cancelled")).toBe(true);
  });
});
