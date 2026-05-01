"""
migrator.py - 文件迁移：处理完的 PDF 自动归档

工作模式：
  - source != output: MD 移到 output，PDF 移到 archive
  - source == output: MD 已在原位，只移 PDF 到 archive
"""

import os
import sys
import time
import shutil
from pathlib import Path
from typing import Optional


def _move(src: str, dst_dir: str) -> Optional[str]:
    if not os.path.exists(src):
        return None
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    shutil.move(src, dst)
    return dst


def has_md_sibling(pdf_path: str, output_dir: str = "") -> bool:
    """
    检查是否有同名的 MD 文件
    
    Bug#6 fix: 需要检查两个位置：
    1. 同目录（原始行为）
    2. output_dir/todo（因为 MD 转换后放在 todo/）
    
    Args:
        pdf_path: PDF/源文件路径
        output_dir: 输出目录（todo/），用于检查 MD 是否已转换
    """
    base, _ = os.path.splitext(pdf_path)
    
    # 1. 检查同目录
    if os.path.exists(base + ".md"):
        return True
    
    # 2. 检查 output_dir（todo/）
    if output_dir:
        fname = os.path.basename(base) + ".md"
        md_in_output = os.path.join(output_dir, fname)
        if os.path.exists(md_in_output):
            return True
    
    return False


def get_pending_files(source_dir: str, recursive: bool = True, output_dir: str = "") -> list:
    """获取待处理文件列表（排除已有同名 MD 的）"""
    # 支持格式：MinerU 直接处理 + DOC/PPT 需先转为 DOCX/PPTX
    exts = {
        ".pdf",
        ".docx", ".pptx", ".xlsx",
        ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif", ".jp2",
        ".doc", ".ppt",  # 旧格式，需 LibreOffice 先转换
    }
    pending = []
    if recursive:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d.lower() != "images"]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in exts:
                    fpath = os.path.join(root, fname)
                    if not has_md_sibling(fpath, output_dir):
                        pending.append((fpath, ext))
    else:
        for fname in os.listdir(source_dir):
            ext = os.path.splitext(fname)[1].lower()
            if ext in exts:
                fpath = os.path.join(source_dir, fname)
                if os.path.isfile(fpath) and not has_md_sibling(fpath, output_dir):
                    pending.append((fpath, ext))
    return pending


def migrate(source_dir: str = "", output_dir: str = "", archive_dir: str = "",
            failed_dir: str = "", retention_days: int = 7) -> dict:
    """
    批量迁移：扫描 source_dir，迁移已处理的文件
      - 如果 source != output: .md -> output_dir, .pdf/.docx/.pptx -> archive_dir
      - 如果 source == output: 只移 .pdf/.docx/.pptx -> archive_dir（MD 已在原位）
      - 超过 retention_days 的 archive 文件删除
    """
    if not source_dir or not os.path.isdir(source_dir):
        return {"error": f"源目录不存在: {source_dir}"}
    if not output_dir:
        return {"error": "未配置 output 目录"}

    same_dir = os.path.abspath(source_dir) == os.path.abspath(output_dir)
    moved_md, moved_src, skipped, deleted, errors = 0, 0, 0, 0, []

    for fname in os.listdir(source_dir):
        fpath = os.path.join(source_dir, fname)
        if not os.path.isfile(fpath):
            continue

        base, ext = os.path.splitext(fname)
        ext_lower = ext.lower()

        if ext_lower == ".md":
            if not same_dir:
                try:
                    r = _move(fpath, output_dir)
                    if r:
                        moved_md += 1
                except Exception as e:
                    errors.append(f"{fname}: {e}")

        elif ext_lower in (".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt",
                            ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif", ".jp2"):
            if has_md_sibling(fpath, output_dir):
                if archive_dir:
                    try:
                        r = _move(fpath, archive_dir)
                        if r:
                            moved_src += 1
                    except Exception as e:
                        errors.append(f"{fname}: {e}")
            else:
                skipped += 1

    # 清理过期归档
    if archive_dir and retention_days > 0 and os.path.isdir(archive_dir):
        cutoff = time.time() - retention_days * 86400
        for fname in os.listdir(archive_dir):
            fpath = os.path.join(archive_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                try:
                    os.remove(fpath)
                    deleted += 1
                except Exception as e:
                    errors.append(f"删除 {fname}: {e}")

    return {
        "moved_md": moved_md,
        "moved_source": moved_src,
        "skipped": skipped,
        "deleted_old": deleted,
        "errors": errors,
    }


def migrate_result(result: dict, cfg: dict) -> dict:
    """单文件迁移：转换成功后立即移动文件"""
    src_file = result.get("source_file", "")
    md_path = result.get("markdown_path", "")
    out = cfg["dirs"].get("output", "")
    arc = cfg["dirs"].get("archive", "")
    vault_root = cfg["vault"]["root"]

    same_dir = os.path.abspath(cfg["dirs"]["source"]) == os.path.abspath(out)
    moved = {}

    if md_path and os.path.exists(md_path) and not same_dir and out:
        try:
            r = _move(md_path, os.path.join(vault_root, out))
            if r:
                moved["md"] = r
        except Exception as e:
            moved["md_error"] = str(e)

    if src_file and os.path.exists(src_file) and arc:
        try:
            r = _move(src_file, os.path.join(vault_root, arc))
            if r:
                moved["source"] = r
        except Exception as e:
            moved["source_error"] = str(e)

    return moved


def cleanup_archive(archive_dir: str, retention_days: int = 7) -> dict:
    """清理过期归档"""
    if not archive_dir or not os.path.isdir(archive_dir):
        return {"error": f"归档目录不存在: {archive_dir}"}

    cutoff = time.time() - retention_days * 86400
    deleted = 0
    for fname in os.listdir(archive_dir):
        fpath = os.path.join(archive_dir, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            deleted += 1
    return {"deleted": deleted}
