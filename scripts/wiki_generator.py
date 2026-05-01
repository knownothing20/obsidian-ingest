"""
wiki_generator.py - MD→wiki 编译生成器

从 todo/ 目录读取 MD 文件，生成带 front matter 的 wiki 页面到 wiki/sources/
"""

import os
import re
import hashlib
from pathlib import Path
from datetime import datetime


# 从 config_loader.py 导入的常量
FILE_CATEGORY_MAP = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".pptx": "slides",
    ".ppt": "slides",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
    ".svg": "image",
    ".md": "markdown",
}


def classify_file(filename: str) -> str:
    """根据文件扩展名分类"""
    ext = os.path.splitext(filename)[1].lower()
    return FILE_CATEGORY_MAP.get(ext, "unknown")


def extract_title(content: str, filename: str) -> str:
    """从 MD 内容或文件名提取标题"""
    # 尝试从第一行 H1 提取
    lines = content.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    
    # 回退到文件名（去掉扩展名和 _md 后缀）
    title = os.path.splitext(filename)[0]
    title = re.sub(r'\.md$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'_md$', '', title, flags=re.IGNORECASE)
    return title


def _parse_front_matter_end(content: str) -> int:
    """解析 front matter 结束位置，返回最后一个 --- 的行索引。无 front matter 返回 -1。"""
    lines = content.strip().split("\n")
    if not lines or lines[0].strip() != "---":
        return -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return -1


def _extract_body_lines(content: str) -> list[str]:
    """提取 MD 正文行（跳过 front matter）"""
    lines = content.strip().split("\n")
    fm_end = _parse_front_matter_end(content)
    if fm_end < 0:
        return lines
    return lines[fm_end + 1:]


def extract_content_proof(content: str, max_length: int = 500) -> str:
    """
    从 MD 内容提取 content_proof（原文引用）
    
    策略：
    1. 跳过 front matter（--- 之间的内容）
    2. 跳过代码块
    3. 取第一段有意义的文本
    """
    lines = content.strip().split("\n")
    
    # 跳过 front matter
    front_matter_end = _parse_front_matter_end(content)
    if front_matter_end < 0:
        front_matter_end = 0
    
    # 从正文开始查找
    body_lines = lines[front_matter_end + 1:]
    
    # 收集第一段有意义的文本（跳过空行和代码块）
    proof_lines = []
    in_code_block = False
    
    for line in body_lines:
        line = line.strip()
        
        # 跳过代码块
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        
        if in_code_block:
            continue
        
        # 跳过图片和链接
        if line.startswith("![]") or line.startswith("[!"):
            continue
        
        # 跳过 HTML 注释
        if line.startswith("<!--") or line.startswith("-->"):
            continue
        
        # 跳过空行
        if not line:
            # 如果已经收集了一些内容，这是段落分隔
            if proof_lines:
                break
            continue
        
        # 跳过 markdown 标题行
        if re.match(r'^#{1,6}\s+', line):
            continue
        
        # 跳过列表项
        if re.match(r'^[\s]*[-*+]\s+', line) or re.match(r'^[\s]*\d+\.\s+', line):
            continue
        
        # 跳过表格行
        if re.match(r'^[\s]*\|', line):
            continue
        
        # 跳过分隔线
        if re.match(r'^[\s]*[-*_]{3,}', line):
            continue
        
        # 这是一段有意义的文本
        proof_lines.append(line)
        
        if len(" ".join(proof_lines)) > max_length:
            break
    
    proof = " ".join(proof_lines)
    
    # 清理引用格式
    proof = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', proof)  # [text](url) -> text
    proof = re.sub(r'[*_`]+', '', proof)  # 移除格式
    
    return proof.strip()


def compute_file_hash(file_path: str) -> str:
    """计算文件内容 hash"""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def generate_wiki_filename(title: str, category: str = "source", counter: int = 0) -> str:
    """生成 wiki 文件名（slug 格式）"""
    # 转换为 slug
    slug = title.lower()
    # 替换特殊字符
    slug = re.sub(r'[^\w\s-]', '', slug)  # 移除非字母数字
    slug = re.sub(r'[-\s]+', '-', slug)   # 连字符替换空格
    slug = slug.strip('-')
    # 限制长度
    if len(slug) > 50:
        slug = slug[:50]
    # 添加序号避免重复
    if counter > 0:
        slug = f"{slug}-{counter}"
    return slug


def compile_single_md(md_path: str, wiki_dir: str, cfg: dict = None) -> dict:
    """
    编译单个 MD 文件到 wiki 页面
    
    Args:
        md_path: MD 文件完整路径
        wiki_dir: wiki 目录路径
        cfg: 配置字典（可选）
        
    Returns:
        {
            "success": bool,
            "wiki_path": str,
            "error": str
        }
    """
    if not os.path.exists(md_path):
        return {"success": False, "error": "MD 文件不存在", "wiki_path": ""}
    
    # 读取 MD 内容
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 提取标题
    filename = os.path.basename(md_path)
    title = extract_title(content, filename)
    
    # 提取 content_proof
    content_proof = extract_content_proof(content)
    if len(content_proof) < 20:
        # 尝试从正文获取更多内容作为 fallback
        body_lines = _extract_body_lines(content)
        
        body_text = ' '.join(body_lines[:5])
        body_text = re.sub(r'\[[^\]]+\]([^)]+)', r'\1', body_text)
        body_text = re.sub(r'[*_`#]+', '', body_text)
        body_text = body_text.strip()
        
        if len(body_text) >= 20:
            content_proof = body_text[:500]
        else:
            # 最终 fallback：无法自动提取内容，标记为需人工审核
            fname_proof = os.path.splitext(filename)[0]
            fname_proof = re.sub(r'[_.\-]', ' ', fname_proof)
            content_proof = f"（待人工审核：{fname_proof}）"

    # 保证最低 20 字（使用占位符标识需人工处理）
    if len(content_proof) < 20:
        content_proof = f"（待人工审核：{os.path.splitext(filename)[0]}）"
    
    # 确保 content_proof 不超过 500 字
    if len(content_proof) > 500:
        content_proof = content_proof[:500]
    
    # 计算文件 hash（提前计算，skip 检查和 front matter 都需要）
    file_hash = compute_file_hash(md_path)
    
    # 生成 wiki 文件名
    category = classify_file(filename)
    
    # 检查重复文件名，添加序号
    counter = 0
    while True:
        slug = generate_wiki_filename(title, category, counter)
        wiki_filename = f"{slug}.md"
        wiki_path = os.path.join(wiki_dir, wiki_filename)
        
        # 如果文件不存在，使用这个路径
        if not os.path.exists(wiki_path):
            break
        
        # 检查现有文件的 source_hash 是否相同（相同则跳过）
        try:
            with open(wiki_path, "r", encoding="utf-8") as f:
                existing = f.read()
            match = re.search(r'source_hash:\s*"([^"]+)"', existing)
            if match and match.group(1) == file_hash:
                # 相同文件已存在，返回成功
                return {
                    "success": True,
                    "wiki_path": wiki_path,
                    "wiki_filename": wiki_filename,
                    "title": title,
                    "content_proof_length": len(content_proof),
                    "skipped": True
                }
        except:
            pass
        
        counter += 1
        if counter > 100:
            return {"success": False, "error": "无法生成唯一文件名", "wiki_path": ""}
    
    # 生成 front matter
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    
    front_matter = f"""---
title: "{title}"
type: source
validity: current
category: {category}
content_proof: "{content_proof}"
source_hash: "{file_hash}"
registered_at: "{timestamp}"
---
"""
    
    # 写入 wiki 页面（附加原有内容）
    wiki_content = front_matter + "\n" + content
    
    # 确保目录存在
    os.makedirs(wiki_dir, exist_ok=True)
    
    with open(wiki_path, "w", encoding="utf-8") as f:
        f.write(wiki_content)
    
    return {
        "success": True,
        "wiki_path": wiki_path,
        "wiki_filename": wiki_filename,
        "title": title,
        "content_proof_length": len(content_proof),
        "skipped": False
    }


def compile_batch(todo_dir: str, wiki_dir: str, limit: int = 50, cfg: dict = None, vault_root: str = "") -> dict:
    """
    批量编译 todo/ 中的 MD 文件
    
    Args:
        todo_dir: todo 目录路径
        wiki_dir: wiki 目录路径
        limit: 最多处理数量
        cfg: 配置字典
        vault_root: vault 根目录（用于联动 compile_queue）
        
    Returns:
        {
            "total": int,
            "success": int,
            "failed": int,
            "skipped": int,
            "details": [...]
        }
    """
    result = {
        "total": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "details": []
    }
    
    if not os.path.isdir(todo_dir):
        print(f"[ERROR] 目录不存在: {todo_dir}")
        return result
    
    # 确保 wiki 目录存在
    os.makedirs(wiki_dir, exist_ok=True)
    
    # 联动 compile_queue
    compile_queue = None
    if vault_root:
        try:
            from compile_queue import CompileQueue
            compile_queue = CompileQueue(vault_root=vault_root)
        except Exception:
            pass  # 无法加载 compile_queue，跳过联动
    
    # 遍历 todo/ 目录
    for root, dirs, files in os.walk(todo_dir):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        
        for fname in files:
            if not fname.endswith(".md"):
                continue
            
            if result["total"] >= limit:
                break
            
            md_path = os.path.join(root, fname)
            rel_path = os.path.relpath(md_path, todo_dir)
            result["total"] += 1
            
            # 检查文件大小（跳过太小的）
            if os.path.getsize(md_path) < 100:
                result["skipped"] += 1
                result["details"].append({
                    "file": rel_path,
                    "status": "skipped",
                    "reason": "文件太小"
                })
                continue
            
            # 编译
            try:
                # 联动 compile_queue：标记开始
                if compile_queue:
                    compile_queue.start(rel_path)
                
                compile_result = compile_single_md(md_path, wiki_dir, cfg)
                if compile_result.get("success"):
                    if compile_result.get("skipped"):
                        result["skipped"] += 1
                        result["details"].append({
                            "file": rel_path,
                            "status": "skipped",
                            "reason": "文件已存在且内容相同"
                        })
                        if compile_queue:
                            compile_queue.done(rel_path, wiki_path=compile_result.get("wiki_filename", ""))
                    else:
                        result["success"] += 1
                        result["details"].append({
                            "file": rel_path,
                            "status": "success",
                            "wiki": compile_result.get("wiki_filename", "")
                        })
                        # 联动 compile_queue：标记完成
                        if compile_queue:
                            compile_queue.done(rel_path, wiki_path=compile_result.get("wiki_filename", ""))
                        # 归档 MD 文件到 archive/md-archive/
                        try:
                            import shutil as _shutil
                            md_archive_dir = os.path.join(vault_root, "archive", "md-archive")
                            md_archive_subdir = os.path.join(md_archive_dir, os.path.dirname(rel_path))
                            os.makedirs(md_archive_subdir, exist_ok=True)
                            md_dst = os.path.join(md_archive_subdir, fname)
                            _shutil.move(md_path, md_dst)
                        except Exception as _e:
                            print(f"  ⚠️ MD 归档失败: {rel_path}: {_e}")
                else:
                    result["failed"] += 1
                    result["details"].append({
                        "file": rel_path,
                        "status": "failed",
                        "error": compile_result.get("error", "未知错误")
                    })
                    # 联动 compile_queue：标记失败
                    if compile_queue:
                        compile_queue.fail(rel_path, error=compile_result.get("error", "未知错误"))
            except Exception as e:
                result["failed"] += 1
                result["details"].append({
                    "file": rel_path,
                    "status": "failed",
                    "error": str(e)
                })
                # 联动 compile_queue：标记失败
                if compile_queue:
                    compile_queue.fail(rel_path, error=str(e))
        
        if result["total"] >= limit:
            break
    
    return result


# ── CLI 入口 ─────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="MD→Wiki 编译生成器")
    parser.add_argument("--vault", "-v", help="Vault 根目录")
    parser.add_argument("--todo", "-t", help="todo 目录（默认 vault/todo）")
    parser.add_argument("--wiki", "-w", help="wiki 目录（默认 vault/wiki/sources）")
    parser.add_argument("--limit", "-l", type=int, default=10, help="批量处理数量")
    
    sub = parser.add_subparsers(dest="cmd", help="子命令")
    
    # compile
    p_compile = sub.add_parser("compile", help="编译 MD→Wiki")
    p_compile.add_argument("--dry-run", action="store_true", help="预览不执行")
    
    # batch
    p_batch = sub.add_parser("batch", help="批量编译")
    p_batch.add_argument("--dry-run", action="store_true", help="预览不执行")
    
    # single
    p_single = sub.add_parser("single", help="编译单个文件")
    p_single.add_argument("file", help="MD 文件路径")
    
    args = parser.parse_args()
    
    vault = args.vault or os.environ.get("OBSIDIAN_VAULT")
    if not vault:
        print("[ERROR] 未指定 vault（--vault 或 OBSIDIAN_VAULT）")
        return
    
    # 确定目录
    todo_dir = args.todo or os.path.join(vault, "todo")
    wiki_dir = args.wiki or os.path.join(vault, "wiki", "sources")
    
    if args.cmd == "compile" or args.cmd == "batch":
        if args.dry_run:
            # 只统计不执行
            count = 0
            for root, dirs, files in os.walk(todo_dir):
                for f in files:
                    if f.endswith(".md") and os.path.getsize(os.path.join(root, f)) >= 100:
                        count += 1
            print(f"🔍 预览模式：可处理 {count} 个文件")
            return
        
        print(f"📁 输入目录: {todo_dir}")
        print(f"📁 输出目录: {wiki_dir}")
        print(f"📊 限制数量: {args.limit}")
        print()
        
        result = compile_batch(todo_dir, wiki_dir, limit=args.limit, vault_root=vault)
        
        print(f"✅ 编译完成")
        print(f"  总计: {result['total']}")
        print(f"  成功: {result['success']}")
        print(f"  失败: {result['failed']}")
        print(f"  跳过: {result['skipped']}")
        
        if result["details"]:
            print(f"\n📋 详情：")
            for d in result["details"][:5]:
                status = d.get("status", "")
                if status == "success":
                    print(f"  ✅ {d['file']} → {d.get('wiki', '')}")
                elif status == "failed":
                    print(f"  ❌ {d['file']}: {d.get('error', '')}")
                else:
                    print(f"  ⏭ {d['file']}: {d.get('reason', '')}")
    
    elif args.cmd == "single":
        md_path = args.file
        if not os.path.isabs(md_path):
            md_path = os.path.join(todo_dir, md_path)
        
        result = compile_single_md(md_path, wiki_dir)
        if result.get("success"):
            print(f"✅ 编译成功: {result.get('wiki_path', '')}")
        else:
            print(f"❌ 编译失败: {result.get('error', '')}")
    
    else:
        # 默认显示帮助
        parser.print_help()


if __name__ == "__main__":
    main()