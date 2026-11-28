# 各平台配置教程

每个平台按同一个顺序走: **注册 → (可选) 建组织 → 生成令牌 → 存成 Actions secret → 改 config.yml**. 每个平台都给了两个链接: 注册入口, 和注册完马上打开的令牌页面.

> 小技巧: 没登录时直接点「令牌页面」链接, 平台会先跳到登录页并记住要回来的地址, 登录页上有注册入口. 在同一个浏览器里注册并登录后, 多数平台会把你送回令牌页面. 没送回来的话, 注册完再点一次令牌链接就行.

只用到的平台才需要配. 没有配 secret 的平台会被工作流自动跳过, 不算失败.

所有 secret 都加在这里: [HX-Workflows → Settings → Secrets and variables → Actions → New repository secret](https://github.com/HengXin666/HX-Workflows/settings/secrets/actions/new). 令牌只能放在 secret 里, 不要写进 `config.yml`.

---

## 0. GitHub (源仓库, 必需)

已经有 `HYGIENE_TOKEN` 就跳过这一节, 工作流会自动复用它.

1. 打开令牌页面 (已预填名字和 `repo` 权限): <https://github.com/settings/tokens/new?scopes=repo&description=hx-mirror-sync>
2. 确认勾选了 `repo`, 过期时间按需选 (选 No expiration 就不用定期换).
3. 点 **Generate token**, 复制 `ghp_...` (只显示一次).
4. 新建 secret: 名字 `MIRROR_GITHUB_TOKEN`, 值粘贴令牌.

---

## 1. GitLab

| 步骤 | 链接 |
| --- | --- |
| 注册入口 | <https://gitlab.com/users/sign_up> |
| 注册完立即打开 (已预填名字和权限) | <https://gitlab.com/-/user_settings/personal_access_tokens?name=hx-mirror-sync&scopes=api,write_repository> |
| 建群组 (可选) | <https://gitlab.com/groups/new> |

1. 注册后 GitLab 会问几个引导问题 (角色、用途), 随便选, 遇到「创建第一个项目」可以直接跳过.
2. 打开上面的令牌链接, 确认勾选了 **`api`** 和 **`write_repository`**. 过期时间 GitLab 最长一年, 到期前要来续.
3. 点 **Create token**, 复制 `glpat-...`.
4. 新建 secret: `GITLAB_TOKEN`.
5. `config.yml` 里把 `owner` 改成你的 GitLab 用户名; 想放进群组就写群组路径 (如 `hx-backup` 或 `hx/backup`):

   ```yaml
   - name: gitlab
     provider: gitlab
     owner: 你的GitLab用户名
     token_env: GITLAB_TOKEN
   ```

注意:

- 想继承「公开」的仓库, 目标群组本身也要是公开的, GitLab 不允许公开项目放在私有群组里.
- 推到群组时你在群组里至少要是 Maintainer; 改可见性建议是 Owner.
- 自建 GitLab: 把链接里的 `gitlab.com` 换成你的域名, 配置里加 `base_url: https://你的域名`.

---

## 2. Gitee

| 步骤 | 链接 |
| --- | --- |
| 注册入口 | <https://gitee.com/signup> |
| 注册完立即打开 | <https://gitee.com/profile/personal_access_tokens/new> |
| 建组织 (可选) | <https://gitee.com/organizations/new> |

1. 用手机号注册, 按提示绑定邮箱. 注册时填的「个人空间地址」就是之后的 `owner`, 不是昵称.
2. 打开令牌链接, 「私人令牌描述」填 `hx-mirror-sync`, 权限勾选 **`user_info`** 和 **`projects`**; 推到组织下再勾 **`groups`**.
3. 点 **提交**, 输入登录密码确认, 复制令牌 (只显示一次).
4. 新建 secret: `GITEE_TOKEN`.
5. `config.yml`:

   ```yaml
   - name: gitee
     provider: gitee
     owner: 你的个人空间地址   # 或组织的 path
     token_env: GITEE_TOKEN
   ```

注意:

- 新建**公开**仓库可能要求账号实名认证, 没认证会报错. 在 Gitee 个人设置里完成实名认证, 或者对 Gitee 单独设 `visibility: private`.
- Gitee 对仓库名的字符要求比 GitHub 严, 例如 `.github` 这类以点开头的名字. 用 `exclude: [".github"]` 或 `rename: { ".github": "dot-github" }` 处理.

---

## 3. Codeberg (或自建 Forgejo / Gitea)

| 步骤 | 链接 |
| --- | --- |
| 注册入口 | <https://codeberg.org/user/sign_up> |
| 注册完立即打开 | <https://codeberg.org/user/settings/applications> |
| 建组织 (可选) | <https://codeberg.org/org/create> |

1. 注册后先去邮箱点激活链接, 不激活的账号不能生成令牌.
2. 打开令牌链接, 在 **Generate new token** 里:
   - Token name 填 `hx-mirror-sync`
   - Repository and Organization access 选 **All**
   - 权限: **`repository` → Read and write**, **`user` → Read**; 推到组织下再加 **`organization` → Read and write**
3. 点 **Generate token**, 复制令牌 (只显示一次).
4. 新建 secret: `CODEBERG_TOKEN`.
5. `config.yml`:

   ```yaml
   - name: codeberg
     provider: codeberg
     owner: 你的Codeberg用户名   # 或组织名
     token_env: CODEBERG_TOKEN
   ```

注意:

- Codeberg 主要面向开源项目, 大量私有仓库备份可能不符合它的使用条款. 私有仓库多的话, 对 Codeberg 设 `include` 只放公开的, 或者改用自建 Forgejo.
- **自建 Forgejo / Gitea**: 链接里的 `codeberg.org` 换成你的域名 (令牌页面同样在 `/user/settings/applications`), 配置这样写, 并在 [工作流](../../.github/workflows/mirror-sync.yml) 「同步」步骤的 `env` 里加一行 `FORGEJO_TOKEN: ${{ secrets.FORGEJO_TOKEN }}`:

  ```yaml
  - name: my-forgejo
    provider: forgejo            # Gitea 写 gitea
    owner: hx
    token_env: FORGEJO_TOKEN
    base_url: https://git.example.com
    api_url: https://git.example.com/api/v1
  ```

---

## 4. CNB (cnb.cool)

| 步骤 | 链接 |
| --- | --- |
| 注册入口 (微信扫码即注册) | <https://cnb.cool/> |
| 注册完先建组织 (必需) | <https://cnb.cool/new/groups> |
| 然后立即打开 | <https://cnb.cool/profile/token/create> |

1. 打开 cnb.cool 点右上角登录, 用微信扫码, 第一次登录就是注册, 按提示设置用户名.
2. **CNB 的仓库只能建在组织下**, 所以先建一个组织 (例如 `hengxin666`). 想分层可以再建子组织, 路径就是 `根组织/子组织`.
3. 打开令牌链接, 名称填 `hx-mirror-sync`, 授权范围选你刚建的组织, 权限勾选:
   - **`account-profile:r`** (校验令牌)
   - **`group-resource:rw`** (在组织下建仓)
   - **`repo-basic-info:r`** (查仓库是否存在、可见性)
   - **`repo-code:rw`** (推送代码)
4. 点创建, 复制令牌.
5. 新建 secret: `CNB_TOKEN`.
6. `config.yml` 的 `owner` 写**组织路径**, 不是用户名:

   ```yaml
   - name: cnb
     provider: cnb
     owner: hengxin666            # 或 hengxin666/backup
     token_env: CNB_TOKEN
   ```

注意: CNB 的接口只能在建仓时设可见性, 不能事后修改. 已存在的 CNB 仓库可见性和源不一致时, 报告里会标 ⚠️, 需要到仓库「设置」页手动改.

---

## 5. 同步邮件 (可选)

想每天收到同步邮件, 在 secrets 里加 `SMTP_HOST`、`SMTP_USERNAME`、`SMTP_PASSWORD` 和收件邮箱 `MIRROR_MAIL_TO` (或复用 `SMTP_TO`). emall 已经配过 SMTP 的话只需要加 `MIRROR_MAIL_TO`, 甚至什么都不用加. 详见 [README 的「同步邮件」](../README.md#同步邮件).

---

## 6. 全部配好之后

1. 打开 [Actions → 多平台镜像同步](https://github.com/HengXin666/HX-Workflows/actions/workflows/mirror-sync.yml), 点 **Run workflow**, 勾选 **dry_run**.
2. 看这次运行的 **Summary**: 「目标平台」一栏里
   - `跳过: 没有配置 secret ...`: 这个平台的 secret 名字没对上, 或者还没加
   - `令牌或 owner 校验失败`: 令牌权限不够, 或 `owner` 写错了 (最常见: Gitee 写成了昵称, CNB 写成了用户名)
   - 没有出现在这一栏: 配置正确
3. 明细表里每个仓库会显示「新建 (私有)」「可见性改为公开」「推送分支和标签」这类计划, 确认没问题后取消 `dry_run` 再跑一次正式同步.

令牌过期或被撤销后, 下一次运行会在「目标平台」里报校验失败, 重新生成后到 secret 页面点该 secret 的 **Update** 替换即可.
