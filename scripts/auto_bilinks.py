"""
auto_bilinks.py - 自动双链生成（SCHEMA v4.0.0）

功能：
- 基于 tags 匹配搜索相关页面
- 基于标题关键词匹配搜索相关页面
- 自动将双链写入 wiki 页面的 ## 关联连接 区块

用法：
  from auto_bilinks import auto_generate_bilinks
  bilinks = auto_generate_bilinks(new_page_path, wiki_dir, cfg)
"""

import os
import re
from pathlib import Path
from typing import List, Dict


# 跳过检查的目录
SKIP_DIRS = {"logs", "attachments", "templates", "_assets", "index.md"}


def find_pages_by_tag(tag: str, wiki_dir: str, max_results: int = 3) -> List[Dict]:
    """根据标签找到相关页面"""
    results = []
    tag_lower = tag.lower()

    if not os.path.isdir(wiki_dir):
        return results

    for root, dirs, files in os.walk(wiki_dir):
        # 跳过特定目录
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for fname in files:
            if not fname.endswith(".md"):
                continue

            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, wiki_dir)

            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read(2000)

                # 提取 tags
                fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                if not fm_match:
                    continue

                fm_text = fm_match.group(1)
                tags_match = re.search(r'tags:\s*\[?(.*?)\]?(?:\n|$)', fm_text)
                if not tags_match:
                    continue

                tags_str = tags_match.group(1)
                page_tags = [t.strip().strip('"').strip("'").lower() for t in tags_str.split(",")]

                if tag_lower in page_tags:
                    # 提取 title
                    title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                    title = title_match.group(1) if title_match else Path(fname).stem

                    results.append({
                        "path": fpath,
                        "rel_path": rel_path,
                        "title": title,
                        "wiki_name": Path(fname).stem,
                    })

                    if len(results) >= max_results:
                        return results
            except Exception:
                continue

    return results


def extract_keywords(title: str) -> List[str]:
    """从标题提取关键词"""
    # 移除常见词
    stop_words = {"的", "是", "和", "与", "或", "以及", "关于", "分析", "报告", "研究", "总", "结"}

    # 分词（简单按空格和特殊字符分割）
    words = re.split(r'[,\s\-_()（）]+', title)
    words = [w.strip() for w in words if w.strip() and len(w.strip()) >= 2]
    words = [w for w in words if w not in stop_words]

    return words[:5]  # 最多5个关键词


def find_pages_by_keyword(keyword: str, wiki_dir: str, max_results: int = 2) -> List[Dict]:
    """根据标题关键词找到相关页面"""
    results = []
    keyword_lower = keyword.lower()

    if not os.path.isdir(wiki_dir):
        return results

    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for fname in files:
            if not fname.endswith(".md") or fname == "index.md":
                continue

            fpath = os.path.join(root, fname)

            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read(2000)

                # 提取 title
                title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                if not title_match:
                    continue

                title = title_match.group(1)
                title_lower = title.lower()

                # 关键词匹配（标题中包含关键词）
                if keyword_lower in title_lower:
                    rel_path = os.path.relpath(fpath, wiki_dir)
                    results.append({
                        "path": fpath,
                        "rel_path": rel_path,
                        "title": title,
                        "wiki_name": Path(fname).stem,
                    })

                    if len(results) >= max_results:
                        return results
            except Exception:
                continue

    return results


def auto_generate_bilinks(
    new_page_path: str,
    wiki_dir: str,
    cfg: dict = None,
    max_links: int = 10,
) -> List[str]:
    """
    自动生成双链

    方式 1: 同 tag 页面
    方式 2: 标题关键词匹配

    Args:
        new_page_path: 新页面的文件路径
        wiki_dir: wiki 根目录
        cfg: 配置字典
        max_links: 最多生成的链接数

    Returns:
        双链列表 ["[[page-name]]", ...]
    """
    cfg = cfg or {}
    bilinks = []
    seen = set()

    if not os.path.exists(new_page_path):
        return bilinks

    # 读取新页面
    try:
        with open(new_page_path, "r", encoding="utf-8") as f:
            new_content = f.read()
    except Exception:
        return bilinks

    # 提取 front matter
    fm_match = re.match(r'^---\s*\n(.*?)\n---', new_content, re.DOTALL)
    if not fm_match:
        return bilinks

    fm_text = fm_match.group(1)

    # 提取 tags
    tags_match = re.search(r'tags:\s*\[?(.*?)\]?(?:\n|$)', fm_text)
    new_tags = []
    if tags_match:
        tags_str = tags_match.group(1)
        new_tags = [t.strip().strip('"').strip("'") for t in tags_str.split(",") if t.strip()]

    # 提取 title
    title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', new_content, re.MULTILINE)
    new_title = title_match.group(1) if title_match else ""

    # === 方式 1: 同 tag 页面 ===
    for tag in new_tags[:5]:  # 最多检查5个tag
        if len(bilinks) >= max_links:
            break

        pages = find_pages_by_tag(tag, wiki_dir, max_results=3)
        for page in pages:
            # 排除自己
            if os.path.normpath(page["path"]) == os.path.normpath(new_page_path):
                continue

            wiki_link = f"[[{page['wiki_name']]]]"
            if wiki_link not in seen:
                seen.add(wiki_link)
                bilinks.append(wiki_link)

    # === 方式 2: 标题关键词匹配 ===
    if new_title:
        keywords = extract_keywords(new_title)
        for kw in keywords[:5]:
            if len(bilinks) >= max_links:
                break

            pages = find_pages_by_keyword(kw, wiki_dir, max_results=2)
            for page in pages:
                if os.path.normpath(page["path"]) == os.path.normpath(new_page_path):
                    continue

                wiki_link = f"[[{page['wiki_name']}]]"
                if wiki_link not in seen:
                    seen.add(wiki_link)
                    bilinks.append(wiki_link)

    return bilinks[:max_links]


def add_bilinks_to_wiki_page(wiki_path: str, bilinks: List[str]) -> bool:
    """
    将双链追加到 wiki 页面的 ## 关联连接 区块

    Args:
        wiki_path: wiki 页面路径
        bilinks: 双链列表

    Returns:
        是否成功
    """
    if not bilinks or not os.path.exists(wiki_path):
        return False

    try:
        with open(wiki_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否已有关联连接区块
        link_section = re.search(r'##\s*关联连接\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)

        if link_section:
            existing = link_section.group(1).strip()
            if existing and "（待自动生成）" in existing:
                # 替换占位符
                new_content = link_section.group(1).replace(
                    "（待自动生成）",
                    "\n".join(f"- {link}" for link in bilinks)
                )
                content = content.replace(link_section.group(1), "\n" + new_content)
            elif existing:
                # 追加新链接
                new_links = "\n".join(f"- {link}" for link in bilinks)
                content = content.replace(
                    link_section.group(0),
                    link_section.group(0) + "\n" + new_links
                )
        else:
            # 没有关联连接区块，追加到文件末尾
            new_section = "\n## 关联连接\n\n" + "\n".join(f"- {link}" for link in bilinks) + "\n"
            content = content.rstrip() + new_section

        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"[ERROR] 追加双链失败: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="自动双链生成工具")
    parser.add_argument("page", help="要生成双链的 wiki 页面路径")
    parser.add_argument("--vault", "-v", required=True, help="Vault 根目录")
    parser.add_argument("--add-to-page", action="store_true", help="将双链追加到页面")
    parser.add_argument("--max", type=int, default=10, help="最多生成链接数")

    args = parser.parse_args()

    wiki_dir = os.path.join(args.vault, "wiki")
    bilinks = auto_generate_bilinks(args.page, wiki_dir, max_links=args.max)

    if not bilinks:
        # 尝试生成占位符
        bilinks = ["（暂无相关页面）"]

    print(f"🔗 自动双链 ({len(bilinks)} 个):")
    for link in bilinks:
        print(f"  {link}")

    if args.add_to_page:
        add_bilinks_to_wiki_page(args.page, bilinks)
        print(f"\n✅ 已将双链追加到页面")