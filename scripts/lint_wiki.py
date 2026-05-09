"""
lint_wiki.py - 时效性管理 / Lint 系统（SCHEMA v4.0.0）

SCHEMA.md 第 8.2 节要求的四种检查：
1. 显式过期检查: expires < today → "建议复核"
2. 含配置页面检测: 含 IP/端口/版本号 → "⚠️ 含具体配置，建议核实"
3. validity:stale 列表: 列出所有标记为 stale 的页面
4. 长期未核实: last_verified > 180天 → "长期未核实"

不做的事（SCHEMA.md 明确禁止）：
- ❌ 不自动标 stale
- ❌ 不自动删除或覆盖内容
- ❌ 不基于时间自动判断知识过期

用法：
  python lint_wiki.py --vault /path/to/vault [--fix]
  python lint_wiki.py --vault /path/to/vault --check expired
  python lint_wiki.py --vault /path/to/vault --check config
"""

import os
import re
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple

_TZ_CN = timezone(timedelta(hours=8))


def _parse_date(date_str: str) -> datetime:
    """解析 YYYY-MM-DD 格式日期"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except Exception:
        return None


def _today() -> datetime:
    return datetime.now(_TZ_CN).date()


# 跳过检查的目录
SKIP_DIRS = {"logs", "attachments", "templates", "_assets"}


def lint_wiki_page(file_path: str) -> Dict:
    """
    对单个 wiki 页面进行 Lint 检查

    Returns:
        {
            "path": str,
            "title": str,
            "issues": [
                {"type": str, "severity": "warning"|"error", "message": str}
            ]
        }
    """
    result = {
        "path": file_path,
        "title": "",
        "issues": [],
    }

    if not os.path.exists(file_path):
        return result

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return result

    # 提取 front matter
    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        return result

    fm_text = fm_match.group(1)

    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception:
        return result

    # 提取 title
    result["title"] = fm.get("title", Path(file_path).stem)

    # 检查 1: 显式过期
    expires = fm.get("expires", "")
    if expires:
        expires_date = _parse_date(expires)
        if expires_date and expires_date.date() < _today():
            result["issues"].append({
                "type": "expired",
                "severity": "warning",
                "message": f"已过期（expires: {expires}），建议复核"
            })

    # 检查 2: 含配置信息
    # 匹配 IP 地址、端口号、版本号等
    body = content[fm_match.end():]
    config_patterns = [
        (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', "IP 地址"),
        (r':\d{2,5}\b', "端口号"),
        (r'\bv?\d+\.\d+\.\d+([.-]\w+)?\b', "版本号（如 v1.2.3）"),
        (r'(password|passwd|pwd)\s*[:=]\s*\S+', "密码配置"),
        (r'(api_key|apikey|secret)\s*[:=]\s*\S+', "密钥配置"),
    ]

    for pattern, desc in config_patterns:
        if re.search(pattern, body, re.IGNORECASE):
            result["issues"].append({
                "type": "config",
                "severity": "warning",
                "message": f"含具体配置（{desc}），建议核实时效性"
            })
            break

    # 检查 3: validity:stale
    validity = fm.get("validity", "")
    if validity == "stale":
        result["issues"].append({
            "type": "stale",
            "severity": "info",
            "message": "已标记为 stale，需人工复核"
        })

    # 检查 4: 长期未核实
    last_verified = fm.get("last_verified", "")
    if last_verified:
        verified_date = _parse_date(last_verified)
        if verified_date:
            days_since = (_today() - verified_date.date()).days
            if days_since > 180:
                result["issues"].append({
                    "type": "outdated",
                    "severity": "warning",
                    "message": f"长期未核实（{days_since}天），建议确认信息时效性"
                })

    return result


def lint_wiki_dir(wiki_dir: str, check_type: str = None) -> Dict:
    """
    对 wiki 目录进行 Lint 检查

    Args:
        wiki_dir: wiki 根目录
        check_type: 可选过滤 "expired" | "config" | "stale" | "outdated"

    Returns:
        {
            "total": int,
            "total_issues": int,
            "results": [lint_wiki_page()结果列表]
        }
    """
    result = {
        "total": 0,
        "total_issues": 0,
        "by_type": {
            "expired": [],
            "config": [],
            "stale": [],
            "outdated": [],
        },
        "results": [],
    }

    if not os.path.isdir(wiki_dir):
        print(f"[ERROR] 目录不存在: {wiki_dir}")
        return result

    for root, dirs, files in os.walk(wiki_dir):
        # 跳过特定目录
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for fname in files:
            if not fname.endswith(".md"):
                continue

            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, wiki_dir)

            result["total"] += 1

            page_result = lint_wiki_page(fpath)

            # 过滤
            if check_type:
                page_result["issues"] = [
                    i for i in page_result["issues"] if i["type"] == check_type
                ]

            if page_result["issues"]:
                result["total_issues"] += len(page_result["issues"])
                result["results"].append(page_result)

                # 分类统计
                for issue in page_result["issues"]:
                    issue_type = issue["type"]
                    if issue_type in result["by_type"]:
                        result["by_type"][issue_type].append({
                            "path": rel_path,
                            "title": page_result["title"],
                            "message": issue["message"],
                        })

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Wiki 页面时效性 Lint 工具")
    parser.add_argument("--vault", "-v", required=True, help="Vault 根目录")
    parser.add_argument("--check", choices=["expired", "config", "stale", "outdated"],
                        help="只检查特定类型")
    parser.add_argument("--fix", action="store_true",
                        help="自动修复（仅添加标记，不修改内容）")

    args = parser.parse_args()

    wiki_dir = os.path.join(args.vault, "wiki")

    if not os.path.isdir(wiki_dir):
        print(f"❌ wiki 目录不存在: {wiki_dir}")
        sys.exit(1)

    print(f"🔍 Lint 检查: {wiki_dir}")
    print("=" * 50)

    lint_result = lint_wiki_dir(wiki_dir, args.check)

    print(f"\n📊 检查结果")
    print(f"  总页面数: {lint_result['total']}")
    print(f"  有问题页面: {len(lint_result['results'])}")
    print(f"  问题总数: {lint_result['total_issues']}")

    if args.check:
        # 只显示指定类型
        issues = lint_result["by_type"].get(args.check, [])
        if issues:
            print(f"\n⚠️ {args.check} 问题 ({len(issues)} 个):")
            for item in issues[:20]:
                print(f"  - {item['title']}")
                print(f"    {item['message']}")
                print(f"    {item['path']}")
    else:
        # 显示各类问题
        type_labels = {
            "expired": "已过期",
            "config": "含配置信息",
            "stale": "已标记 stale",
            "outdated": "长期未核实",
        }

        for itype, label in type_labels.items():
            items = lint_result["by_type"].get(itype, [])
            if items:
                print(f"\n⚠️ {label} ({len(items)} 个):")
                for item in items[:10]:
                    print(f"  - {item['title']}: {item['message']}")
                if len(items) > 10:
                    print(f"  ... 还有 {len(items) - 10} 个")

    # 统计信息
    print("\n" + "=" * 50)
    if lint_result["total_issues"] > 0:
        print(f"⚠️ 共 {lint_result['total_issues']} 个问题待处理")
        print("\n建议操作:")
        print("  1. 逐一检查问题页面")
        print("  2. 确认信息时效性，更新 validity 状态")
        print("  3. 如需更新 last_verified，运行: python cli.py process")
    else:
        print("✅ 所有页面状态正常")

    sys.exit(0 if lint_result["total_issues"] == 0 else 1)


if __name__ == "__main__":
    main()