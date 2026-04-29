"""
compiler.py - 知识库编译引擎

MD → wiki 页面的完整流水线：
  Step 1: 格式清洗（纯规则）
  Step 2: Front Matter 注入
  Step 3: 摘要生成（轻量/深度）
  Step 4: 排重检测
  Step 5: Index 更新
"""

import os
import re
import sys
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

_TZ_CN = timezone(timedelta(hours=8))


def _now_date() -> str:
    return datetime.now(_TZ_CN).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(_TZ_CN).isoformat(timespec="seconds")


# ── Step 1: 格式清洗 ─────────────────────────────────────────────

def clean_format(md_text: str) -> str:
    """清洗 MinerU 输出的 Markdown，使其 Obsidian 兼容"""
    lines = md_text.split("\n")
    cleaned = []
    prev_blank = False

    for line in lines:
        # 去除尾部空白
        line = line.rstrip()

        # 跳过连续空行（最多保留一个）
        if not line:
            if not prev_blank:
                cleaned.append("")
                prev_blank = True
            continue
        prev_blank = False

        # 标题层级修正：确保从 h1 开始，不跳级
        # MinerU 有时输出 ### 但没有 # 和 ##
        # 这里只做基本修正，不改变语义

        # 图片路径标准化：相对路径统一用 /
        line = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _normalize_image_link, line)

        # LaTeX 公式：确保行内公式被 $ 包裹
        # MinerU 有时输出 \(...\) 格式
        line = re.sub(r'\\\((.+?)\\\)', r'$\1$', line)
        line = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', line, flags=re.DOTALL)

        cleaned.append(line)

    # 移除开头的空行
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)

    # 确保文件以换行符结尾
    if cleaned and cleaned[-1]:
        cleaned.append("")

    return "\n".join(cleaned)


def _normalize_image_link(match) -> str:
    alt = match.group(1)
    path = match.group(2)
    # 统一路径分隔符
    path = path.replace("\\", "/")
    # 移除多余的 ./ 前缀
    path = re.sub(r'^\./+', '', path)
    return f"![{alt}]({path})"


# ── Step 2: Front Matter 注入 ─────────────────────────────────────

def extract_title(md_text: str, filename: str) -> str:
    """从 MD 或文件名提取标题"""
    # 优先从 h1 提取
    m = re.search(r'^#\s+(.+)$', md_text, re.MULTILINE)
    if m:
        return m.group(1).strip()

    # 从文件名提取（去掉时间戳前缀和扩展名）
    name = Path(filename).stem
    # 去掉常见时间戳格式：202212-xxx, 2022-12-xxx, 20221215-xxx
    name = re.sub(r'^\d{4}[-_]?\d{2}[-_]?\d{0,2}[-_\s]*', '', name)
    # 去掉序号前缀：01-xxx, 001-xxx
    name = re.sub(r'^\d{1,3}[-_\s]+', '', name)
    return name.strip() or Path(filename).stem


def extract_date_from_filename(filename: str) -> str:
    """从文件名提取日期"""
    name = Path(filename).stem

    # 20221215
    m = re.search(r'(\d{4})(\d{2})(\d{2})', name)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"

    # 2022-12-15 or 2022_12_15
    m = re.search(r'(\d{4})[-_](\d{2})[-_](\d{2})', name)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2000 <= y <= 2099:
            return f"{y}-{mo:02d}-{d:02d}"

    # 202212
    m = re.search(r'(\d{4})(\d{2})', name)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 2000 <= y <= 2099 and 1 <= mo <= 12:
            return f"{y}-{mo:02d}-01"

    # 2022-12
    m = re.search(r'(\d{4})[-_](\d{2})', name)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 2000 <= y <= 2099 and 1 <= mo <= 12:
            return f"{y}-{mo:02d}-01"

    return ""


def extract_tags_from_path(rel_path: str) -> list:
    """从路径提取 tags（目录名作为 tag）"""
    parts = Path(rel_path).parts
    tags = []
    # 跳过 raw/ 和文件名，取中间目录名
    for part in parts[:-1]:
        if part.lower() in ("raw", "todo", "done", "processing", "09-archive", "assets"):
            continue
        # 清理目录名作为 tag
        tag = part.strip().replace(" ", "-")
        if tag and len(tag) < 30:
            tags.append(tag)
    return tags


def build_frontmatter(
    title: str,
    source_file: str,
    tags: list = None,
    created: str = "",
    page_type: str = "source",
    validity: str = "current",
) -> str:
    """构建 YAML front matter"""
    now = _now_date()
    created = created or now

    tags_str = ", ".join(tags) if tags else ""

    # sources 路径格式
    source_rel = source_file.replace("\\", "/")

    fm = f"""---
title: "{title}"
type: {page_type}
tags: [{tags_str}]
sources: ["{source_rel}"]
created: {created}
last_updated: {now}
last_verified: {now}
validity: {validity}
expires:
---"""
    return fm


def inject_frontmatter(
    md_text: str,
    source_file: str,
    tags: list = None,
    created: str = "",
    page_type: str = "source",
) -> str:
    """给 MD 注入 front matter（如果还没有的话）"""
    # 检查是否已有 front matter
    if md_text.lstrip().startswith("---"):
        # 已有，不重复注入
        return md_text

    title = extract_title(md_text, source_file)
    if not created:
        created = extract_date_from_filename(source_file)
    if not tags:
        tags = extract_tags_from_path(source_file)

    fm = build_frontmatter(title, source_file, tags, created, page_type)
    return fm + "\n\n" + md_text


# ── Step 3: 摘要生成 ──────────────────────────────────────────────

def generate_summary_light(md_text: str, max_length: int = 500) -> dict:
    """轻量模式：纯规则提取摘要（零 AI 成本）"""
    lines = md_text.split("\n")

    # 一句话总结：取前 max_length 个字符的正文
    body_lines = []
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if stripped and not stripped.startswith("#"):
            body_lines.append(stripped)
        if sum(len(l) for l in body_lines) > max_length:
            break

    one_line = " ".join(body_lines)[:max_length]
    if len(one_line) >= max_length:
        one_line = one_line[:max_length - 3] + "..."

    # 核心观点：提取所有 h2 标题
    h2_list = []
    for line in lines:
        m = re.match(r'^##\s+(.+)$', line)
        if m:
            heading = m.group(1).strip()
            # 跳过模板区块
            if heading in ("关联连接", "知识冲突", "References", "参考文献"):
                continue
            h2_list.append(heading)

    return {
        "one_line": one_line,
        "key_points": h2_list[:8],  # 最多 8 个
    }


def generate_summary_deep(md_text: str, llm_config: dict = None) -> dict:
    """深度模式：LLM 生成摘要（消耗 Token）"""
    # TODO: 实现 LLM 调用
    # 暂时回退到轻量模式
    return generate_summary_light(md_text)


# ── Step 4: 排重检测 ──────────────────────────────────────────────

def check_duplicate_by_title(title: str, wiki_dir: str) -> Optional[str]:
    """检查 wiki 目录中是否有同标题页面"""
    if not os.path.isdir(wiki_dir):
        return None
    for root, dirs, files in os.walk(wiki_dir):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    head = f.read(500)
                # 检查 front matter 中的 title
                m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', head, re.MULTILINE)
                if m and m.group(1).strip().lower() == title.lower():
                    return os.path.relpath(fpath, wiki_dir).replace("\\", "/")
            except Exception:
                continue
    return None


def check_duplicate_by_hash(md_text: str, wiki_dir: str) -> Optional[str]:
    """检查内容 hash 是否重复"""
    # 去掉 front matter 后取 hash
    body = re.sub(r'^---\n.*?\n---\n', '', md_text, count=1, flags=re.DOTALL)
    body_hash = hashlib.md5(body.strip().encode("utf-8")).hexdigest()

    if not os.path.isdir(wiki_dir):
        return None
    for root, dirs, files in os.walk(wiki_dir):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    existing = f.read()
                existing_body = re.sub(r'^---\n.*?\n---\n', '', existing, count=1, flags=re.DOTALL)
                existing_hash = hashlib.md5(existing_body.strip().encode("utf-8")).hexdigest()
                if body_hash == existing_hash:
                    return os.path.relpath(fpath, wiki_dir).replace("\\", "/")
            except Exception:
                continue
    return None


# ── Step 5: Index 更新 ────────────────────────────────────────────

def update_index(wiki_dir: str, page_type: str, page_name: str, summary: str):
    """更新 wiki/index.md"""
    index_path = os.path.join(wiki_dir, "index.md")
    if not os.path.exists(index_path):
        # 创建初始 index
        _create_index(index_path)

    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 根据类型找到对应区块
    section_map = {
        "concept": "## 概念 (Concepts)",
        "entity": "## 实体 (Entities)",
        "source": "## 来源 (Sources)",
        "synthesis": "## 综合 (Syntheses)",
    }
    section_header = section_map.get(page_type, "## 来源 (Sources)")
    new_entry = f"- [[{page_name}]] — {summary}"

    # 检查是否已存在
    if f"[[{page_name}]]" in content:
        return  # 已存在，不重复添加

    # 在对应区块末尾追加
    if section_header in content:
        # 找到下一个 ## 的位置
        idx = content.index(section_header)
        rest = content[idx + len(section_header):]
        next_section = rest.find("\n## ")
        if next_section == -1:
            # 没有下一个区块，追加到末尾
            content = content.rstrip() + "\n" + new_entry + "\n"
        else:
            insert_pos = idx + len(section_header) + next_section
            content = content[:insert_pos].rstrip() + "\n" + new_entry + "\n" + content[insert_pos:]
    else:
        # 区块不存在，追加到末尾
        content = content.rstrip() + f"\n\n{section_header}\n{new_entry}\n"

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(content)


def _create_index(index_path: str):
    """创建初始 index.md"""
    content = """# Wiki 索引

> 自动生成，勿手动编辑

## 概念 (Concepts)

## 实体 (Entities)

## 来源 (Sources)

## 综合 (Syntheses)
"""
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(content)


# ── 编译入口 ──────────────────────────────────────────────────────

def compile_md(
    md_path: str,
    source_file: str,
    cfg: dict,
    dry_run: bool = False,
) -> dict:
    """
    编译单个 MD 文件为 wiki 页面

    Args:
        md_path: MinerU 生成的 MD 文件路径
        source_file: 原始 PDF/DOCX 路径（用于提取 metadata）
        cfg: 完整配置
        dry_run: 只返回结果，不写入

    Returns:
        {success, wiki_path, summary, duplicate_of, skipped, error}
    """
    vault_root = cfg["vault"]["root"]
    wiki_dir = os.path.join(vault_root, cfg["vault"]["wiki_dir"])
    sources_dir = os.path.join(wiki_dir, "sources")
    mode = cfg["compile"]["mode"]

    # 读取 MD
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()
    except Exception as e:
        return {"success": False, "error": f"读取 MD 失败: {e}"}

    if not md_text.strip():
        return {"success": False, "error": "MD 文件为空"}

    # Step 1: 格式清洗
    md_text = clean_format(md_text)

    # Step 2: Front Matter
    source_basename = os.path.basename(source_file)
    tags = extract_tags_from_path(os.path.relpath(source_file, vault_root))
    created = extract_date_from_filename(source_basename)
    md_text = inject_frontmatter(md_text, source_basename, tags, created)
    title = extract_title(md_text, source_basename)

    # Step 3: 摘要
    if mode == "deep":
        summary_data = generate_summary_deep(md_text, cfg["compile"]["llm"])
    else:
        summary_data = generate_summary_light(md_text)

    # Step 4: 排重
    if cfg["compile"]["dedup"]["enabled"]:
        dup = check_duplicate_by_title(title, wiki_dir)
        if dup:
            return {"success": True, "skipped": True, "duplicate_of": dup,
                    "reason": f"标题重复: {dup}"}

    # 生成 wiki 文件名
    wiki_name = _to_kebab(title)
    wiki_path = os.path.join(sources_dir, f"{wiki_name}.md")

    if dry_run:
        return {
            "success": True,
            "wiki_path": wiki_path,
            "title": title,
            "summary": summary_data["one_line"],
            "key_points": summary_data["key_points"],
            "tags": tags,
        }

    # 写入 wiki 页面
    os.makedirs(sources_dir, exist_ok=True)
    # 检查是否需要追加到已有页面
    if os.path.exists(wiki_path):
        # 追加 sources 到已有页面的 front matter
        _append_source_to_existing(wiki_path, source_file)
    else:
        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write(md_text)

    # Step 5: 更新 index
    update_index(wiki_dir, "source", wiki_name, summary_data["one_line"])

    return {
        "success": True,
        "wiki_path": wiki_path,
        "wiki_name": wiki_name,
        "title": title,
        "summary": summary_data["one_line"],
        "tags": tags,
    }


def _to_kebab(title: str) -> str:
    """标题转 kebab-case"""
    # 中文保留，英文转小写
    s = title.strip().lower()
    # 替换特殊字符为连字符
    s = re.sub(r'[^\w\u4e00-\u9fff]+', '-', s)
    # 去掉首尾连字符
    s = s.strip('-')
    # 合并连续连字符
    s = re.sub(r'-+', '-', s)
    return s or "untitled"


def _append_source_to_existing(wiki_path: str, source_file: str):
    """向已有 wiki 页面追加 source 引用"""
    try:
        with open(wiki_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return

    source_rel = source_file.replace("\\", "/")
    if source_rel in content:
        return  # 已存在

    # 在 sources 字段追加
    if "sources:" in content:
        content = content.replace(
            "sources: [",
            f'sources: ["{source_rel}", ',
            1
        )
    else:
        # 没有 sources 字段，在 front matter 末尾追加
        content = content.replace(
            "\n---",
            f'\nsources: ["{source_rel}"]\n---',
            1
        )

    with open(wiki_path, "w", encoding="utf-8") as f:
        f.write(content)
