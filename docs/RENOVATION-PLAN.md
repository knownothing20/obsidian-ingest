# Obsidian Ingest 改造方案

> 方案版本：v1.0  
> 制定日期：2026-04-30  
> 状态：待执行

---

## 一、当前状态

| 项目 | 数值 |
|------|------|
| todo/ 待编译 MD | 2,887 |
| wiki/sources/ 已有页面 | 6（有实质内容） |
| archive/raw-archive/ 源文件 | 1,367 PDF（全部已转 MD） |
| 待修 Bug | 5 个（涉及 4 个文件） |
| Cron 状态 | 旧 cron 连续 18 次报错（`delivery` 配置缺失） |

**关键发现：**
- todo/ 全部为扁平的 `.md` 文件，无子目录，共 129 MB，平均 45.8 KB/个
- 100 个文件小于 100 bytes（空壳文件，需跳过）
- 1,076 组文件名有重复后缀（如 `xxx_00.md`、`xxx_01.md`），共涉及 2,340 个文件
- 现有 `queue_state.json` 记录的是 PDF→MD 转换队列（4,440 条任务），与本次 MD→wiki 编译任务不同
- 6 个已有 wiki 页面全部**缺少 `content_proof`** 字段，不符合质量标准

---

## 二、改造目标

> **一句话：** 让 2,887 个 MD 自动、可靠、不中断地变成高质量 wiki 页面，中途挂了能自动接上。

**质量标准（强制）：**
- 每个 wiki 页面必须包含 `content_proof`（至少一句原文完整原话）
- 每个 wiki 页面必须有 `核心观点`（LLM 提取，不能空壳）
- 验证不通过不允许入库

---

## 三、架构设计：三层保障

```
┌─────────────────────────────────────────┐
│ Layer 1: compile_queue.json (状态追踪)  │  ← 每个MD一条记录，断点可续
├─────────────────────────────────────────┤
│ Layer 2: Agent 持续工人 (认知层)        │  ← 读MD→提取观点→生成wiki→验证
├─────────────────────────────────────────┤
│ Layer 3: Cron 看门狗 (安全网)            │  ← 15分钟一眼，挂了就拉起来
└─────────────────────────────────────────┘
```

### 各层职责

| 层级 | 组件 | 职责 |
|------|------|------|
| 状态追踪 | `compile_queue.json` | 记录每个 MD 的处理状态、错误次数、最后进度时间 |
| 认知引擎 | Agent (SKILL.md) | 理解内容、提取观点、生成 wiki、调用验证 |
| 安全网 | Cron watchdog | 检测进度停滞、自动拉起处理、清理异常状态 |

---

## 四、新增/修改文件清单

### 4.1 新增文件

| 文件 | 用途 |
|------|------|
| `scripts/compile_queue.py` | 编译队列管理（扫描 todo/、状态追踪、统计、CLI 命令） |
| `scripts/wiki_generator.py` | wiki 页面生成辅助（从 MD 提取 front matter 信息、构建页面骨架） |

### 4.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `scripts/cli.py` | 新增 `compile` 子命令组（scan/status/pending/start/done/fail/skip/retry/stats），修复 **Bug#5**（`cmd_init` 创建旧目录结构） |
| `scripts/file_queue.py` | 修复 **Bug#1**（`_move_to_archive` 路径）、**Bug#2**（`skip_existing_md` 检查 `todo/`）、**Bug#7**（`_get_output_dir` fallback 到源目录） |
| `scripts/persistent_queue.py` | 修复 **Bug#3**（`scan_and_register` 加 `exclude_dirs`） |
| `scripts/migrator.py` | 修复 **Bug#6**（适配新目录结构 `todo/archive/md-archive`） |
| `SKILL.md` | 重写，整合编译流程、Agent 工作流、Cron 配置 |
| `config.yaml.example` | 新增 `compile_queue` / `watchdog` 配置段 |
| `local/config.yaml` | 同步新配置段 |

### 4.3 删除文件（不继续维护）

| 文件 | 原因 |
|------|------|
| `scripts/checkpoint.py` | 功能已被 `compile_queue.py` 覆盖，且状态机设计更优 |

---

## 五、compile_queue.py 设计

### 5.1 数据结构

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
    "todo/生财/202212副业实战手册.md": {
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

### 5.2 CLI 命令

```bash
# 扫描 todo/ 注册新任务（仅注册未收录的MD）
python cli.py compile scan

# 查看队列状态
python cli.py compile status

# 获取下一批待处理（默认50个）
python cli.py compile pending [--limit 50]

# 标记某个文件开始处理
python cli.py compile start "todo/xxx.md"

# 标记某个文件处理完成
python cli.py compile done "todo/xxx.md"

# 标记某个文件处理失败
python cli.py compile fail "todo/xxx.md" --reason "内容为空"

# 标记跳过（不需要生成wiki）
python cli.py compile skip "todo/xxx.md"

# 重置所有失败任务（attempts 归零，status→pending）
python cli.py compile retry

# 统计摘要
python cli.py compile stats
```

### 5.3 状态机

```
pending → processing → done → (MD移至 archive/md-archive/)
         ↘ failed     (attempts+1, ≤3次可 retry)
         ↘ skipped    (不需要处理，如空文件)
```

**状态说明：**
- `pending`：初始状态，扫描后注册
- `processing`：Agent 正在处理，处理完成后必须立即更新为 `done` 或 `failed`
- `done`：处理成功，MD 已移至 `archive/md-archive/`
- `failed`：处理失败（LLM 错误、内容损坏等），可重试
- `skipped`：明确不需要处理（空文件、内容无关等）

### 5.4 扫描逻辑

```python
def scan_todo_register(self) -> ScanResult:
    """扫描 todo/ 目录，注册所有 .md 文件到队列"""
    # 排除：
    #   - 已有记录且 status ≠ failed 的文件（跳过，避免重复）
    #   - 大小 < 100 bytes 的文件（自动标记 skipped）
    #   - 已存在于 queue 且非 failed 的文件（幂等保护）
    #
    # 新增：
    #   - 检查 compile_queue.json 中是否已有记录
    #   - 不依赖 file_queue 的任何逻辑（独立队列）
```

**关键设计决策：**
- `compile_queue.json` **独立于** `queue_state.json`，不混用
- 任务 ID 为 MD 文件的相对路径（不含 `todo/` 前缀）
- 扫描是**幂等的**，已处理的文件不会被重复注册

---

## 六、SKILL.md 工作流（Agent 认知层）

### 触发方式

Cron watchdog 触发 **或** 手动调用 `compile pending`。

### 完整流程

```
1. 读取 compile_queue.json → 获取 pending 列表
2. 取一批（默认50个）→ 逐个标记 processing
3. 对每个 MD:
   a. 读取全文内容
   b. 如果 size < 100 bytes → compile skip → 下一文件
   c. 提取 content_proof（至少一句原文完整原话）
   d. 分析内容类型 → 判断目标目录（sources/entities/concepts）
   e. 按模板生成 wiki 页面
   f. 写入 wiki/ 对应目录
   g. 运行 validate_wiki.py 验证
   h. 验证通过 → compile done → MD 移至 archive/md-archive/
   i. 验证失败 → 重新生成（最多2次）→ 仍失败 → compile fail
4. 一批处理完 → 汇报（✅N个 ❌N个 ⏳剩余N个）
5. 还有 pending → 继续下一批（不等待 cron）
6. 队列清空 → 汇报完成，退出
```

### 内容分类规则

```
文件类型判断（基于内容分析）：
  - 人物/公司/品牌介绍   → wiki/entities/
  - 概念/术语/方法论     → wiki/concepts/
  - 报告/案例/实战记录   → wiki/sources/
  - 会议/日志/过程记录   → wiki/logs/
  - 其他综合内容         → wiki/syntheses/
```

### wiki 页面模板

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

### 关键约束（写入 SKILL.md 强制执行）

1. **禁止空壳页面**：必须有 `content_proof` + 核心观点，否则验证不通过
2. **状态实时持久化**：每处理完一个文件**必须立即**调用 `compile done/fail`（不允许累积到批结束）
3. **验证不过不入库**：`validate_wiki.py` 返回非0则不允许写入 wiki
4. **重试有限制**：同一文件最多处理 3 次（含初试），3 次失败则标记 `failed`，不再自动重试
5. **MD 归档而非删除**：处理完成的 MD 移至 `archive/md-archive/`，不删除原始文件

---

## 七、Cron 看门狗配置

### 配置参数

| 项 | 值 |
|----|----|
| 间隔 | 每 15 分钟 |
| 类型 | `isolated` agentTurn |
| 超时 | 30 分钟 |
| 触发条件 | `pending > 0` 且 `last_progress` 超过 15 分钟 |
| 空转行为 | 读 queue → 无任务 → **NO_REPLY**（≈0 开销） |

### Cron Watchdog Prompt

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

### 修复旧 cron

旧 cron `obsidian-ingest-batch` 的 `delivery` 配置缺少 `to` 字段，导致每次报错：

```json
// 错误配置
{ "mode": "announce", "channel": "feishu" }

// 修复为（已有 to 字段时保留，补充 to 说明）
{ "mode": "announce", "channel": "feishu", "to": "<chatId>" }
```

---

## 八、Bug 修复清单（与改造同步执行）

| # | 文件 | Bug 描述 | 修复方案 |
|---|------|----------|----------|
| 1 | `file_queue.py` | `_move_to_archive` 路径拼错：`raw/archive/raw-archive/` | 改为 `f"{archive_dir}/{sub_path}"`，确保子路径正确拼接 |
| 2 | `file_queue.py` | `skip_existing_md` 在**源目录**找 MD | 改为检查 `todo/` 目录，因为 MD 源文件在 `todo/` |
| 3 | `persistent_queue.py` | `scan_and_register` 缺少 `exclude_dirs` 过滤 | 从 `config['dirs']['exclude']` 读取并应用 |
| 5 | `cli.py` | `cmd_init` 创建旧目录结构（`archive/md-archive/` 在顶层） | 改为正确的 `raw/todo/archive/raw-archive/` + `archive/md-archive/` |
| 6 | `migrator.py` | 不适配新目录结构，无法迁移已完成的 MD | 重写，支持 `todo/ → wiki/` + `archive/md-archive/` 归档路径 |
| 7 | `file_queue.py` | `_get_output_dir` 空值时 fallback 到**源目录** | 始终返回 `todo_dir`，不再 fallback |

**注：** Bug#4 未列出，暂未识别具体问题。

---

## 九、执行顺序

### Phase 1：基础设施（第 1-2 天）

- [ ] 修复 Bug #1, #2, #3, #5, #6, #7
- [ ] 创建 `compile_queue.py`
- [ ] 创建 `wiki_generator.py`
- [ ] 更新 `cli.py` 添加 `compile` 子命令
- [ ] 更新 `config.yaml.example` 新增配置段
- [ ] 更新 `local/config.yaml` 同步配置

### Phase 2：SKILL.md + Cron（第 2 天）

- [ ] 重写 `SKILL.md`（完整工作流 + 质量标准）
- [ ] 配置新 Cron watchdog
- [ ] 修复旧 cron `obsidian-ingest-batch` 的 delivery 配置
- [ ] 删除 `checkpoint.py`（功能已被 `compile_queue.py` 取代）

### Phase 3：试运行（第 2-3 天）

- [ ] `compile scan` → 注册 2,887 个 MD
- [ ] 手动处理 10 个 → 验证全流程
- [ ] 确认 `validate_wiki.py` 通过
- [ ] 确认 MD 正确移至 `archive/md-archive/`
- [ ] 确认 Cron watchdog 在空闲时输出 NO_REPLY

### Phase 4：全量运行（第 3-4 天起）

- [ ] 触发 Agent → 持续处理
- [ ] Cron watchdog 兜底
- [ ] 定期检查失败率，超过 5% 则暂停并分析原因

---

## 十、预期结果

| 指标 | 预期 |
|------|------|
| 处理总量 | 2,887 个 MD → ~2,700+ wiki/sources 页面（100 个空壳跳过，剩余容错） |
| 处理速度 | 每分钟约 3-5 个（受 LLM 响应速度限制） |
| 全部完成预计 | 10-16 小时（含自动重试） |
| 中途挂了最多损失 | 15 分钟，Cron 自动接续 |
| 并发模型 | 单 Agent 持续处理，不并行（避免写入冲突） |

---

## 十一、已知风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| LLM 生成质量不稳定 | `validate_wiki.py` 强制校验 + 最多 2 次重生成 |
| 同一源文件生成多个 wiki 页面（多分段） | 状态机去重：`status=done` 的文件不重复处理 |
| 大量 failed 文件积压 | 定期分析失败原因，分类处理（可跳过/可修复/确实处理不了） |
| Cron 触发时 Agent 正在处理 | 通过 `processing` 状态检测，如果 pending=0 但 processing>0 则跳过 |
| 2,887 个文件过大导致单次超时 | 分批处理（50个/批），每批独立提交状态 |
