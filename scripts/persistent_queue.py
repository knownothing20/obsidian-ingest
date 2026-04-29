"""
persistent_queue.py - 持久化文件处理队列

特性：
- 状态持久化到 JSON，进程重启后自动恢复
- 分类路由（PDF/Office/Spreadsheet/Image/Legacy）
- 分批处理，每批结束自动保存
- 心跳守护：定期扫描新文件，不遗忘、不遗漏
- 失败自动重试（可配置次数）
- 不自动终止：处理完一批后等待下一次心跳触发
"""

import os
import sys
import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Callable
from datetime import datetime


# ── 文件分类 ──────────────────────────────────────────────────────

class Category(str, Enum):
    PDF = "pdf"
    OFFICE_MODERN = "office"
    OFFICE_LEGACY = "legacy"
    SPREADSHEET = "spreadsheet"
    IMAGE = "image"
    UNKNOWN = "unknown"


_EXT_MAP = {
    ".pdf": Category.PDF,
    ".docx": Category.OFFICE_MODERN, ".pptx": Category.OFFICE_MODERN,
    ".doc": Category.OFFICE_LEGACY, ".ppt": Category.OFFICE_LEGACY,
    ".xlsx": Category.SPREADSHEET,
    ".png": Category.IMAGE, ".jpg": Category.IMAGE, ".jpeg": Category.IMAGE,
    ".bmp": Category.IMAGE, ".tiff": Category.IMAGE, ".tif": Category.IMAGE,
    ".webp": Category.IMAGE, ".gif": Category.IMAGE, ".jp2": Category.IMAGE,
}

def classify(path: str) -> Category:
    return _EXT_MAP.get(os.path.splitext(path)[1].lower(), Category.UNKNOWN)


# ── 任务状态 ──────────────────────────────────────────────────────

class Status(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Task:
    task_id: str          # 文件路径的 hash
    path: str             # 绝对路径
    rel_path: str         # 相对路径
    category: str         # Category.value
    status: str = "pending"
    converter: str = ""
    error: str = ""
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = ""
    updated_at: str = ""
    result: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Task(**d)


# ── 持久化队列 ────────────────────────────────────────────────────

class PersistentQueue:
    """持久化文件处理队列"""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.tasks: dict[str, Task] = {}
        self._load()

    # ── 持久化 ──

    def _load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for d in data.get("tasks", []):
                    t = Task.from_dict(d)
                    self.tasks[t.task_id] = t
            except (json.JSONDecodeError, KeyError):
                self.tasks = {}

    def save(self):
        os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
        data = {
            "version": 2,
            "updated_at": datetime.now().isoformat(),
            "tasks": [t.to_dict() for t in self.tasks.values()],
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_file)

    # ── 扫描 & 注册 ──

    def scan_and_register(self, source_dir: str, recursive: bool = True) -> int:
        """扫描目录，注册新文件到队列，返回新增数量"""
        exts = set(_EXT_MAP.keys())
        new_count = 0

        files_to_scan = []
        if recursive:
            for root, dirs, files in os.walk(source_dir):
                dirs[:] = [d for d in dirs if d.lower() != "images"]
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in exts:
                        files_to_scan.append(os.path.join(root, fname))
        else:
            for fname in os.listdir(source_dir):
                ext = os.path.splitext(fname)[1].lower()
                if ext in exts:
                    fpath = os.path.join(source_dir, fname)
                    if os.path.isfile(fpath):
                        files_to_scan.append(fpath)

        for fpath in files_to_scan:
            task_id = hashlib.md5(fpath.encode()).hexdigest()[:12]

            # 跳过已完成的
            if task_id in self.tasks:
                existing = self.tasks[task_id]
                if existing.status in (Status.DONE, Status.SKIPPED):
                    continue
                # 失败的可以重试
                if existing.status == Status.FAILED:
                    continue

            # 跳过已有 MD 的
            base, _ = os.path.splitext(fpath)
            if os.path.exists(base + ".md"):
                # 标记为跳过
                if task_id not in self.tasks:
                    self.tasks[task_id] = Task(
                        task_id=task_id,
                        path=fpath,
                        rel_path=os.path.relpath(fpath, source_dir),
                        category=classify(fpath).value,
                        status=Status.SKIPPED,
                        created_at=datetime.now().isoformat(),
                        updated_at=datetime.now().isoformat(),
                    )
                continue

            # 注册新任务
            if task_id not in self.tasks:
                cat = classify(fpath)
                self.tasks[task_id] = Task(
                    task_id=task_id,
                    path=fpath,
                    rel_path=os.path.relpath(fpath, source_dir),
                    category=cat.value,
                    status=Status.PENDING,
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                )
                new_count += 1

        if new_count > 0:
            self.save()
        return new_count

    # ── 查询 ──

    def get_pending(self, category: Optional[str] = None) -> list[Task]:
        """获取待处理任务（排除已耗尽重试的失败任务）"""
        tasks = []
        for t in self.tasks.values():
            if t.status == Status.PENDING:
                tasks.append(t)
            elif t.status == Status.FAILED and t.retry_count < t.max_retries:
                tasks.append(t)  # 可重试的失败任务也算待处理
        if category:
            tasks = [t for t in tasks if t.category == category]
        return tasks

    def get_retryable(self) -> list[Task]:
        """获取可重试的失败任务"""
        return [t for t in self.tasks.values()
                if t.status == Status.FAILED and t.retry_count < t.max_retries]

    def get_by_status(self, status: str) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == status]

    # ── 任务操作 ──

    def mark_processing(self, task_id: str):
        t = self.tasks.get(task_id)
        if t:
            t.status = Status.PROCESSING
            t.updated_at = datetime.now().isoformat()

    def mark_done(self, task_id: str, result: dict = None):
        t = self.tasks.get(task_id)
        if t:
            t.status = Status.DONE
            t.result = result or {}
            t.updated_at = datetime.now().isoformat()
            self.save()

    def mark_failed(self, task_id: str, error: str):
        t = self.tasks.get(task_id)
        if t:
            t.status = Status.FAILED
            t.error = error
            t.retry_count += 1
            t.updated_at = datetime.now().isoformat()
            self.save()

    def mark_skipped(self, task_id: str):
        t = self.tasks.get(task_id)
        if t:
            t.status = Status.SKIPPED
            t.updated_at = datetime.now().isoformat()
            self.save()

    def reset_failed(self):
        """重置所有失败任务为待处理（手动重试）"""
        count = 0
        for t in self.tasks.values():
            if t.status == Status.FAILED:
                t.status = Status.PENDING
                t.error = ""
                t.updated_at = datetime.now().isoformat()
                count += 1
        if count:
            self.save()
        return count

    # ── 统计 ──

    def stats(self) -> dict:
        by_status = {}
        by_category = {}
        for t in self.tasks.values():
            st = t.status if isinstance(t.status, str) else t.status.value
            by_status[st] = by_status.get(st, 0) + 1
            cat = t.category
            if cat not in by_category:
                by_category[cat] = {"total": 0, "done": 0, "failed": 0, "pending": 0, "skipped": 0}
            by_category[cat]["total"] += 1
            if st in by_category[cat]:
                by_category[cat][st] += 1
        return {
            "total": len(self.tasks),
            "by_status": by_status,
            "by_category": by_category,
        }

    def format_stats(self) -> str:
        s = self.stats()
        lines = [f"\n📊 队列状态 ({s['total']} 个任务)"]
        lines.append("─" * 50)

        # 按状态
        status_parts = []
        for st_val, st_name in [("done", "✅ 已完成"), ("pending", "⏳ 待处理"),
                                 ("processing", "🔄 处理中"), ("failed", "❌ 失败"), ("skipped", "⏭ 跳过")]:
            count = s["by_status"].get(st_val, 0)
            if count:
                status_parts.append(f"{st_name}: {count}")
        lines.append("  " + "  ".join(status_parts))

        # 按分类
        lines.append("")
        for cat, info in sorted(s["by_category"].items()):
            lines.append(f"  {cat:15s}  总计 {info['total']:3d}  "
                         f"✅ {info['done']:3d}  ⏳ {info['pending']:3d}  ❌ {info['failed']:3d}")

        lines.append("─" * 50)
        return "\n".join(lines)


# ── 批处理器 ──────────────────────────────────────────────────────

class BatchProcessor:
    """分批处理引擎"""

    def __init__(self, queue: PersistentQueue, registry: dict):
        self.queue = queue
        self.registry = registry  # {category: (name, handler)}

    def _get_handler(self, category: str):
        entry = self.registry.get(category)
        if entry:
            return entry[1]  # handler function
        return None

    def _get_name(self, category: str):
        entry = self.registry.get(category)
        if entry:
            return entry[0]  # converter name
        return "unknown"

    def process_batch(self, batch_size: int = 50,
                      progress_callback: Optional[Callable] = None) -> dict:
        """处理一批待处理任务，返回本批统计"""
        pending = self.queue.get_pending()
        if not pending:
            return {"processed": 0, "done": 0, "failed": 0, "skipped": 0}

        batch = pending[:batch_size]
        stats = {"processed": 0, "done": 0, "failed": 0, "skipped": 0}

        for i, task in enumerate(batch):
            handler = self._get_handler(task.category)
            if not handler:
                self.queue.mark_failed(task.task_id, f"无处理器: {task.category}")
                stats["failed"] += 1
                continue

            converter_name = self._get_name(task.category)
            self.queue.mark_processing(task.task_id)

            try:
                result = handler(task.path)
                if result.get("skipped"):
                    self.queue.mark_skipped(task.task_id)
                    stats["skipped"] += 1
                elif result.get("success"):
                    self.queue.mark_done(task.task_id, result)
                    stats["done"] += 1
                else:
                    self.queue.mark_failed(task.task_id, result.get("error", "未知错误"))
                    stats["failed"] += 1
            except Exception as e:
                self.queue.mark_failed(task.task_id, str(e))
                stats["failed"] += 1

            stats["processed"] += 1

            if progress_callback:
                progress_callback(task, stats, i + 1, len(batch))

            # 每 10 个保存一次
            if (i + 1) % 10 == 0:
                self.queue.save()

        # 批次结束，保存
        self.queue.save()

        return stats

    def process_all(self, batch_size: int = 50,
                    progress_callback: Optional[Callable] = None) -> dict:
        """处理所有待处理任务（多批次）"""
        total_stats = {"processed": 0, "done": 0, "failed": 0, "skipped": 0}
        batch_num = 0

        while True:
            pending = self.queue.get_pending()
            if not pending:
                break

            batch_num += 1
            print(f"\n📦 批次 {batch_num} (剩余 {len(pending)} 个)")

            batch_stats = self.process_batch(batch_size, progress_callback)

            for k in total_stats:
                total_stats[k] += batch_stats[k]

            if batch_stats["processed"] == 0:
                break

        return total_stats


# ── 转换器注册（复用 file_queue 的逻辑）─────────────────────────

def build_registry(engine, output_dir: str, cfg: dict):
    """构建转换器注册表（字符串键，避免 enum 冲突）"""
    registry = {}

    # MinerU: PDF + Office Modern + Image
    def mineru_handler(fpath):
        return engine.convert_file(fpath, output_dir, skip_if_exists=True)

    registry["pdf"] = ("mineru", mineru_handler)
    registry["office"] = ("mineru", mineru_handler)
    registry["image"] = ("mineru-ocr", mineru_handler)

    # Legacy: DOC/PPT → DOCX/PPTX → MinerU
    def legacy_handler(fpath):
        from legacy_converter import convert_file as legacy_convert
        conv = legacy_convert(fpath)
        if not conv["success"]:
            return {"success": False, "error": f"旧格式转换失败: {conv['error']}"}
        return engine.convert_file(conv["output_path"], output_dir, skip_if_exists=True)

    registry["legacy"] = ("legacy+mineru", legacy_handler)

    # Spreadsheet: openpyxl 本地转换
    def xlsx_handler(fpath):
        from xlsx_converter import convert_xlsx_to_markdown
        return convert_xlsx_to_markdown(fpath, output_dir, skip_if_exists=True)

    registry["spreadsheet"] = ("openpyxl", xlsx_handler)

    return registry


# ── CLI 入口 ──────────────────────────────────────────────────────

def cmd_queue_status(state_file: str):
    """查看队列状态"""
    queue = PersistentQueue(state_file)
    print(queue.format_stats())

    # 显示失败详情
    failed = queue.get_by_status(Status.FAILED)
    if failed:
        print(f"\n❌ 失败详情 ({len(failed)} 个):")
        for t in failed[:20]:
            print(f"  [{t.category}] {t.rel_path}: {t.error[:60]}")
        if len(failed) > 20:
            print(f"  ... 还有 {len(failed) - 20} 个")


def cmd_queue_retry(state_file: str):
    """重置失败任务"""
    queue = PersistentQueue(state_file)
    count = queue.reset_failed()
    print(f"✅ 已重置 {count} 个失败任务为待处理")


def cmd_queue_clear(state_file: str):
    """清空队列"""
    if os.path.exists(state_file):
        os.remove(state_file)
        print("✅ 队列已清空")
    else:
        print("队列文件不存在")


if __name__ == "__main__":
    # 测试
    import sys
    if len(sys.argv) > 1:
        vault = sys.argv[1]
        state_file = os.path.join(vault, ".obsidian-ingest", "queue_state.json")
        queue = PersistentQueue(state_file)
        new = queue.scan_and_register(os.path.join(vault, "raw"))
        print(f"扫描完成，新增 {new} 个任务")
        print(queue.format_stats())
