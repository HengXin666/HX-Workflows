# Agent Note: 多平台镜像同步

Status: implemented

Decision-ID: multi-platform-mirror

## Code

- `mirror/mirror/plan.py`
- `mirror/tests/test_mirror.py`

## Problem

个人名下的 GitHub 仓库 (含私有) 需要定时备份到 GitLab / Gitee / Codeberg / CNB. 目标平台上没有的仓库要自动新建, 公开 / 私有要继承源仓库且在源变更后跟着变. 本仓库是公开的, 运行日志、Summary 和附件谁都能看, 不能泄露私有仓库的名字或代码

## Decision

新建独立子项目 `mirror` 和 `mirror-sync.yml` 工作流, 每天和手动运行, 支持 `dry_run` 只读试跑. 以 [sky22333/git-mirror-sync](https://github.com/sky22333/git-mirror-sync) 的思路为参考, 按本仓库约定用 Python 重写: 平台调用都经过可替换的适配器, 纯决策集中在 `plan.py`

可见性默认 `follow`: 新建时继承源仓库, 已存在时 `sync_visibility` 打开就改成一致. CNB 的 OpenAPI 不能事后改可见性, 只在报告里提示手动处理. 推送只同步 `refs/heads/*` 和 `refs/tags/*` 并强制覆盖, 默认不删除目标上多出的引用; GitLab 推送前为受保护分支打开强制推送、解除受保护标签

令牌只经环境变量和 `GIT_ASKPASS` 传给 git, 不进命令行和远端地址. 报告里私有仓库名一律替换为名字哈希, 本地用 `--reveal-private` 才显示真名. 源仓库的裸镜像只放在 runner 临时目录, 不进 Actions 缓存. 没配 secret 的平台自动跳过, 不算失败

## Alternatives considered

- 什么都不做 / 复用现有: 直接用 sky22333/git-mirror-sync 的 Docker 镜像零开发量, 已支持建仓和 follow 可见性, 但它不会改已存在仓库的可见性, 报告会把私有仓库名写进公开仓库的日志, 也不符合本仓库 Python 子项目和可替换适配器的约定, 因此只借鉴思路
- 并入 hygiene 或 governance: 能复用 GitHub 适配器和工作流骨架, 但两者的职责是扫描后经审阅单改 GitHub 本身, 镜像要写多个外部平台且无需审阅, 混在一起会让权限和失败面互相牵连
- 平台自带的拉取镜像 (GitLab / Gitee / Codeberg 的 pull mirror): 不用跑 CI, 但要在每个平台为每个仓库手工配置并存放 GitHub 令牌, 新仓库不会自动出现, 私有性质也不会继承
- 用 Actions 缓存保存源仓库加速: 第二次起更快, 但公开仓库里来自 fork 的 PR 工作流能恢复默认分支的缓存, 会泄露私有仓库代码, 因此不用
- 推送时默认 `--prune`: 目标和源完全一致, 但 GitLab 等平台的默认分支删除会被拒, 误删也无法恢复, 因此做成可选

## Consequences

第一次运行要推送全部历史, 仓库多时耗时较长. CNB 已存在仓库的可见性需要手动修正. 源仓库删除后目标不会删除. 不同步 Git LFS 对象. 手动运行的 `repo_limit` 参数在公开仓库里可见, 不能填私有仓库名. 加新平台的 secret 时要同时在工作流的 env 里加一行. 带真名和镜像链接的同步结果另外发到用户邮箱, 见 [镜像同步邮件](./2026-11-28-mirror-email-report.md); 推送改用 `--porcelain` 以统计实际更新的引用
