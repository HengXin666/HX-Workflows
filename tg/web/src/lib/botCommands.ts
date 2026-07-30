export type BotCommandLike = {
  command: string;
};

export function resolveCommandAfterLoad(
  currentCommand: unknown,
  commands: readonly BotCommandLike[],
): string {
  const current = String(currentCommand || "");
  if (current.trim()) return current;

  const first = String(commands[0]?.command || "");
  return first.trim() ? first : current;
}
