# obsidian-ingest

**Obsidian Knowledge Base Ingestion Engine** — PDF/DOCX/PPTX/XLSX/Images → Markdown → Obsidian wiki pages, fully automated pipeline.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🇨🇳 [中文文档](README.zh.md)

## ✨ Features

- 🔄 **Multi-Token MinerU Rotation** — Automatic failover across multiple API tokens, rate-limit aware
- ⚡ **Parallel Processing** — Multi-worker concurrent conversion with progress bar + ETA
- 🔧 **Multi-Format Support** — PDF / DOCX / PPTX / XLSX / Images, unified pipeline
- 💾 **Checkpoint & Resume** — Auto-recover from interruptions, no duplicate processing
- 📋 **Persistent Queue** — JSON-backed task queue with auto-retry, dead-letter marking, heartbeat detection
- 🔧 **Legacy Conversion** — .doc→.docx, .ppt→.pptx via MS Office COM / WPS COM
- 📊 **Excel Conversion** — .xlsx → Markdown tables (openpyxl)
- 🔍 **Triple Deduplication** — File fingerprint → Content hash → Semantic matching
- 📝 **Auto Compilation** — Format cleanup, Front Matter injection, wiki-link association
- 🏗️ **Multi-Engine Architecture** — MinerU (cloud, active) / Marker (local GPU) / Docling (IBM)
- 📡 **Watch Mode** — Auto-detect new files and process them

## 🚀 Quick Start

### Install

```bash
git clone https://github.com/knownothing20/obsidian-ingest.git
cd obsidian-ingest
pip install -r requirements.txt
```

### Configure

```bash
# Create local config from template (local/ directory is gitignored)
cp config.yaml.example local/config.yaml
```

Edit `local/config.yaml`:

```yaml
vault:
  root: "D:/MyObsidianVault"

tokens:
  - token: "YOUR_MINERU_TOKEN"
    expires: "2026-12-31T23:59:59+08:00"

parallel:
  max_workers: 4
```

### Usage

```bash
# Check queue status
python scripts/cli.py queue

# Scan and convert pending files
python scripts/cli.py convert

# Compile converted MD files into wiki pages
python scripts/cli.py compile

# View overall status
python scripts/cli.py status

# Run as daemon (periodic scan)
python scripts/cli.py daemon --interval 300

# Mark permanently failed tasks as skipped
python scripts/cli.py queue --skip-failed

# Reset stuck tasks
python scripts/cli.py queue --reset-stuck
```

## 📖 CLI Commands

| Command | Description |
|---------|-------------|
| `queue` | View file processing queue status |
| `convert` | Scan and convert pending files |
| `compile` | Compile converted MD files into wiki pages |
| `status` | View overall processing status |
| `daemon` | Run as periodic daemon |
| `queue --skip-failed` | Mark permanently failed tasks as skipped |
| `queue --reset-stuck` | Reset stuck processing tasks |

## 📁 Project Structure

```
obsidian-ingest/
├── config.yaml.example      # Config template
├── local/                   # User config (gitignored)
│   ├── config.yaml          # Actual config
│   └── README.md
├── requirements.txt
├── scripts/
│   ├── cli.py               # CLI entry point
│   ├── engine.py            # MinerU client (multi-token, parallel)
│   ├── persistent_queue.py  # Persistent task queue
│   ├── file_queue.py        # File classification & routing
│   ├── xlsx_converter.py    # Excel → Markdown
│   ├── legacy_converter.py  # .doc/.ppt → .docx/.pptx (COM)
│   ├── compiler.py          # Compilation engine
│   ├── config_loader.py     # Config loader (local/ priority)
│   ├── checkpoint.py        # Checkpoint & resume
│   └── migrator.py          # File migration
├── SKILL.md                 # AI Agent usage manual
├── INSTALL.md               # Installation guide
├── CHANGELOG.md             # Version history
└── LICENSE
```

## 🔧 Conversion Engines

Currently supports MinerU cloud API, extensible to Marker (local GPU) and Docling (IBM):

```yaml
engine:
  provider: mineru    # Cloud API (default)
  # provider: marker  # Local GPU (self-install)
  # provider: docling # IBM (self-install)
```

## ⚡ MinerU Token Management

Supports multi-token rotation for higher concurrency:

```yaml
tokens:
  - token: "token_1"
    expires: "2026-07-25T00:00:00+08:00"
  - token: "token_2"
    expires: "2026-07-25T00:00:00+08:00"
```

- Rate-limit errors (-60009/-60018) auto-switch to next token
- Tokens cool down for 300s before reuse
- Auto-alert before token expiration

## 📋 Persistent Queue

File processing uses a JSON-backed persistent queue:

- Task state tracking: `pending → processing → done / failed / skipped`
- Auto-retry on failure (max 3 attempts)
- Dead-letter queue (permanently failed tasks marked as `skipped`)
- Heartbeat detection (stuck tasks auto-reset)

## 🤝 Contributing

Issues and PRs welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

[MIT](LICENSE)
