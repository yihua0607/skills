# 发布 skill 更新到 GitHub（yihua0607/skills.git）

本地 `/opt/data/skills/` **不是 git 仓库**（无 .git，`hermes skills list` 显示为 local 来源）。
共享 skill（quotation-generator、indonesia-business-visa-review）的更新要分发到其他用户，
需手动同步到 https://github.com/yihua0607/skills.git。

## 完整流程

```bash
# 1. 克隆仓库到临时目录（每次从最新 main 开始，避免覆盖他人提交）
cd /tmp && rm -rf skills-sync && git clone https://github.com/yihua0607/skills.git skills-sync

# 2. 对比本地与仓库差异（只看内容差异，忽略本地多出的文件）
cd /tmp/skills-sync/quotation-generator
diff -rq /opt/data/skills/quotation-generator . 2>&1 | grep -v "^Only in /opt/data"

# 3. 复制变更文件到克隆目录（只复制 diff 出的文件，不要整目录覆盖）
cp /opt/data/skills/quotation-generator/SKILL.md ./SKILL.md
cp /opt/data/skills/quotation-generator/scripts/build_quotation.py ./scripts/build_quotation.py
# ... 其他变更文件同理

# 4. 提交 + 推送
cd /tmp/skills-sync
git config user.name "yihua0607"
git config user.email "yihua0607@users.noreply.github.com"
git add quotation-generator/ && git commit -m "quotation-generator vX.Y.Z: <变更摘要>"
git push origin main
```

## 凭据（重要）

服务器已配置持久化凭据（2026-08-05）：
- `~/.git-credentials` 存有 classic token（`ghp_` 前缀），权限 600
- `git config --global credential.helper store` 已启用，push 到 github.com 自动认证，**无需再向用户索取 token**
- 若换环境/凭据失效导致 push 报 `could not read Username`，才需要重新向用户（刘旭 / huaweicloud）索取 token

### token 类型经验（403 排查）

| token 前缀 | 说明 |
|-----------|------|
| `ghp_`（classic PAT） | 可直接用于 HTTPS push |
| `github_pat_`（fine-grained PAT） | 即使 `GET /repos/{owner}/{repo}` 返回 `permissions.push: true`（那是账号权限，不是 token 权限），git push 仍可能 403。用 API 写入测试确认：`curl -X PUT -H "Authorization: Bearer <token>" https://api.github.com/repos/{owner}/{repo}/contents/_test.txt -d '{"message":"t","content":"aGk="}'`，返回 `Resource not accessible by personal access token` = token 缺 Contents write 权限，需用户在 GitHub token 设置里把 **Contents 改为 Read and write** |

### token 安全

- URL 内嵌 token push：`git remote set-url origin "https://x-access-token:<token>@github.com/..."`，**推送成功后必须立即** `git remote set-url origin "https://github.com/..."` 清理，避免 token 残留在 `.git/config`
- 持久化用 `~/.git-credentials`（`echo "https://x-access-token:<token>@github.com" > ~/.git-credentials && chmod 600 ~/.git-credentials`），不要明文写进 memory

## 拉取仓库更新到本地（反向同步）

用户更新了仓库（如新增实体/模板/脚本）时，把远端版本同步回 `/opt/data/skills/`：

```bash
# 1. 拉取最新（复用已有 /tmp/skills-sync，没有则先 clone）
cd /tmp/skills-sync && git pull origin main && git log --oneline -3

# 2. 对比本地与远端差异
diff -rq /opt/data/skills/quotation-generator quotation-generator 2>&1 | grep -v ".venv"

# 3. 同步：整目录复制变更，注意保留本地独有文件
cp -r quotation-generator/* /opt/data/skills/quotation-generator/
cp -r quotation-generator/assets/新模板.docx /opt/data/skills/quotation-generator/assets/

# 4. 校验：再 diff 一次，剩余差异应只有 .venv / .DS_Store / 本地独有 references
```

⚠️ **不要覆盖本地独有文件**：`.venv/`（虚拟环境）、本地新增的 `references/bulk-extraction-pattern.md`、`references/publish-to-github.md` 等未推送的文件。`diff -rq` 输出中 `Only in /opt/data` 的行就是本地独有，直接忽略；远端新文件（`Only in quotation-generator`）要复制过来。

## 版本号纪律

每次 SKILL.md 内容变更（修复/规则/新增条目）都要：
1. 升 version（patch 号 +1）
2. 更新 last_updated 为当天
3. 变更摘要写进 commit message
仓库落后多个版本时（如仓库 1.8.0 vs 本地 1.8.6），一次性把累积 diff 全部提交即可，无需逐版本提交。
