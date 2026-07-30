export const CONTINUOUS_REQUEST_LIMITS = {
  repeatCount: { min: 1, max: 1000 },
  intervalSeconds: { min: 0.5, max: 3600 },
  responseWaitSeconds: { min: 0, max: 300 },
  responseLimit: { min: 1, max: 100 },
  maxFailures: { min: 1, max: 1000 },
} as const;

type ContinuousRequestData = Record<string, unknown>;

function finiteNumber(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function validateInteger(
  value: unknown,
  label: string,
  bounds: { min: number; max: number },
): string | null {
  const parsed = finiteNumber(value);
  if (parsed === null || !Number.isInteger(parsed)) return `${label}必须是整数`;
  if (parsed < bounds.min || parsed > bounds.max) {
    return `${label}必须在 ${bounds.min}-${bounds.max} 之间`;
  }
  return null;
}

function validateNumber(
  value: unknown,
  label: string,
  bounds: { min: number; max: number },
): string | null {
  const parsed = finiteNumber(value);
  if (parsed === null) return `${label}必须是数字`;
  if (parsed < bounds.min || parsed > bounds.max) {
    return `${label}必须在 ${bounds.min}-${bounds.max} 之间`;
  }
  return null;
}

export function validateContinuousRequest(data: ContinuousRequestData): string[] {
  const issues = [
    validateInteger(data.repeatCount, "重复次数", CONTINUOUS_REQUEST_LIMITS.repeatCount),
    validateNumber(data.intervalSeconds, "请求间隔", CONTINUOUS_REQUEST_LIMITS.intervalSeconds),
    validateNumber(data.responseWaitSeconds, "回执等待", CONTINUOUS_REQUEST_LIMITS.responseWaitSeconds),
    validateInteger(data.responseLimit, "每轮读取数", CONTINUOUS_REQUEST_LIMITS.responseLimit),
    validateInteger(data.maxFailures, "失败阈值", CONTINUOUS_REQUEST_LIMITS.maxFailures),
  ].filter((issue): issue is string => Boolean(issue));

  const requestMode = String(data.requestMode || "message");
  if (!new Set(["message", "button"]).has(requestMode)) issues.push("请求方式无效");
  if (!String(data.requestText || "").trim()) issues.push("请求内容为空");
  if (!String(data.saveAs || "").trim()) issues.push("回执变量为空");
  if (!String(data.linksSaveAs || "").trim()) issues.push("资源变量为空");

  const repeatCount = finiteNumber(data.repeatCount);
  const maxFailures = finiteNumber(data.maxFailures);
  if (repeatCount !== null && maxFailures !== null && maxFailures > repeatCount) {
    issues.push("失败阈值不能大于重复次数");
  }
  return issues;
}

export function validateResourceCatalog(data: ContinuousRequestData): string[] {
  const issues: string[] = [];
  const inputMode = String(data.inputMode || "auto");
  if (!new Set(["auto", "variable"]).has(inputMode)) issues.push("链接入口模式无效");
  if (inputMode === "variable" && !String(data.source || "").trim()) issues.push("来源变量为空");
  if (!String(data.dedupeStore || "").trim()) issues.push("去重 DB 路径为空");
  if (!String(data.exportPath || "").trim()) issues.push("资源清单路径为空");
  return issues;
}

export function validatePreviewCatalog(data: ContinuousRequestData): string[] {
  const issues: string[] = [];
  const inputMode = String(data.inputMode || "auto");
  if (!new Set(["auto", "variable"]).has(inputMode)) issues.push("预览入口模式无效");
  if (inputMode === "variable" && !String(data.source || "").trim()) issues.push("来源变量为空");
  if (!String(data.callbackPrefix || "").trim()) issues.push("按钮索引前缀为空");
  if (!String(data.dedupeStore || "").trim()) issues.push("预览去重 DB 路径为空");
  if (!String(data.exportPath || "").trim()) issues.push("预览清单路径为空");
  return issues;
}
