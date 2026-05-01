"""
validate_wiki.py - wiki 页面质量验证

验证生成的 wiki 页面是否符合质量标准：
- 必须有 content_proof（原文引用）
- content_proof 长度 >= 20 字
- 必须有 front matter
- type 必须是有效值
"""

import os
import sys
import re
from pathlib import Path
from typing import Tuple, List


# 有效的 type 值
VALID_TYPES = {"source", "entity", "concept", "log", "synthesis"}
# 有效的 validity 值
VALID_VALIDITY = {"historical", "valid", "outdated", "current"}


def validate_wiki_page(file_path: str, min_proof_length: int = 20) -> Tuple[bool, List[str]]:
    """
    验证单个 wiki 页面
    
    Args:
        file_path: wiki 页面路径
        min_proof_length: content_proof 最小长度
        
    Returns:
        (是否通过, 错误列表)
    """
    errors = []
    
    if not os.path.exists(file_path):
        return False, [f"文件不存在: {file_path}"]
    
    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    
    # 1. 检查 front matter
    if not content.startswith("---"):
        errors.append("缺少 front matter（需要以 --- 开头）")
        return False, errors
    
    # 提取 front matter
    try:
        second_dash = content.find("---", 3)
        if second_dash == -1:
            errors.append("front matter 不完整（缺少结束 ---）")
            return False, errors
        
        front_matter = content[3:second_dash].strip()
        fm_lines = front_matter.split("\n")
        
        # 解析 front matter
        fm = {}
        for line in fm_lines:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
        
    except Exception as e:
        errors.append(f"front matter 解析失败: {e}")
        return False, errors
    
    # 2. 检查 required fields
    required_fields = ["title", "type", "content_proof"]
    for field in required_fields:
        if field not in fm:
            errors.append(f"缺少必需字段: {field}")
    
    # 3. 验证 type
    if "type" in fm:
        wiki_type = fm["type"]
        if wiki_type not in VALID_TYPES:
            errors.append(f"type 无效: {wiki_type}（有效值: {', '.join(VALID_TYPES)}）")
    
    # 4. 验证 validity（如果存在）
    if "validity" in fm:
        validity = fm["validity"]
        if validity not in VALID_VALIDITY:
            errors.append(f"validity 无效: {validity}（有效值: {', '.join(VALID_VALIDITY)}）")
    
    # 5. 验证 content_proof
    if "content_proof" in fm:
        proof = fm["content_proof"]
        if not proof or proof.strip() == "":
            errors.append("content_proof 为空")
        elif len(proof) < min_proof_length:
            errors.append(f"content_proof 太短（{len(proof)} < {min_proof_length}字）")
    else:
        # content_proof 在 required_fields 中已检查
        pass
    
    # 6. 检查正文是否为空
    body = content[second_dash + 3:].strip()
    if not body or len(body.strip()) < 10:
        errors.append("正文内容为空或太短")
    
    return len(errors) == 0, errors


# 不需要 front matter 检查的目录（非 wiki 生成文件）
SKIP_FRONT_MATTER_DIRS = {"logs", "attachments", "templates"}
# 不需要验证的根目录文件
SKIP_ROOT_FILES = {"index.md"}

def validate_wiki_dir(wiki_dir: str, min_proof_length: int = 20) -> dict:
    """
    验证 wiki 目录下的所有页面
    
    Returns:
        {
            "total": 数量,
            "passed": 通过数,
            "failed": 失败数,
            "errors": {文件: [错误列表]}
        }
    """
    result = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "errors": {}
    }
    
    if not os.path.isdir(wiki_dir):
        print(f"[ERROR] 目录不存在: {wiki_dir}")
        return result
    
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]  # 跳过隐藏目录
        
        for fname in files:
            if not fname.endswith(".md"):
                continue
            
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, wiki_dir)
            
            # 跳过 logs/ 等非 wiki 目录
            top_dir = rel_path.split(os.sep)[0] if os.sep in rel_path else ""
            if top_dir in SKIP_FRONT_MATTER_DIRS:
                continue
            
            # 跳过根目录的非 wiki 文件（如 index.md）
            if os.sep not in rel_path and rel_path in SKIP_ROOT_FILES:
                continue
            
            result["total"] += 1
            
            passed, errors = validate_wiki_page(fpath, min_proof_length)
            
            if passed:
                result["passed"] += 1
            else:
                result["failed"] += 1
                result["errors"][rel_path] = errors
    
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="验证 wiki 页面质量")
    parser.add_argument("path", nargs="?", help="文件或目录路径")
    parser.add_argument("--min-proof-length", type=int, default=20, help="content_proof 最小长度")
    parser.add_argument("--vault", "-v", help="Vault 根目录（自动定位 wiki/）")
    
    args = parser.parse_args()
    
    path = args.path
    
    # 如果没有指定 path，尝试从 vault 推导
    if not path and args.vault:
        path = os.path.join(args.vault, "wiki")
    
    if not path or not os.path.exists(path):
        print(f"[ERROR] 路径不存在: {path}")
        parser.print_help()
        return
    
    if os.path.isfile(path):
        # 验证单个文件
        passed, errors = validate_wiki_page(path, args.min_proof_length)
        if passed:
            print(f"✅ {path}")
        else:
            print(f"❌ {path}")
            for err in errors:
                print(f"   - {err}")
        sys.exit(0 if passed else 1)
    
    else:
        # 验证目录
        result = validate_wiki_dir(path, args.min_proof_length)
        
        print(f"📊 验证结果")
        print(f"  总数: {result['total']}")
        print(f"  ✅ 通过: {result['passed']}")
        print(f"  ❌ 失败: {result['failed']}")
        
        if result["errors"]:
            print(f"\n失败详情：")
            for fpath, errors in result["errors"].items():
                print(f"  ❌ {fpath}")
                for err in errors[:3]:  # 只显示前3个错误
                    print(f"     - {err}")
        
        sys.exit(0 if result["failed"] == 0 else 1)


if __name__ == "__main__":
    main()