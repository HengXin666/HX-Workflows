import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = process.argv[2];
const out = process.argv[3] || "comparison.md";
if (!dir) {
  console.error("usage: node report.mjs <captures-dir> [comparison.md]");
  process.exit(2);
}

const here = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(dir).filter((name) => name.endsWith(".json")).sort();
const columns = [];
for (const name of files) {
  const raw = JSON.parse(readFileSync(join(dir, name), "utf8"));
  if (raw.available === false) {
    columns.push({ label: raw.browser || name, missing: raw.error || "未安装", rows: [] });
    continue;
  }
  const run = spawnSync(
    process.execPath,
    ["--experimental-strip-types", join(here, "score.ts"), join(dir, name), "--no-fail"],
    { encoding: "utf8" },
  );
  const line = `${run.stdout || ""}${run.stderr || ""}`.split("\n").find((item) => item.startsWith("RESULT "));
  if (!line) {
    columns.push({ label: raw.browser || name, missing: (run.stderr || "打分失败").slice(0, 200), rows: [] });
    continue;
  }
  const result = JSON.parse(line.slice("RESULT ".length));
  columns.push({ label: raw.browser || name, missing: "", score: result.score, verdict: result.verdict, rows: result.rows });
}

const sites = columns.find((column) => column.rows.length)?.rows.map((row) => row.name) ?? [];
const header = ["站", ...columns.map((column) => column.label)];
const lines = [
  `# 检测站对比`,
  ``,
  `对照是本机 Google Chrome 和 Firefox（火狐），有头跑在工作流的虚拟桌面（Xvfb）上，无头不加窗口。`,
  `hx 行是指纹浏览器的每个内核，同样分有头和无头。格子里没有浏览器的，写未安装，不算 0 分。`,
  ``,
  `| ${header.join(" | ")} |`,
  `| ${header.map(() => "---").join(" | ")} |`,
];
for (const site of sites) {
  const cells = [site];
  for (const column of columns) {
    if (column.missing) {
      cells.push("未安装");
      continue;
    }
    const row = column.rows.find((item) => item.name === site);
    cells.push(row ? `${row.score ?? "—"} ${row.headline}` : "—");
  }
  lines.push(`| ${cells.join(" | ")} |`);
}
lines.push("");
for (const column of columns) {
  const tail = column.missing ? `未安装：${column.missing}` : `${column.score} ${column.verdict}`;
  lines.push(`- ${column.label}：${tail}`);
}
writeFileSync(out, lines.join("\n"));
console.log(lines.join("\n"));
