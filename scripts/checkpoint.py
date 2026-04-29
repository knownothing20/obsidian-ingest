"""
checkpoint.py - 断点续传系统

每个 vault 一个 .checkpoint.json，记录每个文件的处理状态。
支持：断点恢复、进度显示、文件指纹去重。
"""

import os
import sys
import json
import time
import hashlib
import signal
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

_TZ_CN = timezone(timedelta(hours=8))
CHECKPOINT_FILE = ".checkpoint.json"

# 文件状态机
STATUS_PENDING = "pending"
STATUS_CONVERTING = "converting"
STATUS_COMPILED = "compiled"
STATUS_DONE = "done"
STATUS_ARCHIVED = "archived"
STATUS_DUPLICATE = "duplicate"
STATUS_FAILED = "failed"

VALID_STATUSES = {
    STATUS_PENDING, STATUS_CONVERTING, STATUS_COMPILED,
    STATUS_DONE, STATUS_ARCHIVED, STATUS_DUPLICATE, STATUS_FAILED,
}

SUPPORTED_EXTS = {
    ".pdf",                                          # PDF
    ".docx", ".pptx", ".xlsx",                      # Office (MinerU 直接支持)
    ".doc", ".ppt",                                  # 旧格式（LibreOffice 自动转换）
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff",        # 图片
    ".webp", ".gif", ".jp2",                          # 图片（扩展）
}


def _now_iso() -> str:
    return datetime.now(_TZ_CN).isoformat(timespec="seconds")


def file_fingerprint(filepath: str) -> str:
    """文件指纹 = 文件名:大小:修改时间"""
    stat = os.stat(filepath)
    name = os.path.basename(filepath)
    return f"{name}:{stat.st_size}:{int(stat.st_mtime)}"


def content_hash(text: str) -> str:
    """内容 hash（用于 MD 正文去重）"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class Checkpoint:
    def __init__(self, vault_root: str):
        self.vault_root = vault_root
        self.path = os.path.join(vault_root, CHECKPOINT_FILE)
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            "version": 1,
            "created": _now_iso(),
            "updated": _now_iso(),
            "files": {},
            "stats": {
                "total": 0, "done": 0, "converting": 0,
                "compiled": 0, "failed": 0, "pending": 0,
                "duplicate": 0, "archived": 0,
            },
        }

    def save(self):
        self.data["updated"] = _now_iso()
        self._update_stats()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _update_stats(self):
        stats = {"total": 0, "done": 0, "converting": 0, "compiled": 0,
                 "failed": 0, "pending": 0, "duplicate": 0, "archived": 0}
        for f in self.data["files"].values():
            s = f.get("status", STATUS_PENDING)
            stats[s] = stats.get(s, 0) + 1
            stats["total"] += 1
        self.data["stats"] = stats

    # ── 文件操作 ──────────────────────────────────────────────────

    def get_status(self, rel_path: str) -> Optional[str]:
        entry = self.data["files"].get(rel_path)
        return entry["status"] if entry else None

    def set_status(self, rel_path: str, status: str, **kwargs):
        if status not in VALID_STATUSES:
            raise ValueError(f"无效状态: {status}")
        if rel_path not in self.data["files"]:
            self.data["files"][rel_path] = {
                "status": status,
                "fingerprint": "",
                "started_at": _now_iso(),
            }
        entry = self.data["files"][rel_path]
        entry["status"] = status
        entry.update(kwargs)

    def mark_converting(self, rel_path: str, task_id: str = ""):
        self.set_status(rel_path, STATUS_CONVERTING, task_id=task_id)

    def mark_compiled(self, rel_path: str, md_path: str = "", wiki_pages: list = None):
        self.set_status(rel_path, STATUS_COMPILED,
                        md_path=md_path, wiki_pages=wiki_pages or [],
                        compiled_at=_now_iso())

    def mark_done(self, rel_path: str):
        self.set_status(rel_path, STATUS_DONE, finished_at=_now_iso())

    def mark_archived(self, rel_path: str):
        self.set_status(rel_path, STATUS_ARCHIVED, archived_at=_now_iso())

    def mark_failed(self, rel_path: str, error: str):
        entry = self.data["files"].get(rel_path, {})
        retries = entry.get("retries", 0) + 1
        self.set_status(rel_path, STATUS_FAILED,
                        error=error, retries=retries,
                        last_attempt=_now_iso())

    def mark_duplicate(self, rel_path: str, duplicate_of: str):
        self.set_status(rel_path, STATUS_DUPLICATE,
                        duplicate_of=duplicate_of,
                        finished_at=_now_iso())

    def mark_fingerprint(self, rel_path: str, fp: str):
        if rel_path in self.data["files"]:
            self.data["files"][rel_path]["fingerprint"] = fp

    # ── 查询 ──────────────────────────────────────────────────────

    def get_pending_files(self, source_dir: str, recursive: bool = True, compile_only: bool = False) -> list:
        """扫描 source_dir，返回待处理文件列表（相对路径）

        compile_only=True 时：只返回有对应 .md 的源文件（跳过图片等）
        """
        doc_exts = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt"}
        pending = []
        if recursive:
            for root, dirs, files in os.walk(source_dir):
                dirs[:] = [d for d in dirs if d.lower() != "images"]
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if compile_only:
                        if ext not in doc_exts:
                            continue
                        base = os.path.splitext(os.path.join(root, fname))[0]
                        if not os.path.exists(base + ".md"):
                            continue
                    else:
                        if ext not in SUPPORTED_EXTS:
                            continue
                    abs_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(abs_path, self.vault_root).replace("\\", "/")
                    status = self.get_status(rel_path)
                    if status in (None, STATUS_PENDING, STATUS_FAILED):
                        fp = file_fingerprint(abs_path)
                        entry = self.data["files"].get(rel_path)
                        if entry and entry.get("fingerprint") == fp and entry.get("status") == STATUS_DONE:
                            continue
                        pending.append(rel_path)
        else:
            for fname in os.listdir(source_dir):
                ext = os.path.splitext(fname)[1].lower()
                if compile_only:
                    if ext not in doc_exts:
                        continue
                    base = os.path.splitext(os.path.join(source_dir, fname))[0]
                    if not os.path.exists(base + ".md"):
                        continue
                else:
                    if ext not in SUPPORTED_EXTS:
                        continue
                abs_path = os.path.join(source_dir, fname)
                if not os.path.isfile(abs_path):
                    continue
                rel_path = os.path.relpath(abs_path, self.vault_root).replace("\\", "/")
                status = self.get_status(rel_path)
                if status in (None, STATUS_PENDING, STATUS_FAILED):
                    fp = file_fingerprint(abs_path)
                    entry = self.data["files"].get(rel_path)
                    if entry and entry.get("fingerprint") == fp and entry.get("status") == STATUS_DONE:
                        continue
                    pending.append(rel_path)
        return pending

    def get_interrupted(self) -> list:
        """获取上次中断时正在处理的文件"""
        return [rel for rel, entry in self.data["files"].items()
                if entry.get("status") in (STATUS_CONVERTING, STATUS_COMPILED)]

    def get_failed(self) -> list:
        """获取失败文件"""
        return [rel for rel, entry in self.data["files"].items()
                if entry.get("status") == STATUS_FAILED]

    def can_retry(self, rel_path: str, max_retries: int = 3) -> bool:
        entry = self.data["files"].get(rel_path)
        if not entry or entry.get("status") != STATUS_FAILED:
            return False
        return entry.get("retries", 0) < max_retries

    def get_task_id(self, rel_path: str) -> str:
        """获取 MinerU 任务 ID（用于断点恢复）"""
        entry = self.data["files"].get(rel_path, {})
        return entry.get("task_id", "")

    def get_stats(self) -> dict:
        self._update_stats()
        return self.data["stats"]

    def is_fingerprint_match(self, rel_path: str, fp: str) -> bool:
        entry = self.data["files"].get(rel_path)
        if not entry:
            return False
        return entry.get("fingerprint") == fp and entry.get("status") in (STATUS_DONE, STATUS_ARCHIVED)


# ── 进度显示 ──────────────────────────────────────────────────────

def format_progress(stats: dict, width: int = 30) -> str:
    total = stats.get("total", 0)
    done = stats.get("done", 0) + stats.get("archived", 0) + stats.get("duplicate", 0)
    if total == 0:
        return f"[{'░' * width}] 0/0 (0%)"
    pct = done / total
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {done}/{total} ({pct:.1%})"


def format_stats_line(stats: dict) -> str:
    parts = []
    if stats.get("converting"):
        parts.append(f"转换中:{stats['converting']}")
    if stats.get("compiled"):
        parts.append(f"编译中:{stats['compiled']}")
    if stats.get("failed"):
        parts.append(f"失败:{stats['failed']}")
    if stats.get("pending"):
        parts.append(f"待处理:{stats['pending']}")
    return " | ".join(parts) if parts else "全部完成"
