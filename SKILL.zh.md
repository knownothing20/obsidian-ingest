# SKILL.md — obsidian-ingest (中文版)
---

# 中文文档

## 概述

obsidian-ingest 将 PDF、Office 文档、图片自动转换为 Obsidian 知识库页面。

支持格式：**PDF** · **DOCX** · **PPTX** · **XLSX** · **PNG/JPG/JPEG/BMP/TIFF/WebP/GIF/JP2**

完整流水线：**源文件 → MD → 清洗 → Front Matter → 摘要 → 排重 → Index → 归档**

## 核心能力

- **MinerU 多 Token 轮询**：4 个 Token 自动故障转移，限流自动切换
- **持久化任务队列**：JSON 存储，自动重试（最多 3 次），死信标记，心跳检测
- **文件分类路由**：PDF→MinerU、DOC/PPT→COM 转换→MinerU、XLSX→openpyxl、图片→OCR
- **多引擎支持**：MinerU API（默认）/ Marker / Docling，一行配置切换
- **断点续传**：每 10 个文件自动保存，Ctrl+C 安全退出，下次启动自动恢复
- **三层去重**：文件指纹 → 内容 hash → 语义匹配
- **自动编译**：格式清洗、Front Matter 注入、摘要生成、双链关联
- **旧格式转换**：.doc→.docx、.ppt→.pptx，支持 MS Office COM / WPS COM / LibreOffice
- **生命周期管理**：源文件→archive，MD→wiki，7 天后自动清理
- **Cron 集成**：通过 OpenClaw cron 定期执行批量处理

## CLI 命令

```bash
# 初始化 vault 目录结构
python scripts/cli.py init --vault "D:/MyVault"

# 一键编译（转换 + 编译 + 归档）
python scripts/cli.py compile --vault "D:/MyVault"

# 仅转换（PDF→MD，不编译）
python scripts/cli.py convert --vault "D:/MyVault"

# 预览（不执行）
python scripts/cli.py compile --vault "D:/MyVault" --dry-run

# 恢复中断任务
python scripts/cli.py resume --vault "D:/MyVault"

# 查看状态
python scripts/cli.py status --vault "D:/MyVault"

# 监听模式（持续自动处理）
python scripts/cli.py watch --vault "D:/MyVault"

# 批量迁移已处理文件
python scripts/cli.py migrate --vault "D:/MyVault"

# 清理过期归档
python scripts/cli.py cleanup --vault "D:/MyVault"
```

## 队列命令

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

# 跳过永久失败任务
python scripts/cli.py queue --skip-failed

# 重置卡住任务
python scripts/cli.py queue --reset-stuck

# 清空队列
python scripts/cli.py queue clear
```

## 配置

编辑 `config.yaml`（或 `local/config.yaml` 存放用户配置）：

```yaml
# MinerU Token（支持多 Token 轮询）
tokens:
  - token: "YOUR_MINERU_TOKEN_1"
    expires: "2026-12-31T23:59:59+08:00"
  - token: "YOUR_MINERU_TOKEN_2"
    expires: "2026-12-31T23:59:59+08:00"

# 转换引擎
engine:
  provider: mineru    # mineru / marker / docling
  mineru:
    base_url: "https://mineru.net"
    verify_ssl: false
    timeout: 300
    poll_interval: 3
    max_poll_interval: 30

# 并行
parallel:
  enabled: true
  max_workers: 4

# Vault 路径
vault:
  root: "D:/MyObsidianVault"
  raw_dir: "raw/todo"
  wiki_dir: "wiki"

# 目录（相对于 vault.root）
dirs:
  source: "raw/todo"
  output: "raw/todo"
  archive: "raw/09-archive"

# 编译
compile:
  mode: light         # light=规则引擎 / deep=AI摘要（待实现）

# 监听
watcher:
  poll_interval: 10
  stability_checks: 3
```

## 工作流

```
raw/todo/xxx.doc|ppt          ← 旧格式（需 COM/LibreOffice）
    ↓ legacy_converter         # DOC→DOCX / PPT→PPTX 自动转换
raw/todo/xxx.pdf|docx|pptx|xlsx|png|jpg...
    ↓ engine.convert_file()    # 转换（MinerU 多 Token 轮询）
raw/todo/xxx.md
    ↓ compiler.compile_md()    # 编译（Front Matter、摘要、去重）
wiki/sources/xxx.md            # wiki 页面
    ↓ index.update()           # 索引更新
wiki/index.md                  # 自动追加条目
    ↓ migrator                 # 归档
raw/09-archive/xxx.pdf|docx... # 原始文件归档
```

## 旧格式兼容（DOC/PPT）

MinerU 仅支持 DOCX/PPTX，旧格式 `.doc`/`.ppt` 需要先转换。

**后端探测机制**（按优先级自动选择）：

1. **MS Office COM**（最高优先级）— Windows + 已装 Office
2. **WPS COM** — Windows + 已装 WPS Office
3. **LibreOffice headless**（兜底）— 跨平台

查看探测结果：

```bash
python scripts/legacy_converter.py --check
```

## 持久化队列

JSON 存储，进程重启后自动恢复。

特性：
- **分类路由**：PDF→MinerU、DOC/PPT→旧格式转换→MinerU、XLSX→openpyxl、图片→OCR
- **分批处理**：可配置批次大小，每批结束自动保存
- **心跳守护**：定期扫描新文件，不遗忘、不遗漏
- **失败重试**：最多重试 3 次，可手动重置或跳过
- **统计报告**：按状态和分类分组统计

## 文件结构

```
obsidian-ingest/
├── config.yaml.example         # 配置模板（复制到 local/config.yaml）
├── local/
│   └── config.yaml             # 用户配置（gitignore）
├── requirements.txt
├── SKILL.md                    # 本文件
├── README.md                   # 英文文档
├── README.zh.md                # 中文文档
├── INSTALL.md                  # 安装指南
├── CHANGELOG.md                # 英文更新日志
├── CHANGELOG.zh.md             # 中文更新日志
├── ROADMAP.md                  # 版本路线图
├── LICENSE                     # MIT
├── sync-to-github.ps1          # 开发→GitHub 同步脚本
├── .gitignore
└── scripts/
    ├── cli.py                  # CLI 入口
    ├── mineru_client.py        # MinerU 客户端（多 Token、并行、进度条）
    ├── persistent_queue.py     # 持久化队列（心跳、重试、统计）
    ├── file_queue.py           # 文件分类路由
    ├── checkpoint.py           # 断点续传
    ├── compiler.py             # 编译引擎
    ├── engine.py               # 多引擎抽象
    ├── config_loader.py        # 配置加载（local/ 优先）
    ├── xlsx_converter.py       # XLSX→MD 转换器
    ├── legacy_converter.py     # 旧格式转换（COM/LibreOffice）
    └── migrator.py             # 文件迁移
```
