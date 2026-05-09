"""
upgrade_links.py - 关联连接自动升级工具

定期运行：将 wiki 页面「关联连接」中的纯文本引用升级为 [[wiki-link]]，
当目标文件在 vault 中存在时自动建立双向链接。

用法:
    python upgrade_links.py --vault "YOUR_VAULT_PATH"          # 执行
    python upgrade_links.py --vault "YOUR_VAULT_PATH" --dry-run  # 预览
    python upgrade_links.py --vault "YOUR_VAULT_PATH" --category concepts  # 只处理 concepts
"""

import argparse
import os
import re
import sys
from pathlib import Path


# ── 核心逻辑 ────────────────────────────────────────────────────────────────

def find_target_file(vault_root: str, ref_name: str) -> str | None:
    """
    在 vault 中查找 ref_name 对应的 .md 文件。
    支持：精确匹配 > 子串包含匹配（不区分大小写）。
    返回找到的相对路径，不存在返回 None。
    """
    # 精确匹配（文件名完全一致，不区分大小写）
    for root, dirs, files in os.walk(vault_root):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if fname.lower() == f"{ref_name}.md".lower():
                rel = os.path.relpath(os.path.join(root, fname), vault_root)
                return rel

    # 子串包含匹配（关联连接文本包含文件名）
    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if fname.lower().startswith(ref_name.lower()) and fname.endswith(".md"):
                rel = os.path.relpath(os.path.join(root, fname), vault_root)
                return rel

    return None


def parse_ref_line(line: str) -> str | None:
    """
    从关联连接行提取引用名称。
    支持格式：
      > - 引用名称
      > 引用名称（有时没有短横线）
      引用名称
    返回引用名称（去掉首尾空白），无法提取返回 None。
    """
    # 去掉前导 > 和空白
    line = re.sub(r"^>\s*", "", line).strip()
    # 去掉列表标记 - * 或数字编号
    line = re.sub(r"^[-*·]\s+", "", line).strip()
    line = re.sub(r"^\d+[.、)\s]+", "", line).strip()
    # 去掉后面的描述文字（— 后的内容）
    name = re.sub(r"\s*[—–—]\s*.*$", "", line).strip()
    return name if name else None


def upgrade_file(vault_root: str, fpath: str, dry_run: bool = False, verbose: bool = False) -> dict:
    """
    检查并升级单个 wiki 页面的「关联连接」。

    返回:
        {
            "upgraded": int,      # 升级数量
            "unchanged": int,     # 保持不变数量
            "skipped": int,       # 跳过（无关联连接节）
            "details": [(行文本, 引用名, 目标文件路径或None), ...]
        }
    """
    result = {"upgraded": 0, "unchanged": 0, "skipped": 0, "details": []}

    with open(fpath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 查找"关联连接"节
    section_start = -1
    for i, line in enumerate(lines):
        if re.search(r"^##\s*关联连接", line):
            section_start = i
            break

    if section_start < 0:
        result["skipped"] = 1
        return result

    # 找到节结束位置（下一个 ## 标题或文件末尾）
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        if re.match(r"^#{1,6}\s+", lines[i]) and not lines[i].startswith(">"):
            section_end = i
            break

    section_lines = lines[section_start:section_end]

    new_section_lines = []
    for line in section_lines:
        stripped = line.strip()

        # 只处理 blockquote 格式的引用行
        if not stripped.startswith(">"):
            new_section_lines.append(line)
            continue

        ref_name = parse_ref_line(stripped)
        if not ref_name:
            new_section_lines.append(line)
            continue

        target = find_target_file(vault_root, ref_name)

        if target:
            # 目标存在，升级为 wiki-link
            # 保留 blockquote 格式，只替换内容
            # 原文：> - 引用名称（— 可选描述）
            # 新文：> - [[引用名称]]
            new_line = re.sub(
                r"(\]\()", ""
            )  # 先不做复杂处理，直接替换
            # 简单替换：去掉描述，只保留 [[wiki-link]]
            clean_name = re.sub(r"\s*[—–—]\s*.*$", "", ref_name).strip()
            new_line = f"> - [[{clean_name}]]\n"
            new_section_lines.append(new_line)
            result["upgraded"] += 1
            result["details"].append((stripped, ref_name, target))
            if verbose:
                print(f"  🔗 升级: {ref_name} → {target}")
        else:
            # 目标不存在，保持 blockquote 纯文本
            new_section_lines.append(line)
            result["unchanged"] += 1
            result["details"].append((stripped, ref_name, None))

    if result["upgraded"] == 0:
        # 无变更，不写入
        return result

    if dry_run:
        return result

    # 写回文件
    new_lines = lines[:section_start] + new_section_lines + lines[section_end:]
    with open(fpath, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return result


def scan_vault(vault_root: str, category: str | None = None, dry_run: bool = False, verbose: bool = False) -> dict:
    """
    扫描 vault 中指定 category 的 wiki 页面，执行升级。
    
    Args:
        vault_root: Vault 根目录
        category: None=所有, 或 concepts/entities/sources/syntheses
        dry_run: True=只预览不写入
        verbose: True=打印详细信息
    
    Returns:
        {"files_scanned": int, "files_upgraded": int, "total_upgraded": int, ...}
    """
    wiki_dir = os.path.join(vault_root, "wiki")
    if not os.path.isdir(wiki_dir):
        print(f"[ERROR] wiki 目录不存在: {wiki_dir}")
        return {}

    categories = [category] if category else ["concepts", "entities", "sources", "syntheses", "logs"]

    total_scanned = 0
    total_upgraded = 0
    total_unchanged = 0
    total_skipped = 0
    all_details = []

    for cat in categories:
        cat_dir = os.path.join(wiki_dir, cat)
        if not os.path.isdir(cat_dir):
            continue

        for fname in os.listdir(cat_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(cat_dir, fname)
            total_scanned += 1

            res = upgrade_file(vault_root, fpath, dry_run=dry_run, verbose=verbose)
            total_upgraded += 1 if res["upgraded"] > 0 else 0
            total_unchanged += res["unchanged"]
            total_skipped += res["skipped"]

            if res["upgraded"] > 0 and not verbose:
                # 非 verbose 模式下汇总打印
                for detail in res["details"]:
                    if detail[2]:  # 有目标文件
                        print(f"  🔗 {detail[1]} → {detail[2]}")

            if res["details"]:
                all_details.extend(res["details"])

    return {
        "files_scanned": total_scanned,
        "files_upgraded": total_upgraded,
        "total_upgraded": total_unchanged,  # unchanged = 目标不存在，保持纯文本
        "total_skipped": total_skipped,
        "all_upgraded": sum(1 for d in all_details if d[2]),
        "all_unchanged": sum(1 for d in all_details if not d[2]),
        "details": all_details,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="关联连接自动升级工具：将纯文本引用升级为 [[wiki-link]]（目标文件存在时）"
    )
    parser.add_argument("--vault", "-v", required=True, help="Vault 根目录")
    parser.add_argument("--dry-run", "-n", action="store_true", help="预览模式，不写入文件")
    parser.add_argument("--verbose", action="store_true", help="打印每个引用的处理详情")
    parser.add_argument(
        "--category", "-c",
        choices=["concepts", "entities", "sources", "syntheses", "logs"],
        help="只处理指定分类（默认全部）"
    )

    args = parser.parse_args()
    vault = os.path.abspath(args.vault)

    if not os.path.isdir(vault):
        print(f"[ERROR] Vault 目录不存在: {vault}")
        sys.exit(1)

    print(f"🔍 Vault: {vault}")
    print(f"📂 分类: {args.category or '全部'}")
    if args.dry_run:
        print(f"⚠️  模式: 预览（dry-run，不写入）")
    print()

    result = scan_vault(
        vault,
        category=args.category,
        dry_run=args.dry_run,
        verbose=args.verbose
    )

    if not result:
        print("未扫描到任何文件。")
        return

    print()
    print(f"📊 扫描结果:")
    print(f"  文件扫描:   {result['files_scanned']}")
    print(f"  有变更:     {result['files_upgraded']}")
    print(f"  🔗 已升级为 wiki-link: {result['all_upgraded']}")
    print(f"  📝 保持纯文本（目标不存在）: {result['all_unchanged']}")
    print(f"  ⏭  无关联连接节: {result['total_skipped']}")

    if args.dry_run:
        print()
        print("💡 这是预览模式，未写入任何文件。")
        print("   确认无误后，去掉 --dry-run 参数正式执行。")
    elif result["files_upgraded"] > 0:
        print()
        print(f"✅ 升级完成，{result['files_upgraded']} 个文件已更新。")
    else:
        print()
        print("✅ 扫描完成，所有关联连接引用目标均不存在，无需升级。")


if __name__ == "__main__":
    main()

