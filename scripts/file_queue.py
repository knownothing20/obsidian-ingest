"""
file_queue.py - 文件分类队列系统

扫描 → 分类 → 路由 → 处理 → 结果归档
支持并行、重试、优先级、统计
"""

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed


# ── 文件分类 ──────────────────────────────────────────────────────

class FileCategory(Enum):
    """文件大类"""
    PDF = "pdf"                    # PDF 文档
    OFFICE_MODERN = "office"       # DOCX / PPTX（MinerU 直接处理）
    OFFICE_LEGACY = "legacy"       # DOC / PPT（需先转 DOCX/PPTX）
    SPREADSHEET = "spreadsheet"    # XLSX（openpyxl 直接转 MD）
    IMAGE = "image"                # JPG/PNG 等（MinerU OCR）
    UNKNOWN = "unknown"


# 扩展名 → 分类映射
_EXT_MAP: dict[str, FileCategory] = {
    ".pdf": FileCategory.PDF,
    ".docx": FileCategory.OFFICE_MODERN,
    ".pptx": FileCategory.OFFICE_MODERN,
    ".doc": FileCategory.OFFICE_LEGACY,
    ".ppt": FileCategory.OFFICE_LEGACY,
    ".xlsx": FileCategory.SPREADSHEET,
    ".png": FileCategory.IMAGE,
    ".jpg": FileCategory.IMAGE,
    ".jpeg": FileCategory.IMAGE,
    ".bmp": FileCategory.IMAGE,
    ".tiff": FileCategory.IMAGE,
    ".tif": FileCategory.IMAGE,
    ".webp": FileCategory.IMAGE,
    ".gif": FileCategory.IMAGE,
    ".jp2": FileCategory.IMAGE,
}


@dataclass
class FileItem:
    """队列中的文件项"""
    path: str                       # 绝对路径
    rel_path: str                   # 相对于 vault 的路径
    ext: str                        # 扩展名（小写）
    category: FileCategory          # 分类
    status: str = "pending"         # pending / processing / done / failed / skipped
    converter: str = ""             # 使用的转换器名称
    error: str = ""                 # 失败原因
    result: dict = field(default_factory=dict)  # 转换结果
    retry_count: int = 0            # 已重试次数
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def elapsed(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        elif self.started_at:
            return time.time() - self.started_at
        return 0.0

    @property
    def basename(self) -> str:
        return os.path.basename(self.path)


def classify_file(file_path: str) -> FileCategory:
    """根据扩展名分类文件"""
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_MAP.get(ext, FileCategory.UNKNOWN)


def scan_directory(source_dir: str, recursive: bool = True,
                   skip_existing_md: bool = True) -> list[FileItem]:
    """
    扫描目录，返回待处理文件列表（已分类）

    Args:
        source_dir: 源目录
        recursive: 是否递归
        skip_existing_md: 跳过已有同名 MD 的文件
    """
    items = []
    exts = set(_EXT_MAP.keys())

    if recursive:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d.lower() != "images"]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in exts:
                    continue
                fpath = os.path.join(root, fname)
                if skip_existing_md:
                    base, _ = os.path.splitext(fpath)
                    if os.path.exists(base + ".md"):
                        continue
                category = classify_file(fpath)
                rel_path = os.path.relpath(fpath, source_dir)
                items.append(FileItem(
                    path=fpath,
                    rel_path=rel_path,
                    ext=ext,
                    category=category,
                ))
    else:
        for fname in os.listdir(source_dir):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in exts:
                continue
            fpath = os.path.join(source_dir, fname)
            if not os.path.isfile(fpath):
                continue
            if skip_existing_md:
                base, _ = os.path.splitext(fpath)
                if os.path.exists(base + ".md"):
                    continue
            category = classify_file(fpath)
            rel_path = os.path.relpath(fpath, source_dir)
            items.append(FileItem(
                path=fpath,
                rel_path=rel_path,
                ext=ext,
                category=category,
            ))

    return items


# ── 转换器注册 ────────────────────────────────────────────────────

class ConverterRegistry:
    """转换器注册表：按分类路由到对应的处理函数"""

    def __init__(self):
        self._converters: dict[FileCategory, Callable] = {}
        self._names: dict[FileCategory, str] = {}

    def register(self, category: FileCategory, handler: Callable, name: str = ""):
        """注册某个分类的处理函数"""
        self._converters[category] = handler
        self._names[category] = name or category.value

    def get_handler(self, category: FileCategory) -> Optional[Callable]:
        return self._converters.get(category)

    def get_name(self, category: FileCategory) -> str:
        return self._names.get(category, "unknown")

    def has_handler(self, category: FileCategory) -> bool:
        return category in self._converters


def build_default_registry(engine, output_dir: str, cfg: dict) -> ConverterRegistry:
    """
    构建默认转换器注册表

    每个 handler 签名: (file_item: FileItem) -> dict
    返回: {"success": bool, "markdown_path": str, "error": str, ...}
    """
    registry = ConverterRegistry()

    # PDF + Office Modern + Image → MinerU
    def mineru_handler(item: FileItem) -> dict:
        return engine.convert_file(item.path, output_dir, skip_if_exists=True)

    registry.register(FileCategory.PDF, mineru_handler, "mineru")
    registry.register(FileCategory.OFFICE_MODERN, mineru_handler, "mineru")
    registry.register(FileCategory.IMAGE, mineru_handler, "mineru-ocr")

    # Legacy Office → legacy_converter → 再走 MinerU
    def legacy_handler(item: FileItem) -> dict:
        from legacy_converter import convert_file as legacy_convert
        conv = legacy_convert(item.path)
        if not conv["success"]:
            return {"success": False, "error": f"旧格式转换失败: {conv['error']}"}
        # 转换后的文件交给 MinerU
        return engine.convert_file(conv["output_path"], output_dir, skip_if_exists=True)

    registry.register(FileCategory.OFFICE_LEGACY, legacy_handler, "legacy+mineru")

    # Spreadsheet → xlsx_converter
    def xlsx_handler(item: FileItem) -> dict:
        from xlsx_converter import convert_xlsx_to_markdown
        return convert_xlsx_to_markdown(item.path, output_dir, skip_if_exists=True)

    registry.register(FileCategory.SPREADSHEET, xlsx_handler, "openpyxl")

    return registry


# ── 队列管理器 ────────────────────────────────────────────────────

class FileQueue:
    """文件处理队列：分类、统计、处理、重试"""

    def __init__(self, items: list[FileItem], registry: ConverterRegistry):
        self.items = items
        self.registry = registry
        self._by_category: dict[FileCategory, list[FileItem]] = {}
        self._classify()

    def _classify(self):
        """按分类分组"""
        self._by_category = {}
        for item in self.items:
            cat = item.category
            if cat not in self._by_category:
                self._by_category[cat] = []
            self._by_category[cat].append(item)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def pending(self) -> list[FileItem]:
        return [i for i in self.items if i.status == "pending"]

    @property
    def done(self) -> list[FileItem]:
        return [i for i in self.items if i.status == "done"]

    @property
    def failed(self) -> list[FileItem]:
        return [i for i in self.items if i.status == "failed"]

    @property
    def skipped(self) -> list[FileItem]:
        return [i for i in self.items if i.status == "skipped"]

    def summary(self) -> str:
        """打印分类统计"""
        lines = [f"📊 文件分类统计 (共 {self.total} 个待处理)"]
        lines.append("─" * 40)
        for cat in FileCategory:
            items = self._by_category.get(cat, [])
            if not items:
                continue
            handler_name = self.registry.get_name(cat) if self.registry.has_handler(cat) else "❌ 无处理器"
            lines.append(f"  {cat.value:15s}  {len(items):4d} 个  →  {handler_name}")
        lines.append("─" * 40)
        return "\n".join(lines)

    def get_by_category(self, category: FileCategory) -> list[FileItem]:
        return self._by_category.get(category, [])

    def process(self, max_workers: int = 1, max_retries: int = 0,
                progress_callback: Optional[Callable] = None) -> dict:
        """
        处理队列中的所有文件

        Args:
            max_workers: 并行数（1 = 串行）
            max_retries: 失败重试次数
            progress_callback: 进度回调 fn(item, stats)
        """
        stats = {"done": 0, "failed": 0, "skipped": 0, "total": self.total}

        if max_workers <= 1:
            # 串行处理
            for i, item in enumerate(self.items):
                self._process_item(item, stats, i, progress_callback)
        else:
            # 并行处理
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for i, item in enumerate(self.items):
                    future = executor.submit(self._process_item_sync, item, i)
                    futures[future] = item

                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        result = future.result()
                        item.result = result
                        item.finished_at = time.time()
                        if result.get("skipped"):
                            item.status = "skipped"
                            stats["skipped"] += 1
                        elif result.get("success"):
                            item.status = "done"
                            stats["done"] += 1
                        else:
                            item.status = "failed"
                            item.error = result.get("error", "未知错误")
                            stats["failed"] += 1
                    except Exception as e:
                        item.status = "failed"
                        item.error = str(e)
                        item.finished_at = time.time()
                        stats["failed"] += 1

                    if progress_callback:
                        progress_callback(item, stats)

        # 重试失败项
        if max_retries > 0:
            failed_items = [i for i in self.items if i.status == "failed" and i.retry_count < max_retries]
            for item in failed_items:
                item.retry_count += 1
                item.status = "pending"
                self._process_item(item, stats, -1, progress_callback)

        return stats

    def _process_item(self, item: FileItem, stats: dict, index: int,
                      progress_callback: Optional[Callable]):
        """处理单个文件"""
        handler = self.registry.get_handler(item.category)
        if not handler:
            item.status = "failed"
            item.error = f"没有处理器: {item.category.value}"
            stats["failed"] += 1
            return

        item.status = "processing"
        item.converter = self.registry.get_name(item.category)
        item.started_at = time.time()

        try:
            result = handler(item)
            item.result = result
            item.finished_at = time.time()

            if result.get("skipped"):
                item.status = "skipped"
                stats["skipped"] += 1
            elif result.get("success"):
                item.status = "done"
                stats["done"] += 1
            else:
                item.status = "failed"
                item.error = result.get("error", "未知错误")
                stats["failed"] += 1
        except Exception as e:
            item.status = "failed"
            item.error = str(e)
            item.finished_at = time.time()
            stats["failed"] += 1

        if progress_callback:
            progress_callback(item, stats)

    def _process_item_sync(self, item: FileItem, index: int) -> dict:
        """同步处理单个文件（用于并行模式）"""
        handler = self.registry.get_handler(item.category)
        if not handler:
            return {"success": False, "error": f"没有处理器: {item.category.value}"}

        item.status = "processing"
        item.converter = self.registry.get_name(item.category)
        item.started_at = time.time()

        try:
            result = handler(item)
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def export_results(self) -> dict:
        """导出处理结果统计"""
        by_category = {}
        for item in self.items:
            cat = item.category.value
            if cat not in by_category:
                by_category[cat] = {"done": 0, "failed": 0, "skipped": 0, "errors": []}
            by_category[cat][item.status] = by_category[cat].get(item.status, 0) + 1
            if item.error:
                by_category[cat]["errors"].append(f"{item.basename}: {item.error}")

        return {
            "total": self.total,
            "done": len(self.done),
            "failed": len(self.failed),
            "skipped": len(self.skipped),
            "by_category": by_category,
            "failed_files": [
                {"file": i.basename, "category": i.category.value, "error": i.error}
                for i in self.failed
            ],
        }
