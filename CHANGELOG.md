# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.0.0] - 2026-04-29

### Added
- **Persistent task queue** — JSON-backed queue with state tracking, auto-retry (max 3), dead-letter marking, heartbeat detection
- **File classification system** — Auto-categorize files into pdf/office/spreadsheet/image and route to appropriate handlers
- **Excel converter** — .xlsx → Markdown tables via openpyxl
- **Legacy format converter** — .doc→.docx and .ppt→.pptx via MS Office COM / WPS COM / LibreOffice
- **Queue CLI commands** — `queue`, `queue --skip-failed`, `queue --reset-stuck`
- **Daemon mode** — Periodic scan with configurable interval
- **Multi-token rotation** — 4 MinerU JWT tokens with automatic failover on rate-limit
- **Token cooldown** — 300s cooldown after rate-limit, auto-reactivation
- **Config separation** — `local/config.yaml` (gitignored) for user-specific settings, `config.yaml.example` as template
- **Sync script** — `sync-to-github.ps1` for dev-to-GitHub workflow
- **Cron integration** — Periodic batch processing via OpenClaw cron

### Changed
- **Architecture** — Merged `any-pdf-2-md` skill into `obsidian-ingest` (single skill)
- **Engine abstraction** — MinerU cloud API as primary engine, Marker/Docling as stubs
- **CLI rewritten** — Queue-based `convert` command replaces direct file scanning
- **Config loader** — Now prefers `local/` path over root for config.yaml

### Fixed
- `mark_skipped()`, `mark_done()`, `mark_failed()` now auto-save to disk (was in-memory only)
- MinerU config mapping — token_slot initialization corrected
- urllib3 InsecureRequestWarning (cosmetic, SSL verification disabled for MinerU API)

### Removed
- `watcher.py` — Dead code, replaced by daemon mode
- `__init__.py` — Unnecessary for script-based CLI
- Docker files (Dockerfile, docker-compose.yml) — Using cloud API, not local deployment

## [1.0.0] - 2026-04-27

### Added
- Initial release
- MinerU cloud API integration with batch processing
- Checkpoint/resume system
- Three-layer deduplication (fingerprint → content hash → semantic)
- Auto-compilation (Front Matter, wiki-links, format cleanup)
- CLI with convert/compile/status commands
- Parallel processing with 4 workers
- Progress bar and ETA display
- SCHEMA.md for wiki page structure

### Fixed
- Batch ID vs task ID confusion in MinerU API
- SSL errors with retry logic
- ZIP extraction path errors
- Image folder extraction (4060+ images recovered)
- Empty template wiki pages — now requires actual content extraction
- Chinese filename encoding issues in PowerShell
