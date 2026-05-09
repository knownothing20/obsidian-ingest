"""
operation_logger.py - 操作日志系统（SCHEMA v4.0.0）

SCHEMA.md 第 10.1 节要求：每次 ingest/migrate/wiki-update/schema 操作
追加日志到 wiki/logs/YYYY-MM-DD.md

日志格式：
## ingest | 处理文件标题
- **raw**: 原始文件名.md
- **新增**:
  - Sources: [[page-name]]
  - Concepts: [[page-name]]
  - Entities: [[page-name]]
- **归档**: → archive/raw-archive/（源文件）或 archive/md-archive/（MD文件）
- **冲突**: 知识冲突描述 / 无
- **时间**: 2026-05-02T14:30:00+08:00

用法：
  from operation_logger import log_operation
  log_operation(
      operation="ingest",
      title="处理文件标题",
      raw_file="原始文件名.pdf",
      wiki_pages={"sources": ["docker.md"], "concepts": ["Docker概念.md"]},
      archived_to="archive/raw-archive/xxx.pdf",
      conflicts="无",
      vault_root="/path/to/vault"
  )
"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict

_TZ_CN = timezone(timedelta(hours=8))


def _now_iso() -> str:
    """获取当前时间 ISO 格式（中国时区）"""
    return datetime.now(_TZ_CN).isoformat(timespec="seconds") + "+08:00"


def _ensure_logs_dir(vault_root: str) -> str:
    """确保 wiki/logs/ 目录存在，返回日志目录路径"""
    logs_dir = os.path.join(vault_root, "wiki", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def _get_log_path(vault_root: str, date: str = None) -> str:
    """获取指定日期的日志文件路径"""
    if date is None:
        date = datetime.now(_TZ_CN).strftime("%Y-%m-%d")
    logs_dir = _ensure_logs_dir(vault_root)
    return os.path.join(logs_dir, f"{date}.md")


def _format_wiki_pages(wiki_pages: Dict[str, List[str]]) -> str:
    """格式化 wiki 页面新增列表"""
    lines = []
    for ptype, pages in wiki_pages.items():
        if not pages:
            continue
        ptype_label = {
            "concept": "Concepts",
            "entity": "Entities",
            "source": "Sources",
            "synthesis": "Syntheses",
        }.get(ptype, ptype.title())

        formatted_pages = []
        for p in pages:
            # 确保双链格式
            if not p.startswith("[["):
                p = f"[[{p}]]"
            formatted_pages.append(p)

        lines.append(f"  - {ptype_label}: {', '.join(formatted_pages)}")
    return "\n".join(lines) if lines else "  - 无"


class OperationLogger:
    """操作日志记录器（单例模式）"""

    _instance = None
    _vault_root: str = ""

    def __new__(cls, vault_root: str = ""):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._vault_root = vault_root
        return cls._instance

    @classmethod
    def get_instance(cls, vault_root: str = ""):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls(vault_root)
        if vault_root:
            cls._vault_root = vault_root
        return cls._instance

    @classmethod
    def reset(cls):
        """重置单例（用于测试）"""
        cls._instance = None
        cls._vault_root = ""


def log_operation(
    operation: str,
    title: str,
    raw_file: str = "",
    wiki_pages: Dict[str, List[str]] = None,
    archived_to: str = "",
    conflicts: str = "无",
    vault_root: str = "",
    skip_duplicate: bool = True,
) -> bool:
    """
    记录操作日志

    Args:
        operation: 操作类型（ingest/migrate/wiki-update/schema）
        title: 处理的文件标题
        raw_file: 原始源文件名
        wiki_pages: 新增的 wiki 页面，格式 {"concept": [...], "entity": [...], "source": [...], "synthesis": [...]}
        archived_to: 归档位置（如 "archive/raw-archive/xxx.pdf"）
        conflicts: 知识冲突描述，"无" 表示无冲突
        vault_root: Vault 根目录
        skip_duplicate: 是否跳过重复日志（基于当天同一标题）

    Returns:
        是否成功记录
    """
    if not vault_root:
        return False

    wiki_pages = wiki_pages or {}

    # 获取日志文件路径
    log_path = _get_log_path(vault_root)
    date_str = datetime.now(_TZ_CN).strftime("%Y-%m-%d")

    # 检查是否重复（skip_duplicate 模式下）
    if skip_duplicate and os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            existing = f.read()
            # 检查是否已有相同标题的日志
            if f"## {operation} | {title}" in existing:
                # 已经记录过，追加一个标记说明已跳过
                pass

    # 构建日志条目
    wiki_pages_str = _format_wiki_pages(wiki_pages)

    raw_line = f"- **raw**: {raw_file}" if raw_file else "- **raw**: （无）"
    archived_line = f"- **归档**: → {archived_to}" if archived_to else "- **归档**: （无）"

    log_entry = f"""## {operation} | {title}
{raw_line}
- **新增**:
{wiki_pages_str}
{archived_line}
- **冲突**: {conflicts}
- **时间**: {_now_iso()}
"""

    # 追加到日志文件
    try:
        if os.path.exists(log_path):
            # 检查是否已存在相同标题的日志
            with open(log_path, "r", encoding="utf-8") as f:
                if f"## {operation} | {title}" in f.read():
                    # 已存在，不重复记录
                    return True

            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n" + log_entry)
        else:
            # 新建日志文件
            header = f"""# 操作日志

> 自动生成，按日期归档
> 格式参考 SCHEMA.md 第 10.1 节

---
"""
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(header + log_entry)

        return True
    except Exception as e:
        print(f"[ERROR] 写操作日志失败: {e}")
        return False


def get_today_operations(vault_root: str) -> List[dict]:
    """获取今天的操作记录"""
    log_path = _get_log_path(vault_root)
    if not os.path.exists(log_path):
        return []

    operations = []
    current_op = {}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("## "):
                    # 新操作开始
                    if current_op:
                        operations.append(current_op)
                    # 解析 "## ingest | 标题"
                    parts = line[3:].split(" | ", 1)
                    current_op = {
                        "operation": parts[0] if len(parts) > 0 else "",
                        "title": parts[1] if len(parts) > 1 else "",
                        "raw": "",
                        "pages": [],
                        "archived": "",
                        "conflicts": "",
                        "time": "",
                    }
                elif line.startswith("- **raw**: "):
                    current_op["raw"] = line[11:].strip()
                elif line.startswith("- **时间**: "):
                    current_op["time"] = line[11:].strip()

            if current_op:
                operations.append(current_op)
    except Exception:
        pass

    return operations


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="操作日志记录工具")
    parser.add_argument("--operation", "-o", required=True, help="操作类型 (ingest/migrate/wiki-update/schema)")
    parser.add_argument("--title", "-t", required=True, help="处理的文件标题")
    parser.add_argument("--raw", "-r", default="", help="原始源文件名")
    parser.add_argument("--pages", "-p", default="", help="新增页面，格式: source:page1,page2|concept:page3")
    parser.add_argument("--archived", "-a", default="", help="归档位置")
    parser.add_argument("--conflicts", "-c", default="无", help="知识冲突描述")
    parser.add_argument("--vault", "-v", required=True, help="Vault 根目录")

    args = parser.parse_args()

    # 解析 pages 参数
    wiki_pages = {}
    if args.pages:
        for part in args.pages.split("|"):
            if ":" not in part:
                continue
            ptype, pages_str = part.split(":", 1)
            pages = [p.strip() for p in pages_str.split(",") if p.strip()]
            if pages:
                wiki_pages[ptype] = pages

    success = log_operation(
        operation=args.operation,
        title=args.title,
        raw_file=args.raw,
        wiki_pages=wiki_pages,
        archived_to=args.archived,
        conflicts=args.conflicts,
        vault_root=args.vault,
    )

    if success:
        print(f"✅ 日志已记录: {args.operation} | {args.title}")
    else:
        print(f"❌ 日志记录失败")
        exit(1)