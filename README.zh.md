# obsidian-ingest

**Obsidian 知识库自动摄入引擎** — PDF/DOCX/PPTX/XLSX/图片 → Markdown → Obsidian wiki 页面，全自动流水线。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🇬🇧 [English](README.md)

## ✨ 特性

- 🔄 **MinerU 多 Token 轮询** — 多 Token 自动故障转移，限流自动切换
- ⚡ **并行转换** — 多 Worker 并行处理，实时进度条 + ETA
- 🔧 **多格式支持** — PDF / DOCX / PPTX / XLSX / 图片，统一管线处理
- 💾 **断点续传** — 中断后自动恢复，不重复处理
- 📋 **持久队列** — JSON-backed 任务队列，失败自动重试，死信标记，心跳检测
- 🔧 **Legacy 转换** — .doc→.docx、.ppt→.pptx（MS Office COM / WPS COM）
- 📊 **Excel 转换** — .xlsx → Markdown 表格（openpyxl）
- 🔍 **三层去重** — 文件指纹 → 内容 hash → 语义匹配
- 📝 **自动编译** — 格式清洗、Front Matter 注入、双链关联
- 🏗️ **多引擎架构** — MinerU（云端）/ Marker（本地 GPU）/ Docling（IBM）
- 📡 **监听模式** — 自动检测新文件并处理

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/knownothing20/obsidian-ingest.git
cd obsidian-ingest
pip install -r requirements.txt
```

### 配置

```bash
# 从模板创建本地配置（local/ 目录不会被提交）
cp config.yaml.example local/config.yaml
```

编辑 `local/config.yaml`：

```yaml
vault:
  root: "D:/MyObsidianVault"

tokens:
  - token: "YOUR_MINERU_TOKEN"
    expires: "2026-12-31T23:59:59+08:00"

parallel:
  max_workers: 4
```

### 使用

```bash
# 查看队列状态
python scripts/cli.py queue

# 扫描并转换待处理文件
python scripts/cli.py convert

# 编译已转换的 MD 文件为 wiki 页面
python scripts/cli.py compile

# 查看整体处理状态
python scripts/cli.py status

# 守护模式（定期扫描）
python scripts/cli.py daemon --interval 300

# 标记永久失败的任务为跳过
python scripts/cli.py queue --skip-failed

# 重置卡住的任务
python scripts/cli.py queue --reset-stuck
```

## 📖 CLI 命令

| 命令 | 说明 |
|------|------|
| `queue` | 查看文件处理队列状态 |
| `convert` | 扫描并转换待处理文件 |
| `compile` | 编译已转换的 MD 文件为 wiki 页面 |
| `status` | 查看整体处理状态 |
| `daemon` | 守护模式运行（定期扫描） |
| `queue --skip-failed` | 标记永久失败的任务为跳过 |
| `queue --reset-stuck` | 重置卡住的处理任务 |

## 📁 项目结构

```
obsidian-ingest/
├── config.yaml.example      # 配置模板
├── local/                   # 用户配置（gitignore）
│   ├── config.yaml          # 实际配置
│   └── README.md
├── requirements.txt
├── scripts/
│   ├── cli.py               # CLI 入口
│   ├── engine.py            # MinerU 客户端（多 Token、并行）
│   ├── persistent_queue.py  # 持久化任务队列
│   ├── file_queue.py        # 文件分类与路由
│   ├── xlsx_converter.py    # Excel → Markdown
│   ├── legacy_converter.py  # .doc/.ppt → .docx/.pptx（COM）
│   ├── compiler.py          # 编译引擎
│   ├── config_loader.py     # 配置加载（local/ 优先）
│   ├── checkpoint.py        # 断点续传
│   └── migrator.py          # 文件迁移
├── SKILL.md                 # AI Agent 使用手册
├── INSTALL.md               # 安装指南
├── CHANGELOG.md             # 版本历史
└── LICENSE
```

## 🔧 转换引擎

当前支持 MinerU 云端 API，可扩展 Marker（本地 GPU）和 Docling（IBM）：

```yaml
engine:
  provider: mineru    # 云端 API（默认）
  # provider: marker  # 本地 GPU（需自行安装）
  # provider: docling # IBM（需自行安装）
```

## ⚡ MinerU Token 管理

支持多 Token 轮换，提高并发处理能力：

```yaml
tokens:
  - token: "token_1"
    expires: "2026-07-25T00:00:00+08:00"
  - token: "token_2"
    expires: "2026-07-25T00:00:00+08:00"
```

- 限流错误（-60009/-60018）自动切换下一个 Token
- Token 冷却 300 秒后自动恢复
- Token 过期前自动提醒更新

## 📋 持久队列

文件处理使用 JSON-backed 持久队列：

- 任务状态追踪：`pending → processing → done / failed / skipped`
- 失败自动重试（最多 3 次）
- 死信队列（永久失败的任务标记为 `skipped`）
- 心跳检测（处理超时自动重置）

## 🤝 贡献

欢迎 Issue 和 PR！请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 📄 许可证

[MIT](LICENSE)
