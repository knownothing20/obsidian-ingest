"""
knowledge_conflict_detector.py - 知识冲突检测引擎（SCHEMA v4.0.0）

功能：
- Stage 1: 规则引擎 — 标签/关键词匹配找到候选页面
- Stage 2: 轻量对比 — 核心观点文本相似度（Jaccard/余弦）
- Stage 3: LLM 深度对比 — 语义级检测矛盾（预留）

输出格式：
- potential_duplicate: 高相似度 → 可能重复
- potential_conflict: 同主题但观点差异大 → 可能冲突
- 冲突记录写入 wiki 页面的 ## 知识冲突 区块

用法：
  from knowledge_conflict_detector import check_knowledge_conflict
  conflicts = check_knowledge_conflict(new_page, wiki_dir, cfg)
"""

import os
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple


def find_related_pages(tags: List[str], wiki_dir: str, max_results: int = 10) -> List[Dict]:
    """
    Stage 1: 规则引擎 - 根据标签找到相关页面

    Args:
        tags: 新页面的标签列表
        wiki_dir: wiki 根目录
        max_results: 最多返回多少结果

    Returns:
        相关页面列表 [{"path": str, "title": str, "tags": List[str]}]
    """
    if not tags:
        return []

    related = []
    tags_lower = [t.lower() for t in tags]

    for root, dirs, files in os.walk(wiki_dir):
        # 跳过非 wiki 目录
        rel_path = os.path.relpath(root, wiki_dir)
        if rel_path.startswith("logs") or rel_path.startswith("attachments"):
            continue

        for fname in files:
            if not fname.endswith(".md"):
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read(2000)  # 只读前面部分

                # 提取 front matter
                page_tags = []
                fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                if fm_match:
                    fm_text = fm_match.group(1)
                    # 提取 tags
                    tags_match = re.search(r'tags:\s*\[?(.*?)\]?(?:\n|$)', fm_text)
                    if tags_match:
                        tags_str = tags_match.group(1)
                        page_tags = [t.strip().strip('"').strip("'") for t in tags_str.split(",")]

                # 提取 title
                title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                title = title_match.group(1) if title_match else Path(fname).stem

                # 计算标签重叠
                page_tags_lower = [t.lower() for t in page_tags]
                overlap = len(set(tags_lower) & set(page_tags_lower))

                if overlap > 0:
                    related.append({
                        "path": fpath,
                        "title": title,
                        "tags": page_tags,
                        "overlap": overlap,
                    })
            except Exception:
                continue

    # 按重叠数排序
    related.sort(key=lambda x: x["overlap"], reverse=True)
    return related[:max_results]


def compute_text_similarity(text1: str, text2: str) -> float:
    """
    Stage 2: 轻量对比 - 计算文本相似度（Jaccard）

    Args:
        text1: 文本1
        text2: 文本2

    Returns:
        相似度 0-1
    """
    # 分词
    words1 = set(re.findall(r'[\w]+', text1.lower()))
    words2 = set(re.findall(r'[\w]+', text2.lower()))

    # 去除短词
    words1 = {w for w in words1 if len(w) >= 2}
    words2 = {w for w in words2 if len(w) >= 2}

    if not words1 or not words2:
        return 0.0

    # Jaccard 相似度
    intersection = len(words1 & words2)
    union = len(words1 | words2)

    return intersection / union if union > 0 else 0.0


def extract_core_points(wiki_content: str) -> str:
    """从 wiki 页面提取核心观点文本"""
    # 尝试多种区块名称
    for section_name in ["核心观点", "关键要点", "核心定义"]:
        match = re.search(rf'##\s*{section_name}\s*\n(.*?)(?=\n##|\Z)', wiki_content, re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def check_knowledge_conflict(
    new_page_path: str,
    wiki_dir: str,
    cfg: dict = None,
) -> List[Dict]:
    """
    检查新页面是否与已有页面存在知识冲突

    逻辑：
    1. 提取新页面的 tags 和内容
    2. 根据 tags 找到候选相关页面
    3. 对每个候选页面计算文本相似度
    4. 根据相似度判断：
       - > 0.7: potential_duplicate（高相似，可能重复）
       - < 0.1 且同主题: potential_conflict（可能冲突）

    Returns:
        冲突列表 [{
            "type": "potential_duplicate" | "potential_conflict",
            "page": str,  # 相关页面路径
            "title": str,
            "overlap": float,
            "note": str
        }]
    """
    cfg = cfg or {}
    conflicts = []

    if not os.path.exists(new_page_path):
        return conflicts

    # 读取新页面
    try:
        with open(new_page_path, "r", encoding="utf-8") as f:
            new_content = f.read()
    except Exception:
        return conflicts

    # 提取新页面的 tags
    fm_match = re.match(r'^---\s*\n(.*?)\n---', new_content, re.DOTALL)
    if not fm_match:
        return conflicts

    fm_text = fm_match.group(1)
    tags_match = re.search(r'tags:\s*\[?(.*?)\]?(?:\n|$)', fm_text)
    new_tags = []
    if tags_match:
        tags_str = tags_match.group(1)
        new_tags = [t.strip().strip('"').strip("'") for t in tags_str.split(",")]

    # 提取新页面的核心观点
    new_core_points = extract_core_points(new_content)

    # Stage 1: 找到候选页面
    candidates = find_related_pages(new_tags, wiki_dir)

    # Stage 2: 对比每个候选
    for candidate in candidates:
        try:
            with open(candidate["path"], "r", encoding="utf-8") as f:
                candidate_content = f.read()
        except Exception:
            continue

        # 提取候选页面的核心观点
        candidate_core = extract_core_points(candidate_content)

        if not candidate_core:
            continue

        # 计算相似度
        overlap = compute_text_similarity(new_core_points, candidate_core)

        if overlap > 0.7:
            # 高相似度 → 可能重复
            conflicts.append({
                "type": "potential_duplicate",
                "page": candidate["path"],
                "title": candidate["title"],
                "overlap": overlap,
                "note": f"核心观点相似度 {overlap:.0%}，建议人工复核是否重复"
            })
        elif overlap < 0.1 and candidate["overlap"] >= 2:
            # 同主题（标签重叠 >= 2）但观点差异大 → 可能冲突
            conflicts.append({
                "type": "potential_conflict",
                "page": candidate["path"],
                "title": candidate["title"],
                "overlap": overlap,
                "note": f"同主题但核心观点差异大，建议人工复核"
            })

    return conflicts


def add_conflict_to_wiki_page(wiki_path: str, conflicts: List[Dict]) -> bool:
    """
    将冲突信息追加到 wiki 页面的 ## 知识冲突 区块

    Args:
        wiki_path: wiki 页面路径
        conflicts: 冲突列表

    Returns:
        是否成功
    """
    if not conflicts or not os.path.exists(wiki_path):
        return False

    try:
        with open(wiki_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否已有知识冲突区块
        conflict_section = re.search(r'##\s*知识冲突\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)

        if conflict_section:
            # 追加到现有区块
            existing = conflict_section.group(1).strip()
            if existing and existing != "（暂无冲突记录）":
                # 已有记录，追加新冲突
                new_entries = "\n".join(f"- {c['title']}: {c['note']}" for c in conflicts)
                new_content = conflict_section.group(0) + "\n" + new_entries + "\n"
                content = content.replace(conflict_section.group(0), new_content)
            else:
                # 无记录，替换占位符
                new_entries = "\n".join(f"- {c['title']}: {c['note']}" for c in conflicts)
                content = content.replace(
                    conflict_section.group(0),
                    f"## 知识冲突\n\n{new_entries}"
                )
        else:
            # 没有知识冲突区块，追加到文件末尾
            new_section = "\n## 知识冲突\n\n" + "\n".join(
                f"- {c['title']}: {c['note']}" for c in conflicts
            )
            content = content.rstrip() + new_section + "\n"

        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"[ERROR] 追加知识冲突失败: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="知识冲突检测工具")
    parser.add_argument("page", help="待检测的 wiki 页面路径")
    parser.add_argument("--vault", "-v", required=True, help="Vault 根目录")
    parser.add_argument("--add-to-page", action="store_true", help="将冲突结果追加到页面")

    args = parser.parse_args()

    wiki_dir = os.path.join(args.vault, "wiki")
    conflicts = check_knowledge_conflict(args.page, wiki_dir)

    if not conflicts:
        print("✅ 未发现知识冲突")
    else:
        print(f"⚠️ 发现 {len(conflicts)} 个潜在冲突:")
        for c in conflicts:
            icon = "🔄" if c["type"] == "potential_duplicate" else "⚡"
            print(f"  {icon} [{c['type']}] {c['title']}")
            print(f"     {c['note']}")

        if args.add_to_page:
            add_conflict_to_wiki_page(args.page, conflicts)
            print(f"\n✅ 已将冲突记录追加到页面")