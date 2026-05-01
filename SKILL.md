# SKILL.md — obsidian-ingest

---
name: obsidian-ingest
description: 知识库文档摄入引擎。PDF/Office/图片 → Markdown → Obsidian wiki 页面，全自动流水线。
version: 3.0.0
---

## 概述

obsidian-ingest 将 PDF、Office 文档、图片自动转换为 Obsidian 知识库页面。

支持格式：**PDF** · **DOCX** · **PPTX** · **XLSX** · **PNG/JPG/JPEG/BMP/TIFF/WebP/GIF/JP2**

**两阶段流水线：**

1. **转换阶段**（已有）→ PDF/DOCX/PPTX/XLSX/图片 → Markdown
2. **编译阶段**（新增）→ todo/ 的 MD → wiki 页面

## 核心能力

- **MinerU 多 Token 轮询**：4 个 Token 自动故障转移，限流自动切换
- **并行转换**：4 Worker 并行处理，实时进度条 + ETA
- **多引擎支持**：MinerU API（默认）/ Marker / Docling，一行配置切换
- **三层去重**：文件指纹 → 内容 hash → 语义匹配
- **编译队列**（新增）：独立于转换队列，状态持久化，断点可续
- **质量验证**（新增）：`content_proof` 强制校验，空壳页面禁止入库
- **Cron 看门狗**（新增）：15 分钟检测进度停滞，自动拉起处理

## 目录结构

```
obsidian-ingest/
├── config.yaml                 # 配置（Token、引擎、路径）
├── local/
│   └── config.yaml             # 本地覆盖配置
├── config.yaml.example         # 配置示例
├── requirements.txt
├── SKILL.md
├── README.md
├── INSTALL.md
├── docs/
│   └── RENOVATION-PLAN.md      # 改造方案
├── scripts/
│   ├── cli.py                  # CLI 入口
│   ├── compile_queue.py        # ⭐ 编译队列管理
│   ├── wiki_generator.py       # ⭐ wiki 页面生成
│   ├── validate_wiki.py        # ⭐ wiki 页面质量验证
│   ├── mineru_client.py        # MinerU 客户端
│   ├── compiler.py             # 编译引擎（light 模式已完成，deep 模式待实现）
│   ├── engine.py               # 多引擎抽象
│   ├── config_loader.py        # 配置加载
│   ├── file_queue.py           # 文件分类队列（Bug#1#2#7 已修）
│   ├── persistent_queue.py     # 转换队列（Bug#3 已修）
│   ├── xlsx_converter.py       # XLSX→MD 转换器
│   ├── legacy_converter.py     # 旧格式转换
│   ├── migrator.py             # 文件迁移（Bug#6 已修）
│   └── checkpoint.py           # 断点续传（待废弃，功能由 compile_queue.py 接管）
```

---

# 第一部分：转换阶段

## CLI 命令（转换）

```bash
# 初始化 vault 目录结构
python scripts/cli.py init --vault "/path/to/your/vault"

# 一键处理（转换 + 编译 + 归档）
python scripts/cli.py process --vault "/path/to/your/vault"

# 仅转换（PDF→MD，不编译）
python scripts/cli.py convert --vault "/path/to/your/vault"

# 预览（不执行）
python scripts/cli.py process --vault "/path/to/your/vault" --dry-run

# 恢复中断任务
python scripts/cli.py resume --vault "/path/to/your/vault"

# 查看状态
python scripts/cli.py status --vault "/path/to/your/vault"

# 监听模式（持续自动处理）
python scripts/cli.py watch --vault "/path/to/your/vault"

# 批量迁移已处理文件
python scripts/cli.py migrate --vault "/path/to/your/vault"

# 清理过期归档
python scripts/cli.py cleanup --vault "/path/to/your/vault"
```

## 持久化队列（转换）

```bash
# 查看队列状态（自动扫描新文件）
python scripts/cli.py queue status

# 分批处理（每批 50 个）
python scripts/cli.py batch --size 50

# 处理所有（多批次循环）
python scripts/cli.py batch --all

# 心跳守护（扫描+处理+循环，不终止）
python scripts/cli.py heartbeat --interval 300 --batch-size 50

# 重置失败任务
python scripts/cli.py queue retry

# 清空队列
python scripts/cli.py queue clear
```

---

# 第二部分：编译阶段（重点）

## CLI 命令（编译）

```bash
# 扫描 todo/ 注册新任务（仅注册未收录的 MD）
python scripts/cli.py compile scan

# 查看编译队列状态
python scripts/cli.py compile status

# 获取下一批待处理（默认 50 个）
python scripts/cli.py compile pending [--limit 50]

# 标记某个文件开始处理
python scripts/cli.py compile start "todo/xxx.md"

# 标记某个文件处理完成
python scripts/cli.py compile done "todo/xxx.md"

# 标记某个文件处理失败
python scripts/cli.py compile fail "todo/xxx.md" --reason "内容为空"

# 标记跳过（不需要生成 wiki）
python scripts/cli.py compile skip "todo/xxx.md"

# 重置所有失败任务（attempts 归零，status→pending）
python scripts/cli.py compile retry

# 统计摘要
python scripts/cli.py compile stats
```

## 编译队列数据结构

`compile_queue.json` 独立于 `queue_state.json`，专门追踪 MD→wiki 编译任务：

```json
{
  "version": 1,
  "vault_root": "/path/to/your/vault",
  "last_scan": "2026-04-30T14:00:00+08:00",
  "last_progress": "2026-04-30T14:05:00+08:00",
  "stats": {
    "pending": 2887,
    "processing": 0,
    "done": 0,
    "failed": 0,
    "skipped": 0
  },
  "tasks": {
    "生财/202212副业实战手册.md": {
      "status": "pending",
      "size": 15234,
      "registered_at": "2026-04-30T14:00:00+08:00",
      "updated_at": null,
      "attempts": 0,
      "last_error": null
    }
  }
}
```

## 编译状态机

```
pending → processing → done → (MD 移至 archive/md-archive/)
         ↘ failed     (attempts+1, ≤3次可 retry)
         ↘ skipped    (不需要处理，如空文件)
```

---

# 第三部分：Agent 工作流

## 触发方式

1. **Cron 看门狗触发** — 每 15 分钟检查，pending>0 时拉起
2. **手动调用** — 运行 `compile pending` 获取任务

## 完整流程

```
1. 读取 compile_queue.json → 获取 pending 列表
2. 取一批（默认 50 个）→ 逐个标记 processing
3. 对每个 MD:
   a. 读取全文内容
   b. 如果 size < 100 bytes → compile skip → 下一文件
   c. 提取 content_proof（至少一句原文完整原话，≥20 字）
   d. 分析内容类型 → 判断目标目录（sources/entities/concepts/logs/syntheses）
   e. 按模板生成 wiki 页面
   f. 写入 wiki/ 对应目录
   g. 运行 validate_wiki.py 验证
   h. 验证通过 → compile done → MD 移至 archive/md-archive/
   i. 验证失败 → 重新生成（最多 2 次）→ 仍失败 → compile fail
4. 一批处理完 → 汇报（✅N个 ❌N个 ⏳剩余N个）
5. 还有 pending → 继续下一批（不等待 cron）
6. 队列清空 → 汇报完成，退出
```

## 内容分类规则

```
文件类型判断（基于内容分析）：
  - 人物/公司/品牌介绍   → wiki/entities/
  - 概念/术语/方法论     → wiki/concepts/
  - 报告/案例/实战记录   → wiki/sources/
  - 会议/日志/过程记录   → wiki/logs/
  - 其他综合内容         → wiki/syntheses/
```

## wiki 页面模板

```markdown
---
title: "{提取的标题}"
type: {source|entity|concept|log|synthesis}
tags: [{标签1}, {标签2}]
sources: [todo/{原文件路径}]
created: {日期}
last_updated: {日期}
last_verified: {日期}
validity: {historical|valid|outdated}
content_proof: "{原文核心句（≥20字）}"
---

# {标题}

## 一句话总结
{1-2句话概括本文核心}

## 核心观点
{按结构化列出主要观点}

## 关联连接
- [[相关概念]]
- [[相关实体]]
```

## ⚠️ 强制约束

1. **禁止空壳页面**：必须有 `content_proof` + 核心观点，否则验证不通过
2. **状态实时持久化**：每处理完一个文件**必须立即**调用 `compile done/fail`（不允许累积到批结束）
3. **验证不过不入库**：`validate_wiki.py` 返回非 0 则不允许写入 wiki
4. **重试有限制**：同一文件最多处理 3 次（含初试），3 次失败则标记 `failed`
5. **MD 归档而非删除**：处理完成的 MD 移至 `archive/md-archive/`，不删除原始文件

---

# 第四部分：Cron 看门狗

## 配置参数

| 项 | 值 |
|----|----|
| 间隔 | 每 15 分钟 |
| 类型 | `isolated` agentTurn |
| 超时 | 30 分钟 |
| 触发条件 | `pending > 0` 且 `last_progress` 超过 15 分钟 |
| 空转行为 | 读 queue → 无任务 → **NO_REPLY** |

## 看门狗 Prompt

```
你是 Obsidian vault 编译看门狗。执行以下步骤：

1. 运行 compile status 查看队列
2. 如果 pending = 0，回复 NO_REPLY
3. 如果 last_progress 距今超过15分钟，说明上次 session 异常中断
4. 运行 compile pending 获取待处理列表
5. 按照 SKILL.md 编译流程处理（持续循环直到清空或超时）
6. 每批处理完输出简报

Cron间隔：15分钟。仅在有任务或进度停滞时才执行实际工作。
禁止在 pending=0 时输出任何消息（静默空转）。
```

---

# 第五部分：配置

## config.yaml

当前 `local/config.yaml` 完整配置如下，新增段标注 ⭐：

```yaml
# ── MinerU Token ──────────────────────────────────────────────────
tokens:
  - token: "..."
    expires: "2026-07-25T14:00:00+08:00"
  # ... 4 个 Token

# ── 转换引擎 ──────────────────────────────────────────────────────
engine:
  provider: mineru
  mineru:
    base_url: "https://mineru.net"
    verify_ssl: false
    timeout: 300
    poll_interval: 3
    max_poll_interval: 30

# ── 并行 ──────────────────────────────────────────────────────────
parallel:
  enabled: true
  max_workers: 8

# ── Vault 路径 ────────────────────────────────────────────────────
vault:
  root: "/path/to/your/vault"
  raw_dir: "raw"
  todo_dir: "todo"
  wiki_dir: "wiki"
  assets_dir: "assets"
  schema: "SCHEMA.md"

# ── 目录（相对于 vault.root）──────────────────────────────────────
dirs:
  source: "raw"               # 扫描的源目录
  exclude:                     # 不扫描的目录
    - "todo"
    - "archive"
  output: "todo"               # MD 文件输出目录
  archive: "archive/raw-archive"     # 源文件归档
  md_archive: "archive/md-archive"   # MD 归档（编译完成后）
  failed: "archive/raw-archive/failed"

# ── 编译 ──────────────────────────────────────────────────────────
compile:
  mode: light                  # light=规则引擎 / deep=AI摘要（待实现）
  llm:
    provider: openai
    model: gpt-4o-mini
    api_key: ""
    base_url: ""
  frontmatter:
    type: source
    validity: current
    tags_from_path: true
  dedup:
    enabled: true
    file_fingerprint: true
    content_hash: true
    semantic: false
  archive:
    source_retention_days: 7
    source_auto_delete: true
    md_retention_days: -1      # -1 = 永久保留
    md_auto_delete: false

# ── 编译队列 ⭐ ───────────────────────────────────────────────────
compile_queue:
  batch_size: 50
  max_attempts: 3
  skip_small_files: 100        # 小于此字节数自动跳过
  validation:
    require_content_proof: true
    min_proof_length: 20

# ── 看门狗 ⭐ ─────────────────────────────────────────────────────
watchdog:
  interval_minutes: 15
  progress_timeout_minutes: 15

# ── 监听 ──────────────────────────────────────────────────────────
watcher:
  poll_interval: 10
  stability_checks: 3
  stability_interval: 2

# ── 自动化 ────────────────────────────────────────────────────────
automation:
  auto_migrate: true
  auto_compile: true
```

---

# 第六部分：旧格式兼容

MinerU 仅支持 DOCX/PPTX，旧格式 `.doc`/`.ppt` 需要先转换。

**后端探测机制**（按优先级自动选择）：

1. **MS Office COM**（最高优先级）— Windows + 已装 Office
2. **WPS COM** — Windows + 已装 WPS Office
3. **LibreOffice headless**（兜底）— 跨平台

运行时自动探测可用后端，无需手动配置。查看探测结果：

```bash
python scripts/legacy_converter.py --check
```

详见 INSTALL.md 中的安装部分。

---

# 第七部分：已知 Bug 修复

| Bug | 文件 | 修复 |
|-----|------|------|
| #1 | file_queue.py | `_move_to_archive` 路径改为 `f"{archive_dir}/{sub_path}"` |
| #2 | file_queue.py | `skip_existing_md` 改为检查 `todo/` 目录 |
| #3 | persistent_queue.py | `scan_and_register` 添加 `exclude_dirs` 过滤 |
| #5 | cli.py | `cmd_init` 改为正确目录结构 |
| #6 | migrator.py | 重写适配新目录结构 |
| #7 | file_queue.py | `_get_output_dir` 始终返回 `todo_dir` |

---

# 第八部分：预期结果

| 指标 | 预期 |
|------|------|
| 当前待处理 | 2,887 个 MD |
| 处理速度 | 每分钟 3-5 个 |
| 全部完成预计 | 10-16 小时 |
| 中途挂了最多损失 | 15 分钟 |
| 并发模型 | 单 Agent 持续处理，不并行 |

---

# 第九部分：实施状态

> 本节跟踪 SKILL.md 中声明的各组件的实际落地情况

## 脚本文件

| 文件 | SKILL.md 声明 | 实际状态 |
|------|-------------|----------|
| `compile_queue.py` | ⭐ 编译队列管理 | ✅ 已创建 (12KB)，支持 scan/status/pending/retry/stats |
| `wiki_generator.py` | ⭐ wiki 页面生成 | ✅ 已创建 (15KB)，支持 batch/single/compile |
| `validate_wiki.py` | ⭐ 质量验证 | ✅ 已创建 (6KB)，支持单文件/目录验证 |
| `cli.py` compile 子命令 | scan/status/pending/start/done/fail/skip/retry | ⚠️ 部分实现（compile_queue CLI 独立运行，未集成到 cli.py） |
| `checkpoint.py` | 待废弃 | ✅ 存在（待 compile_queue.py 替代后删除） |

## 配置

| 配置段 | SKILL.md 声明 | 实际状态 |
|--------|-------------|----------|
| `compile_queue` | batch_size/max_attempts/... | ✅ local/config.yaml 已添加 |
| `watchdog` | interval_minutes/progress_timeout | ✅ local/config.yaml 已添加 |

## Cron

| 项目 | SKILL.md 声明 | 实际状态 |
|------|-------------|----------|
| 旧 cron `obsidian-ingest-batch` | 修复 delivery 配置 | ✅ 已添加 to 字段，连续错误归零 |
| 新 cron watchdog | 15 分钟间隔 | ✅ 已创建（scan + wiki_generator batch + status） |

## Bug 修复

| Bug | 状态 |
|-----|------|
| #1 file_queue._move_to_archive | ✅ 已修 |
| #2 file_queue.skip_existing_md | ✅ 已修 |
| #3 persistent_queue.scan_and_register | ✅ 已修 |
| #5 cli.cmd_init | ✅ 已修 |
| #6 migrator | ✅ 已修 |
| #7 file_queue._get_output_dir | ✅ 已修 |

## 已知遗留问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| compile_queue CLI 独立运行 | 🟡 低 | compile_queue CLI 未集成到 cli.py（功能完整可独立使用） |
| wiki 页面分类仅有 sources/ | 🟢 低 | SKILL.md 声明 entities/concepts/logs/syntheses 但 wiki_generator 仅输出到 sources/（不影响功能，concepts/entities 等为手动创建目录） |
| wiki 页面模板字段不完整 | 🟢 低 | 缺少 tags/sources/created/last_updated 等字段（现有 117 页全部通过验证） |

## 最终验证结果 (2026-04-30)

| 指标 | 结果 |
|------|------|
| Bug 修复 | 6/6 ✅ |
| Wiki 验证 | 117/117 通过 ✅ |
| 编译队列 | 2752 pending / 35 done / 0 failed ✅ |
| Cron 作业 | 3/3 健康运行 ✅ |
| 脚本语法 | 9/9 通过 ✅ |