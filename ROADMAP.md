# Roadmap

## Current Status (v2.0.0)

**Stable & Functional** — obsidian-ingest is fully operational, processing 1200+ files with a persistent queue, multi-token rotation, and automated cron scheduling.

### What's Working
- ✅ MinerU cloud API integration with 4-token rotation
- ✅ Persistent task queue with auto-retry and dead-letter marking
- ✅ PDF / DOCX / PPTX / XLSX / Image conversion
- ✅ Legacy format conversion (.doc/.ppt → .docx/.pptx via COM)
- ✅ Checkpoint/resume system
- ✅ CLI with 8+ commands
- ✅ Cron-based periodic scanning
- ✅ Triple deduplication (fingerprint → content hash → semantic)

---

## v2.1.0 — Polish & Robustness

**Target: May 2026**

- [ ] **urllib3 InsecureRequestWarning suppression** — Clean up console spam from SSL-disabled MinerU requests
- [ ] **WPS COM backend testing** — Legacy converter code written but untested with WPS Office
- [ ] **Scanned PDF handling** — Flag scanned PDFs (where MinerU returns empty text) for manual review or OCR retry
- [ ] **Corrupt xlsx recovery** — Better error reporting for garbled/corrupt Excel files
- [ ] **Image OCR fallback** — Retry failed image OCR with different parameters or engine
- [ ] **Queue dashboard** — Web-based queue monitoring UI (simple Flask/FastAPI)
- [ ] **Better progress reporting** — ETA accuracy improvement, per-file conversion time tracking

---

## v2.2.0 — Multi-Engine Expansion

**Target: June 2026**

- [ ] **Marker engine integration** — Local GPU-based PDF conversion (requires CUDA setup)
- [ ] **Docling engine integration** — IBM's document understanding pipeline
- [ ] **Engine auto-selection** — Route files to the best engine based on type/size/complexity
- [ ] **Hybrid mode** — Cloud + local engines working together
- [ ] **Custom model support** — Fine-tuned models for domain-specific documents

---

## v2.3.0 — Intelligence Layer

**Target: July 2026**

- [ ] **LLM-powered compilation** — Use LLM for better summary generation, entity extraction, and wiki-link inference
- [ ] **Smart tagging** — Auto-tag documents based on content analysis
- [ ] **Relationship mapping** — Build document relationship graphs (references, citations, shared topics)
- [ ] **Semantic search** — Vector-based search across all converted documents
- [ ] **Auto-categorization** — Intelligent folder placement based on content

---

## v3.0.0 — Platform & Ecosystem

**Target: Q4 2026**

- [ ] **Plugin system** — Custom conversion plugins for specialized formats
- [ ] **Multi-vault support** — Process multiple Obsidian vaults independently
- [ ] **Team collaboration** — Shared queue, role-based access, webhook notifications
- [ ] **Cloud deployment** — Docker + cloud storage integration (S3, OneDrive, Google Drive)
- [ ] **Mobile companion** — iOS/Android app for triggering conversions on-the-go
- [ ] **API server** — REST API for external integrations

---

## Long-term Vision

- **Universal document ingestion** — Any format, any source, any destination
- **Knowledge graph** — Automatic relationship discovery across all documents
- **AI-first architecture** — LLM-native pipeline with human-in-the-loop refinement
- **Community ecosystem** — Shared templates, plugins, and conversion rules

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved. Priority areas are marked with 🔥 in the issues.
