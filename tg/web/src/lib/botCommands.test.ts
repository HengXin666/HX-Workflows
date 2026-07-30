import { describe, expect, it } from "vitest";
import { resolveCommandAfterLoad } from "./botCommands";

describe("bot command selection after loading", () => {
  const commands = [
    { command: "/start" },
    { command: "random resource" },
  ];

  it("selects the first loaded command when the node command is empty", () => {
    expect(resolveCommandAfterLoad("", commands)).toBe("/start");
    expect(resolveCommandAfterLoad("   ", commands)).toBe("/start");
  });

  it("preserves a command already chosen by the user", () => {
    expect(resolveCommandAfterLoad("random resource", commands)).toBe("random resource");
  });

  it("leaves the node unchanged when no usable command was loaded", () => {
    expect(resolveCommandAfterLoad("", [])).toBe("");
    expect(resolveCommandAfterLoad("", [{ command: "" }])).toBe("");
  });
});
