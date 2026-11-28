# 多平台镜像同步

把 `HengXin666` 名下的 GitHub 仓库 (含私有) 定时镜像到 GitLab / Gitee / Codeberg (或自建 Forgejo / Gitea) / CNB. 思路参考 [sky22333/git-mirror-sync](https://github.com/sky22333/git-mirror-sync), 按本仓库的约定用 Python 重写.

- 工作流: [多平台镜像同步](../.github/workflows/mirror-sync.yml), 每天 02:00 (北京时间) 自动跑, 也可以手动运行
- 配置: [config.yml](./config.yml)

## 它会做什么

对每个源仓库、每个启用的目标平台:

1. **目标没有这个仓库就新建**, 可见性**继承**源仓库: 源是私有就建私有, 源是公开就建公开 (`policy.visibility: follow`).
2. **已存在的仓库可见性和源不一致时改成一致** (`policy.sync_visibility: true`). 源从私有改成公开、或反过来, 下次同步目标会跟着变.
3. 推送全部**分支和标签** (强制覆盖, 历史完整), 不推 PR 引用.
4. 目标默认分支和源不一致时改成一致.
5. GitLab 推送前给受保护分支打开「允许强制推送」, 并解除受保护标签, 否则改写历史的推送会被拒.

失败只影响当前仓库, 跑完所有仓库后有失败项时工作流标红. 网络等临时问题自动重试 (默认 3 次). 每次运行的结果在 Actions 页面的 Summary 里, 也作为 `mirror-report` 附件保存 7 天.

> **隐私**: 本仓库是公开的, 日志、Summary 和附件谁都能看. 所以报告里私有仓库的名字一律显示成 `私有仓库-xxxxxx` (名字的哈希). 本地想看真名加 `--reveal-private`. 同理, 手动运行时不要在 `repo_limit` 里填私有仓库名, 运行参数也是公开的.

## 配置教程

> 每个平台从注册到生成令牌的分步说明 (带直达链接) 见 [各平台配置教程](./docs/PLATFORMS.md). 下面是总览.

### 第 1 步: 生成 GitHub 令牌 (读取源仓库)

已经给仓库卫生配过 `HYGIENE_TOKEN` 的话可以直接复用, 工作流会自动用它, 这一步跳过.

否则到 GitHub → Settings → Developer settings → Personal access tokens:

- **classic PAT**: 勾选 `repo` (能读私有仓库). 过期时间按需设.
- 或 **fine-grained PAT**: Repository access 选 *All repositories*, 权限 `Contents: Read-only`、`Metadata: Read-only`.

保存为本仓库的 Actions secret `MIRROR_GITHUB_TOKEN`.

### 第 2 步: 生成各目标平台的令牌

只配你要用的平台. **没有配 secret 的平台会被自动跳过**, 不会报错, 所以 config.yml 里的平台段可以先留着.

| 平台 | 在哪里生成 | 需要的权限 | Actions secret 名 |
| --- | --- | --- | --- |
| GitLab | 头像 → Preferences → Access tokens | `api` (含建仓、改可见性、改分支保护) 和 `write_repository` | `GITLAB_TOKEN` |
| Gitee | 设置 → 安全设置 → 私人令牌 | `user_info`、`projects` (推到组织下再加 `groups`) | `GITEE_TOKEN` |
| Codeberg | Settings → Applications → Generate new token | `repository: Read and write`、`user: Read` (推到组织下再加 `organization: Read and write`) | `CODEBERG_TOKEN` |
| CNB | 个人设置 → 访问令牌 | `account-profile:r`、`group-resource:rw`、`repo-basic-info:r`、`repo-code:rw` | `CNB_TOKEN` |

账号本身的权限也要够: GitLab 推到群组时你在群组里至少是 Maintainer (改可见性建议 Owner); Gitee / Codeberg 推到组织时你要是组织管理员; CNB 要能在目标组织下建仓.

### 第 3 步: 添加 Actions secrets

本仓库 → Settings → Secrets and variables → Actions → New repository secret, 按上表的名字逐个添加. 令牌只放在这里, **不要写进 config.yml**; 配置里 `token_env` 只写环境变量名, 写成令牌样子的值会被直接拒绝.

### 第 4 步: 改 config.yml

最少只需要把每个平台的 `owner` 改成你在那个平台的用户名或组织路径:

```yaml
targets:
  - name: gitlab
    provider: gitlab
    owner: HengXin666        # GitLab 用户名, 或群组路径如 hx-backup / hx/backup
    token_env: GITLAB_TOKEN
```

完整字段:

| 位置 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| `source` | `owner` | 必填 | GitHub 用户名或组织名. 是令牌本人时读个人仓库, 否则读组织仓库 |
| | `token_env` | `MIRROR_GITHUB_TOKEN` | |
| | `include_private` | `true` | 同步私有仓库 |
| | `include_forks` | `false` | 同步 fork 仓库 |
| | `include_archived` | `true` | 同步已归档仓库 |
| | `include` / `exclude` | `[]` | 仓库名 glob, 不分大小写. `include` 留空表示全部 |
| `policy` | `visibility` | `follow` | `follow` 继承源仓库; `private` 全部私有; `public` 全部公开 |
| | `sync_visibility` | `true` | 已存在的目标仓库也按上面的规则改可见性 |
| | `create_missing` | `true` | 目标没有就新建; `false` 时只推已存在的仓库 |
| | `prune` | `false` | `true` 时源上删除的分支 / 标签在目标上也删除 |
| | `concurrency` | `2` | 同时处理的仓库数 |
| | `max_retries` | `3` | 临时失败重试次数 |
| | `git_timeout` | `1800` | 单次拉取 / 推送超时秒数 |
| `targets[]` | `name` | 同 `provider` | 报告里显示的名字, 不能重复. 同一平台推两个账号时用它区分 |
| | `provider` | 必填 | `gitlab` / `gitee` / `codeberg` / `forgejo` / `gitea` / `cnb` |
| | `owner` | 必填 | 目标用户名或组织 (群组) 路径 |
| | `token_env` | 必填 | 令牌所在的环境变量名 |
| | `enabled` | `true` | `false` 暂时停用 |
| | `base_url` / `api_url` | 按平台 | 自建 GitLab 只改 `base_url`; 自建 Forgejo / Gitea 两个都要写 |
| | `username` | 自动 | 推送用的用户名, 默认用令牌查出来的登录名 |
| | `visibility` | 跟 `policy` | 只对这个平台覆盖可见性规则 |
| | `include` / `exclude` | `[]` | 只对这个平台过滤仓库 |
| | `rename` | `{}` | `源仓库名: 目标仓库名`, 目标平台不接受原名时用 |

加新平台 (比如自建 Forgejo) 时: 在 `targets` 加一段, 新建对应 secret, 再在 [工作流](../.github/workflows/mirror-sync.yml) 的「同步」步骤 `env` 里加一行 `FORGEJO_TOKEN: ${{ secrets.FORGEJO_TOKEN }}`.

### 第 5 步: 试跑, 再正式跑

1. Actions → 多平台镜像同步 → Run workflow, 勾选 `dry_run`. 只读: 列出每个仓库会新建、改可见性还是推送, 什么都不写.
2. 看 Summary 里的「目标平台」一栏, 每个平台都没有报「令牌或 owner 校验失败」就说明令牌和 owner 对了.
3. 取消 `dry_run` 再跑一次, 第一次会建仓并推送全部历史, 仓库多时要久一点. 之后每天自动增量同步.

## 本地运行

```sh
export MIRROR_GITHUB_TOKEN=$(gh auth token) GITLAB_TOKEN=glpat-xxx   # 只导出要用的平台
PYTHONPATH=mirror uv run --project mirror python -m mirror sync \
  --config mirror/config.yml --dry-run --reveal-private
```

去掉 `--dry-run` 就正式执行. `--repo-limit a,b` 只处理这几个仓库. 结果在 `mirror/output/report.md` 和 `report.json`, 源仓库的裸镜像缓存在 `.mirror-cache/`, 下次只拉增量.

测试:

```sh
cd mirror && PYTHONPATH=. uv run python -m unittest discover -s tests -v
```

## 各平台的限制

- **CNB**: OpenAPI 只能在建仓时设可见性, 不能事后修改. 已存在的 CNB 仓库可见性和源不一致时, 报告里会标 ⚠️ 提示你到网页上手动改. CNB 仓库只能建在组织下, `owner` 填组织路径 (可以是 `根组织/子组织`).
- **Gitee**: 新建公开仓库可能要求账号实名认证, 否则会报错; 遇到这种情况可以对 Gitee 单独设 `visibility: private`. Gitee 对仓库路径的字符要求比 GitHub 严 (例如 `.github` 这类以点开头的名字), 用 `exclude` 或 `rename` 处理. Gitee 简介最长 200 字, 超出部分截断.
- **GitLab**: 公开仓库不能放在私有群组里, 想继承公开属性时目标群组本身要是公开的. 新仓库第一次推送后 GitLab 会自动保护默认分支, 下一次同步会自动为它打开强制推送.
- **Codeberg**: 主要面向开源项目, 大量私有仓库备份可能不符合它的使用条款. 私有仓库多的话建议对 Codeberg 设 `exclude`, 或改用自建 Forgejo.
- **Git LFS**: 只同步 Git 对象, 不同步 LFS 大文件.
- **删除**: 源仓库删掉后, 目标仓库不会被删, 要手动处理.
