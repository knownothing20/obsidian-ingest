"""
unified_compiler.py - 统一编译引擎 (SCHEMA v4.0.0)

合并 compiler.py + wiki_generator.py，生成符合 SCHEMA.md 标准的 wiki 页面：
  Step 1: 格式清洗（纯规则）
  Step 2: 内容分析 + 页面类型自动分类
  Step 3: 提取 content_proof
  Step 4: Front Matter 注入（SCHEMA 完整字段）
  Step 5: 结构化 wiki 正文生成
  Step 6: 排重检测
  Step 7: 质量门禁（Phase 2.1 新增）
  Step 8: 写入 wiki 页面（按类型路由到不同目录）
  Step 9: Index 更新
  Step 10: 操作日志（Phase 2.2）
  Step 11: raw 文件 processed 标记（Phase 2.3）
"""

import os
import re
import sys
import hashlib
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

# ══════════════════════════════════════════════════════════════════════════════
# DEBUG 日志 - 记录所有文件操作，防止"耍赖"
# ══════════════════════════════════════════════════════════════════════════════
_DEBUG_LOG_FILE = r"debug.log"  # 调试日志文件

def _debug_log(operation: str, file_path: str, details: str = ""):
    """记录文件操作日志"""
    if _DEBUG_LOG_FILE:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {operation}: {file_path}"
            if details:
                log_entry += f" | {details}"
            with open(_DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_entry + "\n")
        except Exception:
            pass  # 日志失败不中断主流程


# Phase 2.2: 操作日志
try:
    from operation_logger import log_operation
except ImportError:
    log_operation = None

# 当前编译的 wiki 目录（由 compile_single 设置，供 generate_structured_body 引用）
_CURRENT_WIKI_DIR = ""

_TZ_CN = timezone(timedelta(hours=8))


def _now_date() -> str:
    return datetime.now(_TZ_CN).strftime("%Y-%m-%d")


def _now_iso() -> str:
    datetime.now(_TZ_CN).isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════════════
# 页面类型自动分类（SCHEMA Step 5 要求）
# ══════════════════════════════════════════════════════════════════════════════

PAGE_TYPE_RULES = {
    "concept": {
        "keywords": ["概念", "方法论", "框架", "原理", "理论", "模型", "范式", "方法", "体系", "系统", "思想", "哲学", "规律", "法则", "定律", "定义", "本质", "内涵", "定义", "定义:", "是什么", "指的是"],
        "path_prefixes": ["concepts/", "概念/", "方法/", "理论/", "框架/"],
    },
    "entity": {
        "keywords": ["人物", "公司", "产品", "工具", "项目", "组织", "机构", "品牌", "人物:", "创始人", "CEO", "成立于", "总部在", "位于"],
        "path_prefixes": ["entities/", "人物/", "公司/", "产品/", "工具/", "项目/"],
    },
    "synthesis": {
        "keywords": ["综合", "总结", "综述", "概述", "汇总", "合集", "整理", "分析报告", "研究", "深度", "解读", "评论", "观点"],
        "path_prefixes": ["syntheses/", "综合/", "总结/", "综述/", "分析/"],
    },
    "source": {
        "keywords": [],  # 默认类型，兜底
        "path_prefixes": ["sources/", "来源/", "raw/", "todo/"],
    },
}


def auto_classify_page_type(content: str, rel_path: str) -> str:
    """
    根据内容特征和路径自动判断页面类型

    Returns:
        "concept" | "entity" | "synthesis" | "source"
    """
    rel_path = rel_path.replace("\\", "/").lower()
    content_lower = content.lower()

    # 优先检查路径前缀
    for ptype, rule in PAGE_TYPE_RULES.items():
        if ptype == "source":
            continue
        for prefix in rule["path_prefixes"]:
            if prefix.lower() in rel_path:
                return ptype

    # 检查内容关键词
    scores = {ptype: 0 for ptype in PAGE_TYPE_RULES}

    for ptype, rule in PAGE_TYPE_RULES.items():
        if ptype == "source":
            continue
        for kw in rule["keywords"]:
            if kw.lower() in content_lower:
                scores[ptype] += 1

    # 返回得分最高的类型（至少 1 分）
    max_score = max(scores.values())
    if max_score > 0:
        for ptype, score in scores.items():
            if score == max_score:
                return ptype

    return "source"  # 默认


def get_wiki_subdir(page_type: str) -> str:
    """根据页面类型获取 wiki 子目录"""
    type_to_dir = {
        "concept": "concepts",
        "entity": "entities",
        "source": "sources",
        "synthesis": "syntheses",
    }
    return type_to_dir.get(page_type, "sources")


# ══════════════════════════════════════════════════════════════════════════════
# Step 1: 格式清洗
# ══════════════════════════════════════════════════════════════════════════════

def clean_format(md_text: str) -> str:
    """清洗 Markdown，使 Obsidian 兼容"""
    lines = md_text.split("\n")
    cleaned = []
    prev_blank = False

    for line in lines:
        line = line.rstrip()
        if not line:
            if not prev_blank:
                cleaned.append("")
                prev_blank = True
            continue
        prev_blank = False

        # 图片路径标准化
        line = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _normalize_image_link, line)

        # LaTeX 公式标准化
        line = re.sub(r'\\\((.+?)\\\)', r'$\1$', line)
        line = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', line, flags=re.DOTALL)

        cleaned.append(line)

    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)

    if cleaned and cleaned[-1]:
        cleaned.append("")

    return "\n".join(cleaned)


def _normalize_image_link(match) -> str:
    alt = match.group(1)
    path = match.group(2)
    path = path.replace("\\", "/")
    path = re.sub(r'^\./+', '', path)
    return f"![{alt}]({path})"


# ══════════════════════════════════════════════════════════════════════════════
# Step 3: content_proof 提取（从 wiki_generator.py 移植）
# ══════════════════════════════════════════════════════════════════════════════

def _parse_front_matter_end(content: str) -> int:
    """解析 front matter 结束位置"""
    lines = content.strip().split("\n")
    if not lines or lines[0].strip() != "---":
        return -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return -1


def extract_content_proof(content: str, max_length: int = 500) -> str:
    """从 MD 内容提取 content_proof（原文引用）"""
    lines = content.strip().split("\n")
    front_matter_end = _parse_front_matter_end(content)
    if front_matter_end < 0:
        front_matter_end = 0

    body_lines = lines[front_matter_end + 1:]
    proof_lines = []
    in_code_block = False

    for line in body_lines:
        line = line.strip()

        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if line.startswith("![]") or line.startswith("[!"):
            continue
        if line.startswith("<!--") or line.startswith("-->"):
            continue
        if not line:
            # 空行不中断，继续收集内容（允许段落间隔）
            continue
        if re.match(r'^#{1,6}\s+', line):
            continue
        if re.match(r'^[\s]*[-*+]\s+', line) or re.match(r'^[\s]*\d+\.\s+', line):
            continue
        if re.match(r'^[\s]*\|', line):
            continue
        if re.match(r'^[\s]*[-*_]{3,}', line):
            continue

        proof_lines.append(line)
        if len(" ".join(proof_lines)) > max_length:
            break

    proof = " ".join(proof_lines)
    # 清除表格分隔符残留（| | | |）
    proof = re.sub(r'\|\s*\|', ' ', proof)  # 连续竖线 → 空格
    proof = re.sub(r'\s*\|\s*', ' ', proof)  # 单个竖线 → 空格
    # 清除链接、格式标记
    proof = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', proof)
    proof = re.sub(r'[*_`]+', '', proof)
    # 清除多余空白
    proof = re.sub(r'\s{2,}', ' ', proof)

    return proof.strip()


# ══════════════════════════════════════════════════════════════════════════════
# 标题、日期、Tags 提取
# ══════════════════════════════════════════════════════════════════════════════

# 垃圾标题检测模式 — 过滤无效的"标题"候选
_TITLE_GARBAGE_PATTERNS = [
    re.compile(r'^[\d\s\.\-\+#!@$%^&*()=+\[\]{}|;:,<>/?`~\\]+$'),  # 仅数字/符号
    re.compile(r'^[\d]{1,4}$'),  # 仅年份数字（如 "2021"）
    re.compile(r'^[\d\s]{1,10}$'),  # 仅数字+空格（如 "0 0"、"3 1 5"）
]

_TITLE_BLOCKLIST = {
    'untitled', '目录', 'contents', 'index',
    'undefined', 'null', 'none', '无标题',
    '未命名', '新建文档', 'new document',
}


def _is_valid_title(title: str) -> bool:
    """检查候选标题是否为有效标题"""
    title = title.strip()
    if len(title) < 3:
        return False
    if len(title) > 200:  # 太长可能是段落文本
        return False
    for p in _TITLE_GARBAGE_PATTERNS:
        if p.match(title):
            return False
    if title.lower() in _TITLE_BLOCKLIST:
        return False
    # 必须有至少 2 个有意义字符（中文/英文/数字）
    meaningful = re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\w]', title)
    if len(meaningful) < 2:
        return False
    # 无中文且 30%+ 标点符号 → 可能是乱码
    total = len(title)
    cn = len(re.findall(r'[\u4e00-\u9fff]', title))
    punct = len(re.findall(r'[!！?？。，,、；;：:\s\.\-+#$%^&*()\[\]{}|\\/<>]', title))
    if total > 10 and cn == 0 and punct / total > 0.3:
        return False
    return True


def _clean_heading(heading: str) -> str:
    """清洗标题行，去除常见噪音前缀和水印"""
    h = heading.strip()
    h = re.sub(r'^[!！]{1,5}\s*', '', h)           # 前导 !! 号
    h = re.sub(r'^[#＃]\s*', '', h)                 # 泄漏的 # 号
    h = re.sub(r'^[\d一二三四五六七八九十]+[、。．.)）\s]+', '', h)  # 序号 "1、" "一、"
    h = re.sub(r'\s*[!！]{1,5}$', '', h)            # 尾随 !! 号
    h = re.sub(r'[（(]?扫码[^）)]*[）)]?', '', h)   # 扫码广告
    h = re.sub(r'[（(]?关注公众号[^）)]*[）)]?', '', h)  # 公众号广告
    h = re.sub(r'[（(]?微信号[:：]\s*\S+[）)]?', '', h)  # 微信号广告
    return h.strip()


def _filename_to_title(filename: str) -> str:
    """从文件名提取可用的标题"""
    name = Path(filename).stem
    name = re.sub(r'^\d{4}[-_]?\d{2}[-_]?\d{0,2}[-_\s]*', '', name)
    name = re.sub(r'^\d{1,3}[-_\s]+', '', name)
    name = re.sub(
        r'[-_](新浪|搜狐|腾讯|网易|36氪|虎嗅|知乎|公众号|微信|今日头条|微博|百度|澎湃|界面|财新|华尔街见闻|雪球|东方财富|同花顺)$',
        '', name, flags=re.IGNORECASE
    )
    name = name.replace('_', ' ').replace('-', ' ')
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip()


def extract_title(md_text: str, filename: str) -> str:
    """从 MD 或文件名提取标题，带质量验证
    优先级：有效 H1 标题 > 有效 H2 标题 > 清洗后文件名 > 原始文件名
    """
    # 收集所有 H1/H2 标题
    headings = re.findall(r'^#{1,2}\s+(.+)$', md_text, re.MULTILINE)

    # 第一轮：清洗后检查有效性
    for h in headings:
        cleaned = _clean_heading(h)
        if _is_valid_title(cleaned):
            return cleaned

    # 第二轮：不清洗，长度合理且有效即用
    for h in headings:
        h = h.strip()
        if 3 <= len(h) <= 200:
            cleaned = _clean_heading(h)
            if _is_valid_title(cleaned):
                return cleaned

    # 兜底：从文件名提取
    name = _filename_to_title(filename)
    if _is_valid_title(name):
        return name

    # 最终兜底
    return Path(filename).stem or "untitled"


def extract_date_from_filename(filename: str) -> str:
    """从文件名提取日期"""
    name = Path(filename).stem

    for pattern, fmt in [
        (r'(\d{4})(\d{2})(\d{2})', '%Y%m%d'),
        (r'(\d{4})[-_](\d{2})[-_](\d{2})', '%Y-%m-%d'),
        (r'(\d{4})(\d{2})', '%Y%m'),
        (r'(\d{4})[-_](\d{2})', '%Y-%m'),
    ]:
        m = re.search(pattern, name)
        if m:
            try:
                if len(m.groups()) == 3:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if 2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                        return f"{y}-{mo:02d}-{d:02d}"
                elif len(m.groups()) == 2:
                    y, mo = int(m.group(1)), int(m.group(2))
                    if 2000 <= y <= 2099 and 1 <= mo <= 12:
                        return f"{y}-{mo:02d}-01"
            except (ValueError, IndexError):
                continue

    return ""


def extract_tags_from_path(rel_path: str) -> list:
    """从路径提取 tags（目录名作为 tag）"""
    parts = Path(rel_path).parts
    tags = []
    skip_dirs = {"raw", "todo", "done", "processing", "09-archive", "archive", "assets"}
    for part in parts[:-1]:
        if part.lower() in skip_dirs:
            continue
        tag = part.strip().replace(" ", "-")
        if tag and len(tag) < 30:
            tags.append(tag)
    return tags


# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Front Matter 构建（SCHEMA 完整字段）
# ══════════════════════════════════════════════════════════════════════════════

def build_frontmatter(
    title: str,
    page_type: str,
    content_proof: str,
    source_file: str,
    source_hash: str,
    tags: List[str] = None,
    created: str = "",
    validity: str = "current",
    expires: str = "",
) -> str:
    """
    构建符合 SCHEMA.md 第7节的完整 front matter

    必填字段：title, type, content_proof, sources, created, validity
    选填字段：tags, last_updated, last_verified, expires
    """
    now = _now_date()
    created = created or now

    tags_str = ", ".join(tags) if tags else ""
    source_rel = source_file.replace("\\", "/")

    # 格式化 sources 为 YAML 数组
    sources_yaml = f'["{source_rel}"]'

    # expires 字段处理
    expires_line = f"expires: {expires}" if expires else "expires:"

    fm = f"""---
title: "{title}"
type: {page_type}
tags: [{tags_str}]
sources: {sources_yaml}
created: {created}
last_updated: {now}
last_verified: {now}
validity: {validity}
{expires_line}
content_proof: "{content_proof}"
source_hash: "{source_hash}"
---"""
    return fm


# ══════════════════════════════════════════════════════════════════════════════
# 自动双链生成（SCHEMA 要求每个页面必须有 [[双链]] 关联连接）
# ══════════════════════════════════════════════════════════════════════════════

def _extract_title_keywords(title: str) -> List[str]:
    """从标题提取关键词（用于匹配相关页面）"""
    keywords = []
    # 英文单词
    en_words = re.findall(r'[a-zA-Z]{2,}', title)
    keywords.extend(w.lower() for w in en_words if len(w) >= 2)
    # 中文词组（2-4字滑动窗口）
    cn_chars = re.findall(r'[\u4e00-\u9fff]+', title)
    for segment in cn_chars:
        if len(segment) >= 2:
            keywords.append(segment)
            if len(segment) >= 4:
                # 也提取2字子串
                for i in range(len(segment) - 1):
                    keywords.append(segment[i:i+2])
    # 过滤纯数字年份关键词（如 "2021"、"2024"），年份太泛会错误匹配 [[2021]] 等年度索引
    keywords = [kw for kw in keywords if not re.match(r'^\d{4}$', kw)]
    return keywords


def _generate_bilinks(current_title: str, key_points: List[str] = None, wiki_dir: str = "") -> str:
    """
    搜索 wiki/ 已有页面，基于标题关键词匹配生成 [[双链]]

    策略：
    1. 从当前页面标题和关键要点提取关键词
    2. 扫描 wiki/ 下所有 .md 文件的标题
    3. 标题有重叠关键词的页面生成双链
    4. 最多返回 5 个相关链接
    """
    if not wiki_dir or not os.path.isdir(wiki_dir):
        return "（暂无关联页面）"

    # 提取当前页面的关键词
    search_text = current_title
    if key_points:
        search_text += " " + " ".join(key_points[:5])
    keywords = _extract_title_keywords(search_text)

    # 过滤太短或太通用的关键词
    generic = {"的", "了", "是", "在", "和", "与", "或", "不", "有", "中", "为",
               "an", "the", "of", "in", "is", "to", "and", "for", "on", "with",
               "如何", "什么", "怎么", "为什么", "哪些"}
    keywords = [kw for kw in keywords if kw.lower() not in generic and len(kw) >= 2]

    if not keywords:
        return "（暂无关联页面）"

    # 扫描 wiki/ 下所有页面标题
    candidate_pages = []  # [(title, wiki_name, score)]

    for root, dirs, files in os.walk(wiki_dir):
        # 跳过 logs 目录
        dirs[:] = [d for d in dirs if d != "logs"]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    head = f.read(500)
                # 提取标题
                m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', head, re.MULTILINE)
                if m:
                    page_title = m.group(1).strip()
                else:
                    page_title = Path(fname).stem

                # 不链接到自己
                if page_title.lower() == current_title.lower():
                    continue

                # 计算关键词重叠得分
                page_keywords = _extract_title_keywords(page_title)
                score = 0
                for kw in keywords:
                    for pkw in page_keywords:
                        if kw.lower() == pkw.lower():
                            score += 2  # 完全匹配
                        elif kw.lower() in pkw.lower() or pkw.lower() in kw.lower():
                            score += 1  # 部分匹配

                if score > 0:
                    # 提取 wiki_name（不含扩展名）
                    wiki_name = Path(fname).stem
                    candidate_pages.append((page_title, wiki_name, score))

            except Exception:
                continue

    if not candidate_pages:
        return "（暂无关联页面）"

    # 按得分排序，取前5
    candidate_pages.sort(key=lambda x: x[2], reverse=True)
    top_pages = candidate_pages[:5]

    # 生成双链
    bilinks = [f"- [[{name}]]" for _, name, _ in top_pages]
    return "\n".join(bilinks)


# ══════════════════════════════════════════════════════════════════════════════
# Step 5: 结构化 wiki 正文生成（SCHEMA Step 4 模板）
# ══════════════════════════════════════════════════════════════════════════════

def generate_structured_body(md_text: str, content_proof: str, key_points: List[str] = None, page_type: str = "concept", wiki_dir: str = "") -> str:
    """
    生成符合 SCHEMA.md 第4节模板的 wiki 正文

    根据 page_type 使用不同结构：
    - concept: ## 核心定义 + ## 关键要点
    - entity:  ## 简介 + ## 关键信息
    - source:  ## 核心定义 + ## 关键要点
    - synthesis: ## 核心定义 + ## 关键要点
    """
    # 解析原始 MD（去掉可能存在的 front matter）
    fm_end = _parse_front_matter_end(md_text)
    if fm_end >= 0:
        body = "\n".join(md_text.split("\n")[fm_end + 1:])
    else:
        body = md_text

    # 提取关键要点（h1 或 h2 标题）
    if not key_points:
        key_points = []
        skip_keywords = ("关联连接", "知识冲突", "References", "参考文献", "核心定义",
                        "关键要点", "详细说明", "目录", "Contents", "前言", "写在", "致谢")
        for line in body.split("\n"):
            # 匹配 ## 标题（优先）或 # 标题
            m = re.match(r'^#{1,2}\s+(.+)$', line)
            if m:
                heading = m.group(1).strip()
                # 排除目录、前言等非关键要点
                if any(skip in heading for skip in skip_keywords):
                    continue
                # 排除纯数字开头的目录项（如 "1. 有赞"）
                if re.match(r'^\d+\.', heading):
                    continue
                key_points.append(heading)

    # Fallback：如果关键要点不足3个，从段落首句提取
    if len(key_points) < 3:
        para_points = []
        in_code = False
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not line or line.startswith('#') or line.startswith('|') or line.startswith('!'):
                continue
            if line.startswith('- ') or line.startswith('* '):
                text = re.sub(r'^[-*+]\s+', '', line)
                text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
                if len(text) > 5:
                    para_points.append(text)
            elif not line.startswith('-') and not line.startswith('*') and len(line) > 15:
                # 段落首句
                sent = re.split(r'[。！？；]', line)[0]
                sent = re.sub(r'\*\*(.+?)\*\*', r'\1', sent)
                if len(sent) > 5:
                    para_points.append(sent)
            if len(para_points) >= 8:
                break
        # 合并：保留已有标题要点，补充段落要点
        existing_set = set(key_points)
        for pt in para_points:
            if pt not in existing_set:
                key_points.append(pt)
                existing_set.add(pt)
            if len(key_points) >= 8:
                break

    # 构建结构化正文（根据页面类型使用不同章节名）
    sections = []

    # 章节名称映射（SCHEMA.md 规范）
    section_names = {
        "concept": ("核心定义", "关键要点"),
        "entity": ("简介", "关键信息"),
        "source": ("核心定义", "关键要点"),
        "synthesis": ("核心定义", "关键要点"),
    }
    name1, name2 = section_names.get(page_type, ("核心定义", "关键要点"))

    # 1. 核心定义 / 简介
    sections.append(f"## {name1}\n\n{content_proof}")

    # 2. 关键要点 / 关键信息
    if key_points:
        points_md = "\n".join(f"- {pt}" for pt in key_points[:8])
        sections.append(f"## {name2}\n\n{points_md}")

    # 3. 详细说明（清洗后的原文）
    cleaned_body = clean_format(body)
    sections.append("## 详细说明\n\n" + cleaned_body)

    # 4. 知识冲突（预留）
    sections.append("## 知识冲突\n\n（暂无冲突记录）")

    # 从原始内容提取标题（用于双链匹配）
    title = ""
    m = re.search(r'^#\s+(.+)$', md_text, re.MULTILINE)
    if m:
        title = m.group(1).strip()

    # 5. 关联连接 — 自动双链生成
    # 搜索 wiki/ 已有页面，基于标题关键词匹配
    effective_wiki_dir = wiki_dir or _CURRENT_WIKI_DIR
    bilinks_text = _generate_bilinks(title, key_points, effective_wiki_dir)
    sections.append(f"## 关联连接\n\n{bilinks_text}")

    return "\n\n".join(sections)


# ══════════════════════════════════════════════════════════════════════════════
# Step 6: 排重检测
# ══════════════════════════════════════════════════════════════════════════════

def _title_to_wikiname(title: str) -> str:
    """将标题转为 kebab-case wiki 名称（与文件命名对齐）"""
    name = title.lower().strip()
    name = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '-', name)
    name = name.strip('-')
    return name


def check_duplicate_by_title(title: str, wiki_dir: str) -> Optional[str]:
    """检查 wiki 目录中是否有同标题页面（先查 index.md，再查文件）"""
    index_path = os.path.join(wiki_dir, "index.md")
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index_content = f.read()
            # 精确提取 index.md 中所有 [[链接]] 的目标标题
            all_index_links = re.findall(r'\[\[([^\]]+)\]\]', index_content)
            title_lower = title.lower().strip()
            wikiname = _title_to_wikiname(title)
            wikiname_lower = wikiname.lower().strip()
            # 精确匹配（排除纯数字年份等泛化关键词误匹配）
            for linked_title in all_index_links:
                linked_lower = linked_title.lower().strip()
                # 必须完全相同（原始标题或 kebab-case 形式），且不为纯4位数字年份
                if (linked_lower == title_lower or linked_lower == wikiname_lower) and \
                   not re.match(r'^\d{4}$', linked_title.strip()):
                    return f"index.md 中已存在: [[{linked_title}]]"
        except Exception:
            pass

    # 回退：遍历 wiki 目录文件（精确标题匹配）
    if not os.path.isdir(wiki_dir):
        return None
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if d != "logs"]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    head = f.read(500)
                m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', head, re.MULTILINE)
                if m and m.group(1).strip().lower() == title.lower():
                    return os.path.relpath(fpath, wiki_dir).replace("\\", "/")
            except Exception:
                continue
    return None


def check_duplicate_by_hash(body: str, wiki_dir: str) -> Optional[str]:
    """检查内容 hash 是否重复"""
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
                # 提取 body（去掉 front matter）
                existing_body = re.sub(r'^---\n.*?\n---\n', '', existing, count=1, flags=re.DOTALL)
                existing_hash = hashlib.md5(existing_body.strip().encode("utf-8")).hexdigest()
                if body_hash == existing_hash:
                    return os.path.relpath(fpath, wiki_dir).replace("\\", "/")
            except Exception:
                continue
    return None


def compute_file_hash(file_path: str) -> str:
    """计算文件内容 hash"""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# Step 8: Index 更新
# ══════════════════════════════════════════════════════════════════════════════

def update_index(wiki_dir: str, page_type: str, page_name: str, summary: str):
    """更新 wiki/index.md"""
    index_path = os.path.join(wiki_dir, "index.md")
    if not os.path.exists(index_path):
        _create_index(index_path)

    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    section_map = {
        "concept": "## 概念 (Concepts)",
        "entity": "## 实体 (Entities)",
        "source": "## 来源 (Sources)",
        "synthesis": "## 综合 (Syntheses)",
    }
    section_header = section_map.get(page_type, "## 来源 (Sources)")
    new_entry = f"- [[{page_name}]] — {summary}"

    if f"[[{page_name}]]" in content:
        return  # 已存在

    if section_header in content:
        idx = content.index(section_header)
        rest = content[idx + len(section_header):]
        next_section = rest.find("\n## ")
        if next_section == -1:
            content = content.rstrip() + "\n" + new_entry + "\n"
        else:
            insert_pos = idx + len(section_header) + next_section
            content = content[:insert_pos].rstrip() + "\n" + new_entry + "\n" + content[insert_pos:]
    else:
        content = content.rstrip() + f"\n\n{section_header}\n{new_entry}\n"

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(content)

    _sort_index_section(index_path, section_header)


def _sort_index_section(index_path: str, section_header: str):
    """对 index.md 中指定区块按拼音排序"""
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    if section_header not in content:
        return

    idx = content.index(section_header)
    rest = content[idx + len(section_header):]
    next_section = rest.find("\n## ")
    if next_section == -1:
        section_body = rest
        after_section = ""
    else:
        section_body = rest[:next_section]
        after_section = rest[next_section:]

    entries = [line for line in section_body.strip().split("\n") if line.strip().startswith("- ")]
    if not entries:
        return

    entries.sort(key=lambda e: e.lower())

    new_section_body = "\n".join(entries)
    content = content[:idx] + section_header + "\n" + new_section_body + "\n" + after_section

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


def _to_kebab(title: str) -> str:
    """标题转 kebab-case"""
    s = title.strip().lower()
    s = re.sub(r'[^\w一-鿿]+', '-', s)
    s = s.strip('-')
    s = re.sub(r'-+', '-', s)
    return s or "untitled"


def _to_title_case(title: str) -> str:
    """标题转 TitleCase（用于 concepts/entities）
    - 中文为主的内容保持原样
    - 英文为主的内容首字母大写
    """
    title = title.strip()

    # 计算中文/东亚字符占比，过半则保持原样
    cjk = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]', title))
    total = len(title.replace(' ', '').replace('-', '').replace('.', ''))
    if total > 0 and cjk / total > 0.5:
        return title

    # 英文词首字母大写
    def capitalize_word(match):
        word = match.group(0)
        if re.match(r'^[a-zA-Z]+$', word):
            # 仅全小写才首字母大写，保留混合大小写的专有名词（如 ChatGPT）
            return word.capitalize() if word.islower() else word
        return word

    result = re.sub(r'[a-zA-Z]+', capitalize_word, title)
    if result and result[0].isascii() and result[0].isalpha():
        result = result[0].upper() + result[1:]
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2.3: raw 文件 processed 标记
# ══════════════════════════════════════════════════════════════════════════════

def mark_raw_processed(
    source_file: str,
    wiki_pages: List[str],
    vault_root: str = "",
) -> bool:
    """
    在源文件 front matter 标记 processed 状态

    Args:
        source_file: 源文件路径
        wiki_pages: 生成的 wiki 页面列表
        vault_root: Vault 根目录（用于日志）

    Returns:
        是否成功标记
    """
    if not source_file or not os.path.exists(source_file):
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            content = f.read()

        now = _now_date()

        # 检查是否已有 front matter
        if content.strip().startswith("---"):
            # 已有 front matter，解析并追加字段
            lines = content.split("\n")
            if len(lines) >= 2 and lines[1].strip() != "---":
                # 找到 front matter 结束位置
                fm_end = -1
                for i in range(1, len(lines)):
                    if lines[i].strip() == "---":
                        fm_end = i
                        break

                if fm_end > 0:
                    # 解析现有 front matter
                    fm_lines = lines[1:fm_end]
                    fm_dict = {}
                    for line in fm_lines:
                        if ":" not in line:
                            continue
                        key, _, value = line.partition(":")
                        fm_dict[key.strip()] = value.strip()

                    # 更新字段
                    fm_dict["processed"] = "true"
                    fm_dict["processed_date"] = now
                    fm_dict["wiki_pages"] = "[" + ", ".join(f"[[{p}]]" for p in wiki_pages) + "]"

                    # 重建 front matter
                    new_fm = []
                    for k, v in fm_dict.items():
                        if isinstance(v, list):
                            new_fm.append(f"{k}: {v}")
                        else:
                            new_fm.append(f"{k}: {v}")

                    new_content = "---\n" + "\n".join(new_fm) + "\n---\n" + "\n".join(lines[fm_end + 1:])
                else:
                    # 没有有效的 front matter 结束标记，在开头插入
                    fm_text = f"""---
processed: true
processed_date: {now}
wiki_pages: [{", ".join(f"[[{p}]]" for p in wiki_pages)}]
---

"""
                    new_content = fm_text + content
            else:
                # 空 front matter，添加字段
                fm_text = f"""---
processed: true
processed_date: {now}
wiki_pages: [{", ".join(f"[[{p}]]" for p in wiki_pages)}]
---
"""
                new_content = fm_text + content[3:]  # 跳过开头的 ---
        else:
            # 没有 front matter，在开头插入
            fm_text = f"""---
processed: true
processed_date: {now}
wiki_pages: [{", ".join(f"[[{p}]]" for p in wiki_pages)}]
---

"""
            new_content = fm_text + content

        # 写回文件
        with open(source_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        _debug_log("MARK_PROCESSED", source_file, f"wiki_pages={len(wiki_pages)}")

        return True
    except Exception as e:
        print(f"[ERROR] 标记 processed 失败: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2.1: 质量门禁
# ══════════════════════════════════════════════════════════════════════════════

def check_front_matter_quality(text: str) -> Tuple[bool, List[str]]:
    """检查 front matter 完整性（来自 pre_validate.py）"""
    errors = []

    fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not fm_match:
        return False, ["缺少 front matter（以 --- 开头和结束）"]

    fm = fm_match.group(1)
    required_fields = ["title:", "type:", "sources:", "created:", "validity:"]
    for field in required_fields:
        if field not in fm:
            errors.append(f"缺少字段：{field.rstrip(':')}")

    if "tags:" not in fm:
        errors.append("缺少字段：tags")

    # 检查 type 值
    type_match = re.search(r'type:\s*(\S+)', fm)
    if type_match:
        type_val = type_match.group(1)
        valid_types = {"source", "entity", "concept", "synthesis"}
        if type_val not in valid_types:
            errors.append(f"type 值无效：{type_val}（应为 source/entity/concept/synthesis）")

    return len(errors) == 0, errors


def check_content_proof_quality(text: str, min_length: int = 20) -> Tuple[bool, str]:
    """检查 content_proof 字段存在且有效（来自 pre_validate.py）"""
    PLACEHOLDER_WORDS = ["待补充", "详见原文", "待完善", "TBD", "TODO", "待人工审核"]
    # 注意: 不包含单字"略"和"无"，太短容易误匹配正常文本（如"策略""无异于"）

    match = re.search(r'content_proof:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if not match:
        return False, "content_proof 字段不存在"

    proof = match.group(1).strip()
    if not proof or proof in PLACEHOLDER_WORDS:
        return False, f"content_proof 为空或占位：{proof}"

    if len(proof) < min_length:
        return False, f"content_proof 长度不足：{len(proof)}字（要求≥{min_length}字）"

    return True, ""


def check_core_points_quality(text: str, min_points: int = 3) -> Tuple[bool, str]:
    """检查核心观点区块（来自 pre_validate.py）
    
    优先检查「关键要点」/「关键信息」区块（列表项格式），
    回退检查「核心定义」/「简介」区块。
    """
    # 优先查找列表类区块（关键要点/关键信息），这些区块应该有列表项
    list_section_names = ["关键要点", "关键信息", "核心观点"]
    for section_name in list_section_names:
        section_match = re.search(rf'##\s*{section_name}\s*\n(.*?)(?=\n##|\Z)', text, re.DOTALL)
        if section_match:
            section = section_match.group(1)
            points = re.findall(r'^\s*[-*+]\s+\S', section, re.MULTILINE)
            if len(points) >= min_points:
                return True, ""
            elif len(points) > 0:
                return False, f"核心观点不足：{len(points)}条（要求≥{min_points}条）"

    # 回退：查找定义类区块（核心定义/简介），检查是否有足够内容
    def_section_names = ["核心定义", "简介"]
    for section_name in def_section_names:
        section_match = re.search(rf'##\s*{section_name}\s*\n(.*?)(?=\n##|\Z)', text, re.DOTALL)
        if section_match:
            section = section_match.group(1).strip()
            # 检查内容长度或列表项
            points = re.findall(r'^\s*[-*+]\s+\S', section, re.MULTILINE)
            if len(points) >= min_points:
                return True, ""
            # 如果内容够长（>100字），也算通过
            if len(section) >= 100:
                return True, ""

    return False, f"缺少「关键要点」/「关键信息」/「核心观点」区块且内容不足"


def quality_gate(wiki_content: str, cfg: dict = None) -> Tuple[bool, List[str]]:
    """
    质量门禁：写文件前阻断性检查

    检查项：
    1. front matter 完整性
    2. content_proof 有效性
    3. 核心观点数量

    Returns:
        (是否通过, 错误列表)
    """
    cfg = cfg or {}
    gates = cfg.get("quality_gates", {})

    min_proof_length = gates.get("min_proof_length", 20)
    min_core_points = gates.get("require_core_points", 2)

    errors = []

    # 1. Front matter 检查
    fm_ok, fm_errors = check_front_matter_quality(wiki_content)
    if not fm_ok:
        errors.extend(fm_errors)

    # 2. Content proof 检查
    cp_ok, cp_msg = check_content_proof_quality(wiki_content, min_proof_length)
    if not cp_ok:
        errors.append(cp_msg)

    # 3. 核心观点检查
    cps_ok, cps_msg = check_core_points_quality(wiki_content, min_core_points)
    if not cps_ok:
        errors.append(cps_msg)

    return len(errors) == 0, errors


def validate_after_write(wiki_path: str, cfg: dict = None) -> Tuple[bool, List[str]]:
    """
    写文件后验证：检查生成的 wiki 页面是否符合质量标准

    Returns:
        (是否通过, 错误列表)
    """
    import subprocess

    cfg = cfg or {}
    gates = cfg.get("quality_gates", {})

    # 质量门禁配置
    min_proof = gates.get("min_proof_length", 20)
    require_bilinks = gates.get("require_bilinks", 1)  # 默认为1，需要双链
    require_core_points = gates.get("require_core_points", 2)  # 默认为2，降低门槛
    require_content_proof = gates.get("require_content_proof", True)  # 默认需要 content_proof

    # 如果设置为0，则禁用该检查
    if require_bilinks == 0:
        require_bilinks = None
    if require_core_points == 0:
        require_core_points = None
    if require_content_proof is False:
        min_proof = 0  # 禁用 content_proof 长度检查

    # 直接调用 validate_wiki.py 的逻辑
    return _validate_wiki_page(wiki_path, min_proof, require_bilinks, require_core_points, require_content_proof)


def _validate_wiki_page(file_path: str, min_proof_length: int = 20, require_bilinks: int = 1, require_core_points: int = 2, require_content_proof: bool = True) -> Tuple[bool, List[str]]:
    """验证单个 wiki 页面（内联实现，避免循环导入）

    Args:
        file_path: wiki 文件路径
        min_proof_length: content_proof 最小长度（0表示不检查）
        require_bilinks: 需要的最少双链数量（None表示不检查）
        require_core_points: 需要的最少核心观点数量（None表示不检查）
        require_content_proof: 是否必须存在 content_proof
    """
    errors = []
    PLACEHOLDER_WORDS = ["待补充", "详见原文", "待完善", "TBD", "TODO", "待人工审核"]
    # 注意: 不包含单字"略"和"无"，太短容易误匹配正常文本（如"策略""无异于"）
    VALID_TYPES = {"source", "entity", "concept", "synthesis"}
    VALID_VALIDITY = {"current", "changing", "historical", "stale"}

    if not os.path.exists(file_path):
        return False, [f"文件不存在: {file_path}"]

    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # 检查 front matter
    if not content.startswith("---"):
        errors.append("缺少 front matter")
        return False, errors

    try:
        second_dash = content.find("---", 3)
        if second_dash == -1:
            errors.append("front matter 不完整")
            return False, errors

        front_matter_text = content[3:second_dash].strip()

        try:
            fm = yaml.safe_load(front_matter_text) or {}
        except Exception:
            fm = {}
            for line in front_matter_text.split("\n"):
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                fm[key.strip()] = value.strip()
    except Exception as e:
        errors.append(f"front matter 解析失败: {e}")
        return False, errors

    # 必需字段
    for field in ["title", "type", "content_proof", "sources", "created", "validity", "tags"]:
        if field not in fm:
            errors.append(f"缺少字段: {field}")

    # type 验证
    wiki_type = fm.get("type", "")
    if isinstance(wiki_type, str) and wiki_type not in VALID_TYPES:
        errors.append(f"type 无效: {wiki_type}")

    # validity 验证
    validity = fm.get("validity", "")
    if isinstance(validity, str) and validity not in VALID_VALIDITY:
        errors.append(f"validity 无效: {validity}")

    # content_proof 验证
    if require_content_proof:
        proof = fm.get("content_proof", "")
        if isinstance(proof, str):
            proof = proof.strip()
        elif proof is None:
            proof = ""
        else:
            proof = str(proof).strip()

        if not proof:
            errors.append("content_proof 为空")
        elif any(pw in proof for pw in PLACEHOLDER_WORDS):
            errors.append(f"content_proof 含占位文本")
        elif min_proof_length > 0 and len(proof) < min_proof_length:
            errors.append(f"content_proof 太短（{len(proof)} < {min_proof_length}字）")

    # 正文非空
    body = content[second_dash + 3:].strip()
    if not body or len(body.strip()) < 10:
        errors.append("正文内容为空或太短")

    # 双链检查
    if require_bilinks is not None:
        bilinks = re.findall(r'\[\[.+?\]\]', body)
        # 如果关联连接区块明确表示“暂无关联页面”，跳过双链数量检查
        no_bilinks_note = "暂无关联页面" in body
        if not no_bilinks_note and len(bilinks) < require_bilinks:
            errors.append(f"双链数量不足（{len(bilinks)} < {require_bilinks}）")

    # 核心观点检查（与 check_core_points_quality 对齐：支持列表项和标题两种格式）
    if require_core_points is not None:
        # 优先检查列表项（- xxx 格式），与 generate_structured_body 输出对齐
        list_points = re.findall(r'^\s*[-*+]\s+\S', body, re.MULTILINE)
        heading_points = re.findall(r'^###?\s+\d+\.?', body, re.MULTILINE)
        core_points_count = max(len(list_points), len(heading_points))
        if core_points_count < require_core_points:
            errors.append(f"核心观点不足（{core_points_count} < {require_core_points}）")

    return len(errors) == 0, errors


# ══════════════════════════════════════════════════════════════════════════════
# 主编译入口
# ══════════════════════════════════════════════════════════════════════════════

def compile_single(
    md_path: str,
    source_file: str,
    vault_root: str,
    cfg: dict = None,
    dry_run: bool = False,
) -> dict:
    """
    统一编译入口：MD → wiki 页面（符合 SCHEMA v4.0.0）

    Args:
        md_path: MinerU 生成的 MD 文件路径
        source_file: 原始源文件路径（PDF/DOCX 等）
        vault_root: Vault 根目录
        cfg: 配置字典（可选）
        dry_run: 只返回结果，不写入

    Returns:
        {
            success: bool,
            wiki_path: str,
            wiki_name: str,
            title: str,
            page_type: str,
            content_proof_length: int,
            duplicate_of: str,
            skipped: bool,
            error: str
        }
    """
    cfg = cfg or {}
    vault_root = vault_root or cfg.get("vault", {}).get("root", "")

    if not vault_root:
        return {"success": False, "error": "未指定 vault_root"}

    wiki_dir = os.path.join(vault_root, "wiki")

    # 设置全局 wiki 目录，供双链生成函数使用
    global _CURRENT_WIKI_DIR
    _CURRENT_WIKI_DIR = wiki_dir

    # Step 0: 读取 MD 内容
    if not os.path.exists(md_path):
        return {"success": False, "error": "MD 文件不存在", "wiki_path": ""}

    try:
        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()
    except Exception as e:
        return {"success": False, "error": f"读取 MD 失败: {e}"}

    if not md_text.strip():
        return {"success": False, "error": "MD 文件为空"}

    # Step 1: 格式清洗
    md_text = clean_format(md_text)

    # Step 2: 页面类型自动分类
    rel_path = os.path.relpath(md_path, vault_root)
    page_type = auto_classify_page_type(md_text, rel_path)

    # Step 3: 提取 content_proof
    content_proof = extract_content_proof(md_text)

    # 确保 content_proof 至少 20 字
    if len(content_proof) < 20:
        filename = os.path.basename(md_path)
        content_proof = f"（待人工审核：{os.path.splitext(filename)[0]}）"

    if len(content_proof) > 500:
        content_proof = content_proof[:500]

    # 计算文件 hash
    source_hash = compute_file_hash(md_path)

    # 提取元数据
    filename = os.path.basename(md_path)
    title = extract_title(md_text, filename)
    created = extract_date_from_filename(filename)
    tags = extract_tags_from_path(rel_path)

    # Step 5: 生成结构化正文
    structured_body = generate_structured_body(md_text, content_proof, page_type=page_type, wiki_dir=wiki_dir)

    # Step 6: 排重检测
    if cfg.get("compile", {}).get("dedup", {}).get("enabled", True):
        dup = check_duplicate_by_title(title, wiki_dir)
        if dup:
            return {
                "success": True,
                "skipped": True,
                "duplicate_of": dup,
                "reason": f"标题重复: {dup}",
                "wiki_path": "",
            }

        dup_hash = check_duplicate_by_hash(structured_body, wiki_dir)
        if dup_hash:
            return {
                "success": True,
                "skipped": True,
                "duplicate_of": dup_hash,
                "reason": f"内容重复: {dup_hash}",
                "wiki_path": "",
            }

    # 生成 wiki 文件名和路径
    # concepts/entities 用 TitleCase，其他用 kebab-case
    if page_type in ("concept", "entity"):
        wiki_name = _to_title_case(title)
    else:
        wiki_name = _to_kebab(title)

    # 清理 Windows 非法文件名字符: \ / : * ? " < > |
    # 以及中文标点「」『』等可能被转换成非法字符的符号
    wiki_name = re.sub(r'[\\/:*?"<>|]', '-', wiki_name)
    wiki_name = wiki_name.replace('「', '(').replace('」', ')').replace('『', '(').replace('』', ')')
    wiki_name = wiki_name.replace('×', 'x').replace('—', '-').replace('–', '-')
    # 清理超长文件名（Windows MAX_PATH 限制 + 美观考虑）
    if len(wiki_name) > 150:
        wiki_name = wiki_name[:150]
    wiki_name = wiki_name.strip('-').strip()
    if not wiki_name:
        wiki_name = 'untitled'

    wiki_subdir = get_wiki_subdir(page_type)
    wiki_dir_type = os.path.join(wiki_dir, wiki_subdir)
    wiki_path = os.path.join(wiki_dir_type, f"{wiki_name}.md")

    if dry_run:
        return {
            "success": True,
            "wiki_path": wiki_path,
            "wiki_name": wiki_name,
            "title": title,
            "page_type": page_type,
            "content_proof_length": len(content_proof),
            "tags": tags,
        }

    # 构建 front matter
    source_basename = os.path.basename(source_file)
    front_matter = build_frontmatter(
        title=title,
        page_type=page_type,
        content_proof=content_proof,
        source_file=source_basename,
        source_hash=source_hash,
        tags=tags,
        created=created,
        validity="current",
    )

    # 组合完整 wiki 内容
    wiki_content = front_matter + "\n\n" + structured_body

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 7: 质量门禁（Phase 2.1）- 写前阻断性检查 + 写后验证性检查 + 重试机制
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    max_attempts = cfg.get("compile_queue", {}).get("max_attempts", 3)
    attempt = 0
    last_errors = []

    # 尝试写入，最多 max_attempts 次
    while attempt < max_attempts:
        attempt += 1

        # === 写前质量门禁 ===
        gate_passed, gate_errors = quality_gate(wiki_content, cfg)
        if not gate_passed:
            # 质量门禁失败，不写入
            last_errors = gate_errors
            if attempt < max_attempts:
                # 尝试修复 content_proof（简单的自动修复策略）
                if len(content_proof) < 20:
                    filename = os.path.basename(md_path)
                    content_proof = f"（需人工审核：{os.path.splitext(filename)[0]}）"
                    front_matter = build_frontmatter(
                        title=title,
                        page_type=page_type,
                        content_proof=content_proof,
                        source_file=source_basename,
                        source_hash=source_hash,
                        tags=tags,
                        created=created,
                        validity="current",
                    )
                    wiki_content = front_matter + "\n\n" + structured_body
                continue
            else:
                # 达到最大重试次数，标记失败
                return {
                    "success": False,
                    "error": f"质量门禁失败（重试{attempt}次）: {', '.join(gate_errors)}",
                    "wiki_path": "",
                    "quality_gate_failed": True,
                }

        # === 写文件 ===
        os.makedirs(wiki_dir_type, exist_ok=True)

        # 检查是否需要追加 sources 到已有页面
        if os.path.exists(wiki_path):
            _append_source_to_existing(wiki_path, source_basename)
            write_success = True
        else:
            try:
                with open(wiki_path, "w", encoding="utf-8") as f:
                    f.write(wiki_content)
                _debug_log("WRITE_WIKI", wiki_path, f"size={len(wiki_content)}")
                write_success = True
            except Exception as e:
                write_success = False
                last_errors = [f"写文件失败: {e}"]

        if not write_success:
            if attempt < max_attempts:
                continue
            else:
                return {
                    "success": False,
                    "error": f"写文件失败（重试{attempt}次）: {last_errors[0]}",
                    "wiki_path": "",
                }

        # === 写后验证性检查 ===
        validate_passed, validate_errors = validate_after_write(wiki_path, cfg)
        if not validate_passed:
            last_errors = validate_errors
            # 验证失败，删除文件并重试
            try:
                if os.path.exists(wiki_path):
                    os.remove(wiki_path)
            except Exception:
                pass
            if attempt < max_attempts:
                continue
            else:
                return {
                    "success": False,
                    "error": f"验证失败（重试{attempt}次）: {', '.join(validate_errors)}",
                    "wiki_path": "",
                    "validation_failed": True,
                }

        # 所有检查通过，退出重试循环
        break

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 9: 更新 index
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    summary = content_proof[:100] + "..." if len(content_proof) > 100 else content_proof
    update_index(wiki_dir, page_type, wiki_name, summary)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 10: Phase 2.3 - 标记源文件 processed
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    wiki_pages = [f"wiki/{wiki_subdir}/{wiki_name}.md"]
    if log_operation:
        try:
            mark_raw_processed(source_file, wiki_pages, vault_root)
        except Exception:
            pass  # 忽略标记失败

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 11: Phase 2.2 - 记录操作日志
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if log_operation:
        try:
            log_operation(
                operation="wiki-update",
                title=title,
                raw_file=rel_path,
                wiki_pages={page_type: [f"{wiki_name}.md"]},
                vault_root=vault_root,
                skip_duplicate=True,
            )
        except Exception as e:
            pass  # 忽略日志失败

    return {
        "success": True,
        "wiki_path": wiki_path,
        "wiki_name": wiki_name,
        "title": title,
        "page_type": page_type,
        "content_proof_length": len(content_proof),
        "tags": tags,
    }


def _append_source_to_existing(wiki_path: str, source_file: str):
    """向已有 wiki 页面追加 source 引用"""
    try:
        with open(wiki_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return

    source_rel = source_file.replace("\\", "/")

    fm_match = re.match(r'^(---\s*\n)(.*?)(\n---)', content, re.DOTALL)
    if not fm_match:
        return

    fm_text = fm_match.group(2)
    body = content[fm_match.end():]

    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception:
        return

    existing_sources = fm.get("sources", [])
    if not isinstance(existing_sources, list):
        existing_sources = [existing_sources] if existing_sources else []
    if source_rel in existing_sources:
        return

    existing_sources.append(source_rel)
    fm["sources"] = existing_sources

    try:
        new_fm = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    except Exception:
        return

    new_content = f"---\n{new_fm}\n---{body}"
    with open(wiki_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    _debug_log("APPEND_SOURCE", wiki_path, "added source reference")


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口（可选）
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="统一编译引擎 (SCHEMA v4.0.0)")
    parser.add_argument("md_file", help="MD 文件路径")
    parser.add_argument("--vault", "-v", required=True, help="Vault 根目录")
    parser.add_argument("--source", "-s", help="源文件路径（默认从 MD 推断）")
    parser.add_argument("--dry-run", action="store_true", help="只返回结果，不写入")

    args = parser.parse_args()

    source_file = args.source or args.md_file
    result = compile_single(args.md_file, source_file, args.vault, dry_run=args.dry_run)

    if result.get("success"):
        if result.get("skipped"):
            print(f"⏭ 跳过: {result.get('reason')}")
        else:
            print(f"✅ 编译成功: {result.get('wiki_path')}")
            print(f"   类型: {result.get('page_type')}")
            print(f"   标题: {result.get('title')}")
    else:
        print(f"❌ 失败: {result.get('error')}")
