"""config_loader.py - 统一配置加载（YAML 优先，JSON 兼容旧版）

同时提供统一的扩展名映射和文件分类常量，供其他模块引用，避免硬编码和重复定义。
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

_TZ_CN = timezone(timedelta(hours=8))


# ── 统一文件分类常量（唯一权威定义）────────────────────────────

class FileCategory:
    """文件大类（字符串常量，避免 enum 跨模块兼容问题）"""
    PDF = "pdf"
    OFFICE_MODERN = "office"       # DOCX / PPTX
    OFFICE_LEGACY = "legacy"       # DOC / PPT
    SPREADSHEET = "spreadsheet"    # XLSX
    IMAGE = "image"
    UNKNOWN = "unknown"


# 扩展名 → 分类映射（唯一权威定义，其他模块从此导入）
EXT_CATEGORY_MAP: dict[str, str] = {
    ".pdf":  FileCategory.PDF,
    ".docx": FileCategory.OFFICE_MODERN,
    ".pptx": FileCategory.OFFICE_MODERN,
    ".doc":  FileCategory.OFFICE_LEGACY,
    ".ppt":  FileCategory.OFFICE_LEGACY,
    ".xlsx": FileCategory.SPREADSHEET,
    ".png":  FileCategory.IMAGE,
    ".jpg":  FileCategory.IMAGE,
    ".jpeg": FileCategory.IMAGE,
    ".bmp":  FileCategory.IMAGE,
    ".tiff": FileCategory.IMAGE,
    ".tif":  FileCategory.IMAGE,
    ".webp": FileCategory.IMAGE,
    ".gif":  FileCategory.IMAGE,
    ".jp2":  FileCategory.IMAGE,
}

# 所有支持的扩展名集合
SUPPORTED_EXTENSIONS = set(EXT_CATEGORY_MAP.keys())

# 源文件扩展名集合（非 MD，用于归档/迁移过滤）
SOURCE_EXTENSIONS = SUPPORTED_EXTENSIONS


def classify_file(path: str) -> str:
    """根据扩展名分类文件，返回 FileCategory 字符串"""
    ext = os.path.splitext(path)[1].lower()
    return EXT_CATEGORY_MAP.get(ext, FileCategory.UNKNOWN)


def _find_config() -> Path:
    scripts_dir = Path(__file__).resolve().parent
    skill_dir = scripts_dir.parent
    # 优先从 local/ 目录加载（用户配置，gitignore）
    local_yaml = skill_dir / "local" / "config.yaml"
    local_json = skill_dir / "local" / "config.json"
    if local_yaml.exists():
        return local_yaml
    if local_json.exists():
        return local_json
    raise FileNotFoundError(
        f"配置文件不存在。\n"
        f"请将配置放在 local/ 目录（被 gitignore 保护，不会泄漏到 GitHub）：\n"
        f"  mkdir -p local\n"
        f"  cp config.yaml.example local/config.yaml\n"
        f"\n"
        f"填入你的 MinerU Token 后即可使用。"
    )


def load_config(config_path: Optional[str] = None) -> dict:
    path = Path(config_path) if config_path else _find_config()
    with open(path, "r", encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            import yaml
            raw = yaml.safe_load(f)
        else:
            raw = json.load(f)

    # tokens
    raw_tokens = raw.get("tokens", [])
    token_expires_at = raw.get("token_expires_at", "")
    tokens = []
    for t in raw_tokens:
        if isinstance(t, str):
            tokens.append({"token": t, "expires": token_expires_at})
        elif isinstance(t, dict):
            tokens.append({
                "token": t.get("token", t.get("value", "")),
                "expires": t.get("expires", token_expires_at),
            })
    if not tokens:
        raise ValueError("config 中未配置任何 Token")

    # engine
    engine_raw = raw.get("engine", {})
    provider = engine_raw.get("provider", "mineru")
    mineru_cfg = engine_raw.get("mineru", {})
    engine = {
        "provider": provider,
        "mineru": {
            "base_url": mineru_cfg.get("base_url", "https://mineru.net"),
            "verify_ssl": mineru_cfg.get("verify_ssl", False),
            "timeout": mineru_cfg.get("timeout", 300),
            "poll_interval": mineru_cfg.get("poll_interval", 3),
            "max_poll_interval": mineru_cfg.get("max_poll_interval", 30),
        },
    }

    # parallel
    parallel_raw = raw.get("parallel", {})
    parallel = {
        "enabled": parallel_raw.get("enabled", True),
        "max_workers": parallel_raw.get("max_workers", 4),
    }

    # vault
    vault_raw = raw.get("vault", {})
    vault = {
        "root": os.path.expandvars(os.path.expanduser(vault_raw.get("root", ""))),
        "raw_dir": vault_raw.get("raw_dir", "raw"),
        "todo_dir": vault_raw.get("todo_dir", "todo"),
        "wiki_dir": vault_raw.get("wiki_dir", "wiki"),
        "assets_dir": vault_raw.get("assets_dir", "assets"),
        "schema": vault_raw.get("schema", "SCHEMA.md"),
        "state_dir": vault_raw.get("state_dir", ".obsidian-ingest"),
        "wiki_subdirs": vault_raw.get("wiki_subdirs", ["sources", "concepts", "entities", "syntheses", "logs"]),
    }

    # dirs（相对于 vault.root）
    dirs_raw = raw.get("dirs", {})
    dirs = {}
    for key in ("source", "output", "archive", "md_archive", "failed"):
        val = dirs_raw.get(key, "")
        if val:
            val = os.path.expandvars(os.path.expanduser(val))
            val = val.replace("\\", "/")
        dirs[key] = val

    # exclude 目录列表（不扫描）
    dirs["exclude"] = dirs_raw.get("exclude", [])

    # skip_dirnames 目录名列表（扫描时跳过这些名字的子目录，如 images）
    dirs["skip_dirnames"] = dirs_raw.get("skip_dirnames", ["images"])

    # compile
    compile_raw = raw.get("compile", {})
    llm_raw = compile_raw.get("llm", {})
    fm_raw = compile_raw.get("frontmatter", {})
    dedup_raw = compile_raw.get("dedup", {})
    arc_raw = compile_raw.get("archive", {})
    compile = {
        "mode": compile_raw.get("mode", "light"),
        "llm": {
            "provider": llm_raw.get("provider", "openai"),
            "model": llm_raw.get("model", "gpt-4o-mini"),
            "api_key": llm_raw.get("api_key", ""),
            "base_url": llm_raw.get("base_url", ""),
        },
        "frontmatter": {
            "type": fm_raw.get("type", "source"),
            "validity": fm_raw.get("validity", "current"),
            "tags_from_path": fm_raw.get("tags_from_path", True),
        },
        "dedup": {
            "enabled": dedup_raw.get("enabled", True),
            "file_fingerprint": dedup_raw.get("file_fingerprint", True),
            "content_hash": dedup_raw.get("content_hash", True),
            "semantic": dedup_raw.get("semantic", False),
        },
        "archive": {
            # 新字段：source / MD 分离归档
            "source_retention_days": arc_raw.get("source_retention_days", arc_raw.get("retention_days", 7)),
            "source_auto_delete": arc_raw.get("source_auto_delete", arc_raw.get("auto_delete", True)),
            "md_retention_days": arc_raw.get("md_retention_days", -1),   # -1 = 永久保留
            "md_auto_delete": arc_raw.get("md_auto_delete", False),
            # 保留旧字段兼容
            "retention_days": arc_raw.get("retention_days", 7),
            "auto_delete": arc_raw.get("auto_delete", True),
        },
    }

    # watcher
    watcher_raw = raw.get("watcher", {})
    watcher = {
        "poll_interval": watcher_raw.get("poll_interval", 10),
        "stability_checks": watcher_raw.get("stability_checks", 3),
        "stability_interval": watcher_raw.get("stability_interval", 2),
    }

    # automation
    auto_raw = raw.get("automation", {})
    automation = {
        "auto_migrate": auto_raw.get("auto_migrate", True),
        "auto_compile": auto_raw.get("auto_compile", True),
    }

    return {
        "tokens": tokens,
        "engine": engine,
        "parallel": parallel,
        "vault": vault,
        "dirs": dirs,
        "compile": compile,
        "watcher": watcher,
        "automation": automation,
        "_path": str(path),
    }


def resolve_vault_path(cfg: dict, relative_path: str) -> str:
    """将相对路径解析为绝对路径（基于 vault.root）"""
    root = cfg["vault"]["root"]
    if not root:
        return relative_path
    return os.path.join(root, relative_path)


def seconds_until(expires_str: str) -> int:
    if not expires_str:
        return 999 * 86400
    dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
    now = datetime.now(_TZ_CN)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_CN)
    return int((dt - now).total_seconds())


def format_countdown(seconds: int) -> str:
    if seconds <= 0:
        return "⚠️ 已过期"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    if d > 0:
        return f"⏱️ {d}天{h}小时{m}分"
    elif h > 0:
        return f"⏱️ {h}小时{m}分"
    else:
        return f"⏱️ {m}分"


def build_expiry_info(tokens: list) -> dict:
    """构建 Token 过期摘要（取最近过期的那个）"""
    if not tokens:
        return {"token_expires_at": "", "token_remaining_seconds": 0, "token_remaining_countdown": "无 Token"}
    earliest = min((seconds_until(t.get("expires", "")) for t in tokens), default=999 * 86400)
    expires_at = tokens[0].get("expires", "")
    return {
        "token_expires_at": expires_at,
        "token_remaining_seconds": earliest,
        "token_remaining_countdown": format_countdown(earliest),
    }
