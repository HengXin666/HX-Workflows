# 仓库卫生

手动扫描 `HengXin666` 名下的仓库, 找出已经合入默认分支的多余分支, 以及版本落后的 Skill. 结果写进本仓库的一张审阅单. 勾选后加上 `apply` 标签, 才会删除分支或提交更新.

## 状态卡片

[![仓库卫生](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FHengXin666%2FHX-Workflows%2Fmain%2Fhygiene%2Fbadges%2Fsummary.json)](https://github.com/HengXin666/HX-Workflows/issues?q=is%3Aissue+is%3Aopen+label%3Ahygiene-report)
[![可删分支](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FHengXin666%2FHX-Workflows%2Fmain%2Fhygiene%2Fbadges%2Fbranches.json)](https://github.com/HengXin666/HX-Workflows/issues?q=is%3Aissue+is%3Aopen+label%3Ahygiene-report)
[![过期 Skill](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FHengXin666%2FHX-Workflows%2Fmain%2Fhygiene%2Fbadges%2Fskills.json)](https://github.com/HengXin666/HX-Workflows/issues?q=is%3Aissue+is%3Aopen+label%3Ahygiene-report)

卡片 SVG:

- [仓库卫生](./badges/summary.svg)
- [可删分支](./badges/branches.svg)
- [过期 Skill](./badges/skills.svg)

GitHub README 不渲染仓库内的 SVG, 所以上面的徽标由 shields.io 读取同目录的 JSON. 扫描后工作流会用这些 SVG 重新导出 `badges/summary.png`.

## 怎么用

1. 创建一个 classic PAT, 勾选 `repo`. 细粒度令牌每次新建仓库都要再授权, 不适合扫全部仓库.
2. 在本仓库 Settings -> Secrets and variables -> Actions 添加 `HYGIENE_TOKEN`.
3. 打开 Actions 里的「仓库卫生扫描」, 手动运行. `repo_limit` 只在日志里看结果, 不会改正式审阅单和状态卡片.
4. 在新的审阅单里勾选要处理的项.
5. 给这张 Issue 加上 `apply` 标签. 只勾选不会执行.

分支会直接删除. Skill 会按仓库打开一个拉取请求, 能通过保护规则就 squash 合入. 合不进去会把拉取请求留在原仓库, 并写回审阅单.

## 判断规则

可删分支必须同时满足:

- 不是默认分支, 也不是受保护分支
- 不匹配 `main` / `master` / `develop` / `dev` / `gh-pages` / `bilibili-token` / `release/*` / `dependabot/*` / `renovate/*`
- 没有打开的拉取请求
- 相对默认分支 `ahead = 0`

Skill 按目录名和 frontmatter 的 `name` 归组, 读 `metadata.version`. 最高版本是权威副本. 版本相同时优先 `preferred_sources` 里的 `HXLoLi`, 再比最后提交时间. 只替换版本更低且目录内容不同的副本. 同版本分叉、没有版本号, 只写在审阅单里, 不提供自动覆盖.

扫描根目录:

- `.agents/skills`
- `.dsh/skills`
- `.codex/skills`

fork、归档和私有仓库默认不扫, 避免公开审阅单带出私有仓库的名字和分支. 可以在 [config.yml](./config.yml) 里改. `bilibili-token` 是令牌分支, 永远不删.

## 工作流

- [仓库卫生扫描](../.github/workflows/hygiene-scan.yml): 只读扫描, 更新审阅单和状态卡片
- [仓库卫生修复](../.github/workflows/hygiene-apply.yml): 只在 `apply` 标签出现时执行, 并且操作者必须是 `HengXin666`
