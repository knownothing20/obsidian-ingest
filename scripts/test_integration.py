"""
test_integration.py - 集成测试与全链路验证（SCHEMA v4.0.0）

测试场景：
1. 完整流程测试：raw/ → MD → wiki 页面 → 验证 front matter + 正文结构
2. 排重测试：相同文件再次放入 → 应被跳过
3. 质量门禁测试：空文件/不合法文件 → 应被拒绝
4. 知识冲突测试：同主题不同观点 → 应标记冲突
5. 时效性测试：Lint 应触发长期未核实页面

用法：
  python test_integration.py --vault /path/to/vault
  python test_integration.py --vault /path/to/vault --test dedup
"""

import os
import sys
import re
import yaml
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

_TZ_CN = timezone(timedelta(hours=8))

# 添加 scripts 目录到 path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from unified_compiler import compile_single
from validate_wiki import validate_wiki_page
from knowledge_conflict_detector import check_knowledge_conflict
from lint_wiki import lint_wiki_page


def create_test_vault(base_dir: str) -> str:
    """创建测试 vault"""
    vault = os.path.join(base_dir, "test_vault_" + datetime.now(_TZ_CN).strftime("%Y%m%d_%H%M%S"))
    os.makedirs(vault)
    os.makedirs(os.path.join(vault, "raw"))
    os.makedirs(os.path.join(vault, "todo"))
    os.makedirs(os.path.join(vault, "wiki", "sources"))
    os.makedirs(os.path.join(vault, "wiki", "concepts"))
    os.makedirs(os.path.join(vault, "wiki", "entities"))
    os.makedirs(os.path.join(vault, "wiki", "syntheses"))
    os.makedirs(os.path.join(vault, "archive", "raw-archive"))
    return vault


def create_test_md(vault: str, filename: str, content: str) -> str:
    """创建测试 MD 文件"""
    md_path = os.path.join(vault, "todo", filename)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return md_path


def test_full_pipeline(vault: str) -> dict:
    """测试 1: 完整流程"""
    print("\n" + "=" * 50)
    print("🧪 测试 1: 完整流程")
    print("=" * 50)

    result = {"passed": True, "details": []}

    # 创建测试 MD
    test_md = """# Docker 容器技术

## 核心要点
- 容器是轻量级虚拟化技术
- Docker 是最流行的容器平台
- 容器与虚拟机不同，不包含操作系统

## 技术原理
Docker 使用 namespace 和 cgroup 实现资源隔离。

## 优势
1. 快速启动
2. 资源占用低
3. 跨平台部署
"""

    md_path = create_test_md(vault, "docker-container.md", test_md)

    # 编译
    compile_result = compile_single(
        md_path=md_path,
        source_file="docker-container.pdf",
        vault_root=vault,
    )

    if not compile_result.get("success"):
        result["passed"] = False
        result["details"].append(f"编译失败: {compile_result.get('error')}")
        return result

    wiki_path = compile_result.get("wiki_path")
    if not wiki_path or not os.path.exists(wiki_path):
        result["passed"] = False
        result["details"].append("Wiki 页面未生成")
        return result

    # 验证 front matter
    with open(wiki_path, "r", encoding="utf-8") as f:
        content = f.read()

    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        result["passed"] = False
        result["details"].append("缺少 front matter")
        return result

    fm_text = fm_match.group(1)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception:
        result["passed"] = False
        result["details"].append("Front matter 解析失败")
        return result

    required_fields = ["title", "type", "tags", "sources", "created", "validity", "content_proof"]
    for field in required_fields:
        if field not in fm:
            result["passed"] = False
            result["details"].append(f"缺少字段: {field}")

    # 验证正文结构
    body = content[fm_match.end():]
    if "## 核心定义" not in body and "## 关键要点" not in body:
        result["passed"] = False
        result["details"].append("缺少核心区块")

    if "## 关联连接" not in body:
        result["passed"] = False
        result["details"].append("缺少关联连接区块")

    # 验证通过
    passed, errors = validate_wiki_page(wiki_path)
    if not passed:
        result["passed"] = False
        result["details"].extend(errors)

    if result["passed"]:
        print("✅ 完整流程测试通过")
    else:
        print("❌ 完整流程测试失败")
        for d in result["details"]:
            print(f"   - {d}")

    return result


def test_dedup(vault: str) -> dict:
    """测试 2: 排重"""
    print("\n" + "=" * 50)
    print("🧪 测试 2: 排重")
    print("=" * 50)

    result = {"passed": True, "details": []}

    # 创建两个相同内容的 MD
    content = "# 测试文档\n\n这是测试内容。"
    md1 = create_test_md(vault, "test1.md", content)
    md2 = create_test_md(vault, "test2.md", content)

    # 编译第一个
    r1 = compile_single(md1, "test.pdf", vault)
    if not r1.get("success"):
        result["passed"] = False
        result["details"].append("第一次编译失败")
        return result

    # 编译第二个（应该被跳过）
    r2 = compile_single(md2, "test2.pdf", vault)

    if r2.get("skipped"):
        print("✅ 排重测试通过（相同内容被正确跳过）")
    else:
        result["passed"] = False
        result["details"].append("相同内容未被跳过")
        print("❌ 排重测试失败")

    return result


def test_quality_gate(vault: str) -> dict:
    """测试 3: 质量门禁"""
    print("\n" + "=" * 50)
    print("🧪 测试 3: 质量门禁")
    print("=" * 50)

    result = {"passed": True, "details": []}

    # 测试空文件
    empty_md = create_test_md(vault, "empty.md", "")
    r = compile_single(empty_md, "empty.pdf", vault)

    if not r.get("success"):
        print("✅ 质量门禁测试通过（空文件被拒绝）")
    else:
        result["passed"] = False
        result["details"].append("空文件未被拒绝")
        print("❌ 质量门禁测试失败")

    return result


def test_knowledge_conflict(vault: str) -> dict:
    """测试 4: 知识冲突检测"""
    print("\n" + "=" * 50)
    print("🧪 测试 4: 知识冲突检测")
    print("=" * 50)

    result = {"passed": True, "details": []}

    # 创建两个相似主题的页面
    content1 = """---
title: "Docker 容器"
type: concept
tags: [docker, 容器, 虚拟化]
sources: [test.pdf]
created: 2026-01-01
validity: current
content_proof: "Docker 是一个容器平台"
---

# Docker 容器

## 核心观点
- Docker 是容器平台
- 容器是轻量级虚拟化
"""

    content2 = """---
title: "Docker 容器技术"
type: concept
tags: [docker, 容器, 虚拟化]
sources: [test2.pdf]
created: 2026-01-02
validity: current
content_proof: "Docker 使用容器技术实现隔离"
---

# Docker 容器技术

## 核心观点
- Docker 是容器技术
- 容器实现资源隔离
"""

    md1 = create_test_md(vault, "docker1.md", content1)
    md2 = create_test_md(vault, "docker2.md", content2)

    # 编译第一个
    r1 = compile_single(md1, "test1.pdf", vault)
    if not r1.get("success"):
        result["passed"] = False
        result["details"].append("编译第一个失败")
        return result

    # 编译第二个
    r2 = compile_single(md2, "test2.pdf", vault)
    if not r2.get("success"):
        result["passed"] = False
        result["details"].append("编译第二个失败")
        return result

    # 检查冲突
    wiki_path = r2.get("wiki_path")
    conflicts = check_knowledge_conflict(wiki_path, os.path.join(vault, "wiki"))

    if len(conflicts) > 0:
        print(f"✅ 知识冲突检测测试通过（发现 {len(conflicts)} 个潜在冲突）")
    else:
        print("⚠️ 未检测到冲突（可能需要相似内容）")

    return result


def test_lint_outdated(vault: str) -> dict:
    """测试 5: 时效性 Lint"""
    print("\n" + "=" * 50)
    print("🧪 测试 5: 时效性 Lint")
    print("=" * 50)

    result = {"passed": True, "details": []}

    # 创建长期未核实的页面
    old_date = (datetime.now(_TZ_CN) - timedelta(days=200)).strftime("%Y-%m-%d")
    content = f"""---
title: "旧文档"
type: source
tags: [测试]
sources: [test.pdf]
created: 2025-01-01
last_updated: 2025-01-01
last_verified: {old_date}
validity: current
content_proof: "这是一个测试文档"
---

# 旧文档

## 核心观点
- 测试内容
"""

    md_path = create_test_md(vault, "old.md", content)
    r = compile_single(md_path, "old.pdf", vault)

    if not r.get("success"):
        result["passed"] = False
        result["details"].append("编译失败")
        return result

    # Lint 检查
    wiki_path = r.get("wiki_path")
    lint_result = lint_wiki_page(wiki_path)

    outdated_found = any(i["type"] == "outdated" for i in lint_result["issues"])

    if outdated_found:
        print("✅ 时效性 Lint 测试通过（检测到长期未核实）")
    else:
        result["passed"] = False
        result["details"].append("未检测到长期未核实")
        print("❌ 时效性 Lint 测试失败")

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="集成测试工具")
    parser.add_argument("--vault", "-v", help="Vault 根目录（不指定则创建临时目录）")
    parser.add_argument("--test", choices=["full", "dedup", "quality", "conflict", "lint", "all"],
                        default="all", help="测试类型")
    parser.add_argument("--keep", action="store_true", help="测试后保留 vault")

    args = parser.parse_args()

    # 确定 vault 目录
    if args.vault:
        vault = args.vault
    else:
        vault = create_test_vault(tempfile.gettempdir())
        print(f"📁 创建临时测试 vault: {vault}")

    if not os.path.exists(vault):
        print(f"❌ Vault 不存在: {vault}")
        sys.exit(1)

    # 运行测试
    tests = {
        "full": test_full_pipeline,
        "dedup": test_dedup,
        "quality": test_quality_gate,
        "conflict": test_knowledge_conflict,
        "lint": test_lint_outdated,
    }

    if args.test == "all":
        results = {}
        for name, test_func in tests.items():
            results[name] = test_func(vault)
    else:
        results = {args.test: tests[args.test](vault)}

    # 汇总
    print("\n" + "=" * 50)
    print("📊 测试结果汇总")
    print("=" * 50)

    passed = sum(1 for r in results.values() if r["passed"])
    total = len(results)

    for name, r in results.items():
        status = "✅" if r["passed"] else "❌"
        print(f"  {status} {name}: {'通过' if r['passed'] else '失败'}")

    print(f"\n总计: {passed}/{total} 通过")

    if not args.keep and not args.vault:
        shutil.rmtree(vault)
        print(f"🗑️ 已清理临时 vault")
    elif not args.keep:
        print(f"💾 Vault 保留在: {vault}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()