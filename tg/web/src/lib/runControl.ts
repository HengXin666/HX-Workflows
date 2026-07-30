const ACTIVE_RUN_STATUSES = new Set(["running", "cancelling"]);
const TERMINAL_RUN_STATUSES = new Set(["done", "error", "not_found", "cancelled"]);

export function isRunActiveStatus(status: string | null | undefined): boolean {
  return Boolean(status && ACTIVE_RUN_STATUSES.has(status));
}

export function isRunTerminalStatus(status: string | null | undefined): boolean {
  return Boolean(status && TERMINAL_RUN_STATUSES.has(status));
}

export function shouldAcceptRunStatus(currentStatus: string | null | undefined, nextStatus: string): boolean {
  return !isRunTerminalStatus(currentStatus) || isRunTerminalStatus(nextStatus);
}
