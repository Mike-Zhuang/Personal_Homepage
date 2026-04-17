# 宝塔自动拉取冲突处理手册（给后续 Agent）

本手册专门记录一个高频事故：宝塔面板执行 `git pull` 时，仓库工作区因为线上内容编辑而变脏，随后 `autostash` 回放冲突，导致 Hugo 读取到冲突标记并构建失败。

## 1. 典型报错特征

出现以下任一信号，优先怀疑是同一类问题：

```text
Created autostash
Applying autostash resulted in conflicts.
```

以及后续 Hugo 报错：

```text
invalid character at start of key: <
```

这通常意味着 `data/*.toml` 里已经混入了 Git 冲突标记 `<<<<<<<`.

## 2. 根因

根因不是 Hugo，也不是模板本身，而是 **同一份 git 工作区同时承担了两种职责**：

1. 代码仓库
2. 线上可写内容目录

当 admin API 把内容直接写进 `/opt/personal-homepage/data/*.toml` 时，git 工作区就会变脏。  
宝塔面板自己的部署逻辑会直接执行：

```bash
cd /opt/personal-homepage && git pull origin main
```

一旦远端也改了同一文件，`git pull` 就会自动 stash 本地改动，再回放；如果回放命中同一位置，就会把冲突标记写回 TOML 文件，后续构建必炸。

## 3. 正确方案

必须把线上可编辑内容挪出 git 工作区。

### 目标目录

推荐使用：

```bash
/opt/personal-homepage/runtime/live-data
```

### 运行时规则

1. FastAPI admin 的 `DATA_ROOT` 指向 `runtime/live-data`
2. git 仓库里的 `/opt/personal-homepage/data` 只保留仓库基线，不再作为线上可写内容目录
3. 发布脚本构建时使用临时工作目录，把 `runtime/live-data` 覆盖进临时目录下的 `data/`，然后在临时目录运行 `hugo`
4. 这样宝塔 `git pull` 面对的是干净仓库，不再与线上内容编辑打架

## 4. 标准修复步骤

### 4.1 先看现场

```bash
cd /opt/personal-homepage
git status --short
git stash list
sed -n '1,120p' data/site.toml
```

如果看到：

- `UU data/site.toml`
- `<<<<<<< Updated upstream`

说明已经发生了 autostash 冲突。

### 4.2 备份线上内容

```bash
mkdir -p /opt/personal-homepage/runtime/manual-backups
cp -a /opt/personal-homepage/data /opt/personal-homepage/runtime/manual-backups/data-$(date +%Y%m%dT%H%M%S)
```

### 4.3 创建外置内容目录

```bash
mkdir -p /opt/personal-homepage/runtime/live-data
rsync -a /opt/personal-homepage/data/ /opt/personal-homepage/runtime/live-data/
chown -R www:www /opt/personal-homepage/runtime/live-data
```

### 4.4 修改真实环境变量

编辑：

```bash
/opt/personal-homepage/deploy/env/api.env
```

确保：

```bash
DATA_ROOT=/opt/personal-homepage/runtime/live-data
```

### 4.5 保证发布脚本已支持“临时构建目录”

仓库里的 `deploy/scripts/publish-content.sh` 必须具备以下行为：

1. 读取 `deploy/env/api.env`
2. 如果 `DATA_ROOT != $PROJECT_ROOT/data`，则创建临时构建目录
3. 把仓库代码 rsync 到临时目录
4. 再把 `DATA_ROOT` 覆盖到临时目录的 `data/`
5. 在临时目录运行 `hugo --gc --minify`
6. 最后把临时目录的 `public/` 同步到站点目录

## 5. 冲突后的收尾

在完成内容迁移后，需要把 git 工作区恢复干净：

```bash
cd /opt/personal-homepage
git restore --source=HEAD --staged --worktree data/site.toml data/now.toml
git stash drop stash@{0}   # 仅在确认外置内容已接管后执行
git status --short
```

注意：如果 stash 里还有用户未合并的真实内容，先手动合并到 `runtime/live-data/*.toml`，再 drop。

## 6. 后续 Agent 禁止事项

1. 禁止再让线上 admin 直接写 git 工作区里的 `data/*.toml`
2. 禁止在工作区脏的情况下直接点击宝塔“拉取”
3. 禁止把用户线上改过的内容只留在 stash 里不处理
4. 禁止把真实密钥或线上内容备份提交回仓库

## 7. 快速判断现在是否已经安全

同时满足以下条件，才算修好：

```bash
cd /opt/personal-homepage
git status --short
systemctl restart personal-homepage-api
/opt/personal-homepage/deploy/scripts/publish-content.sh
curl -fsS http://127.0.0.1:8000/api/health
```

验收标准：

1. `git status --short` 为空
2. `publish-content.sh` 构建成功
3. admin 保存后，修改落在 `runtime/live-data/*.toml`
4. 下一次宝塔 `git pull` 不再出现 `Created autostash`
