"""
pre_validate.py — 写文件前结构预校验

在 Agent 生成 wiki 页面后、写文件之前运行。
只做轻量级正则检查，不消耗 API token。
不通过 → 返回具体错误 → Agent 必须修复后才能写文件。

用法：
  python pre_validate.py <page_content_file> [--config path/to/config.yaml]

  或者通过 stdin 传入：
  type page.md | python pre_validate.py --stdin [--config ...]

返回 JSON（exit 0=通过，1=失败）：
  {"pass": true/false, "errors": [...], "warnings": [...]}
"""

import json
import os
import re
import sys
import argparse

# ── 占位文本黑名单 ──
PLACEHOLDER_WORDS = ["待补充", "详见原文", "待完善", "TBD", "TODO", "略", "无"]


def load_config(config_path):
    """加载 quality_gates 配置"""
    try:
        import yaml
    except ImportError:
        return {}

    if not config_path or not os.path.exists(config_path):
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config.get("quality_gates", {})


def check_content_proof(text, min_length=20):
    """检查 content_proof 字段存在且有效"""
    match = re.search(r'content_proof:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if not match:
        return False, "content_proof 字段不存在"

    proof = match.group(1).strip()
    if not proof or proof in PLACEHOLDER_WORDS:
        return False, f"content_proof 为空或占位：{proof}"

    if len(proof) < min_length:
        return False, f"content_proof 长度不足：{len(proof)}字（要求≥{min_length}字）"

    return True, None


def check_core_points(text, min_points=3):
    """检查核心观点区块"""
    section_match = re.search(r'##\s*核心观点\s*\n(.*?)(?=\n##|\Z)', text, re.DOTALL)
    if not section_match:
        return False, "缺少「核心观点」区块"

    section = section_match.group(1)
    points = re.findall(r'^\d+\.\s+\S', section, re.MULTILINE)

    if len(points) < min_points:
        return False, f"核心观点不足：{len(points)}条（要求≥{min_points}条）"

    for p in PLACEHOLDER_WORDS:
        if p in section:
            return False, f"核心观点包含占位文本：{p}"

    return True, None


def check_bilinks(text, min_links=1):
    """检查双链数量"""
    links = re.findall(r'\[\[.+?\]\]', text)
    if min_links > 0 and len(links) < min_links:
        return False, f"双链不足：{len(links)}个（要求≥{min_links}个）"
    return True, None


def check_front_matter(text):
    """检查 front matter 完整性"""
    fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not fm_match:
        return False, ["缺少 front matter（以 --- 开头和结束）"]

    fm = fm_match.group(1)
    missing = []

    required_fields = ["title:", "type:", "sources:", "created:", "validity:"]
    for field in required_fields:
        if field not in fm:
            missing.append(f"缺少字段：{field.rstrip(':')}")

    if "tags:" not in fm:
        missing.append("缺少字段：tags")

    # 检查 type 值
    type_match = re.search(r'type:\s*(\S+)', fm)
    if type_match:
        type_val = type_match.group(1)
        valid_types = {"source", "entity", "concept", "synthesis"}
        if type_val not in valid_types:
            missing.append(f"type 值无效：{type_val}（应为 source/entity/concept/synthesis）")

    if missing:
        return False, missing

    return True, None


def main():
    parser = argparse.ArgumentParser(
        description="Wiki 页面结构预校验 — 写文件前运行"
    )
    parser.add_argument("file", nargs="?", help="页面内容文件路径")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取")
    parser.add_argument("--config", "-c", help="配置文件路径")
    parser.add_argument("--min-proof-length", type=int, default=20, help="content_proof 最小字数")
    parser.add_argument("--min-core-points", type=int, default=3, help="最少核心观点数")
    parser.add_argument("--min-bilinks", type=int, default=1, help="最少双链数（0=不检查）")

    args = parser.parse_args()

    # 加载配置
    gates = load_config(args.config)
    min_proof = gates.get("min_proof_length", args.min_proof_length)
    min_points = gates.get("require_core_points", args.min_core_points)
    min_links = gates.get("require_bilinks", args.min_bilinks)

    # 读取内容
    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        if not os.path.exists(args.file):
            result = {"pass": False, "errors": [f"文件不存在：{args.file}"]}
            json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
            print()
            sys.exit(1)
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        result = {"pass": False, "errors": ["未提供输入（需要文件路径或 --stdin）"]}
        json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
        print()
        sys.exit(1)

    errors = []
    warnings = []

    # ── Front matter 检查（阻断性） ──
    fm_ok, fm_result = check_front_matter(text)
    if not fm_ok:
        errors.extend(fm_result)

    # ── Content proof 检查（阻断性） ──
    cp_ok, cp_msg = check_content_proof(text, min_proof)
    if not cp_ok:
        errors.append(cp_msg)

    # ── 核心观点检查（阻断性） ──
    cps_ok, cps_msg = check_core_points(text, min_points)
    if not cps_ok:
        errors.append(cps_msg)

    # ── 双链检查（警告，不阻断） ──
    if min_links > 0:
        bl_ok, bl_msg = check_bilinks(text, min_links)
        if not bl_ok:
            warnings.append(bl_msg)

    result = {
        "pass": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }

    json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
    print()
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
