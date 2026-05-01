# 更新日志

本项目的所有重要变更都会记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> 🇬🇧 [English](CHANGELOG.md)

> 🇬🇧 [English](CHANGELOG.md)

## [2.0.0] - 2026-04-29

### 新增
- **持久化任务队列** — JSON 存储，状态跟踪，自动重试（最多 3 次），死信标记，心跳检测
- **文件分类系统** — 自动分类为 pdf/office/spreadsheet/image 并路由到对应处理器
- **Excel 转换器** — .xlsx → Markdown 表格（基于 openpyxl）
- **旧格式转换器** — .doc→.docx、.ppt→.pptx，支持 MS Office COM / WPS COM / LibreOffice
- **队列 CLI 命令** — `queue`、`queue --skip-failed`、`queue --reset-stuck`
- **守护模式** — 周期性扫描，可配置间隔
- **多 Token 轮询** — 4 个 MinerU JWT Token，限流自动切换
- **Token 冷却机制** — 限流后 300 秒冷却，自动恢复
- **配置分离** — `local/config.yaml`（gitignore）存放用户配置，`config.yaml.example` 作为模板
- **同步脚本** — `sync-to-github.ps1` 用于开发目录到 GitHub 的同步
- **Cron 集成** — 通过 OpenClaw cron 定期执行批量处理

### 变更
- **架构** — 将 `any-pdf-2-md` 合并到 `obsidian-ingest`（单一技能）
- **引擎抽象** — MinerU 云 API 为主引擎，Marker/Docling 为预留接口
- **CLI 重构** — 基于队列的 `convert` 命令替代直接文件扫描
- **配置加载器** — 优先从 `local/` 目录加载 config.yaml

### 修复
- `mark_skipped()`、`mark_done()`、`mark_failed()` 现在自动持久化（之前仅修改内存）
- MinerU 配置映射 — token_slot 初始化修正
- urllib3 InsecureRequestWarning（SSL 验证禁用导致的警告）

### 移除
- `watcher.py` — 已废弃，由守护模式替代
- `__init__.py` — 脚本模式 CLI 不需要
- Docker 文件 — 使用云 API，不需要本地部署

## [1.0.0] - 2026-04-27

### 新增
- 首次发布
- MinerU 云 API 集成，支持批量处理
- 断点续传系统
- 三层去重（文件指纹 → 内容 hash → 语义匹配）
- 自动编译（Front Matter、wiki 链接、格式清洗）
- CLI 命令：convert / compile / status
- 4 Worker 并行处理
- 进度条 + ETA 显示
- SCHEMA.md wiki 页面结构规范

### 修复
- MinerU API 中 batch_id 与 task_id 混淆问题
- SSL 错误重试逻辑
- ZIP 解压路径错误
- 图片文件夹提取（恢复 4060+ 张图片）
- 空模板 wiki 页面 — 现在要求实际内容提取
- PowerShell 中文文件名编码问题
