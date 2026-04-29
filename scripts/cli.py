"""
cli.py - obsidian-ingest CLI 入口

用法:
    python cli.py status                         # 查看状态
    python cli.py compile [--vault PATH]         # 转换 + 编译
    python cli.py compile --dry-run              # 预览不执行
    python cli.py resume                         # 恢复中断任务
    python cli.py watch                          # 监听模式
    python cli.py init                           # 初始化 vault 目录结构
"""

import sys
import os
import argparse
import signal
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from persistent_queue import (
    PersistentQueue, BatchProcessor, Status,
    cmd_queue_status, cmd_queue_retry, cmd_queue_clear,
    build_registry,
)
from config_loader import load_config, resolve_vault_path
from checkpoint import (
    Checkpoint, format_progress, format_stats_line,
    STATUS_PENDING, STATUS_CONVERTING, STATUS_COMPILED,
    STATUS_DONE, STATUS_ARCHIVED, STATUS_FAILED, STATUS_DUPLICATE,
)
from compiler import compile_md, clean_format, inject_frontmatter
from engine import create_engine

_TZ_CN = timezone(timedelta(hours=8))
_should_stop = False


def _signal_handler(sig, frame):
    global _should_stop
    _should_stop = True
    print("\n⚠️ 收到中断信号，正在保存进度...")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── 初始化 ────────────────────────────────────────────────────────

def cmd_init(args):
    """初始化 vault 目录结构"""
    vault_root = args.vault or input("Vault 根目录路径: ").strip()
    if not vault_root:
        print("❌ 未指定 vault 路径")
        return

    dirs = [
        "raw/todo",
        "raw/09-archive",
        "wiki/sources",
        "wiki/concepts",
        "wiki/entities",
        "wiki/syntheses",
        "wiki/logs",
        "assets",
        "notes",
    ]

    print(f"\n📁 初始化 Vault: {vault_root}\n")
    for d in dirs:
        full = os.path.join(vault_root, d)
        os.makedirs(full, exist_ok=True)
        print(f"  ✅ {d}/")

    # 创建 index.md
    index_path = os.path.join(vault_root, "wiki", "index.md")
    if not os.path.exists(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# Wiki 索引\n\n> 自动生成，勿手动编辑\n\n## 概念 (Concepts)\n\n## 实体 (Entities)\n\n## 来源 (Sources)\n\n## 综合 (Syntheses)\n")
        print(f"  ✅ wiki/index.md")

    # 创建 SCHEMA.md（如果不存在）
    schema_path = os.path.join(vault_root, "SCHEMA.md")
    if not os.path.exists(schema_path):
        print(f"  ⚠️ SCHEMA.md 不存在，请手动创建或从模板复制")

    print(f"\n✅ 初始化完成！")
    print(f"\n下一步：")
    print(f"  1. 把 PDF/DOCX 放入 {vault_root}/raw/todo/")
    print(f"  2. 编辑 config.yaml 填入 MinerU Token 和 vault.root")
    print(f"  3. 运行: python cli.py compile --vault \"{vault_root}\"")


# ── 状态查看 ──────────────────────────────────────────────────────

def cmd_status(args):
    """查看处理状态"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    if not vault_root:
        print("❌ 未配置 vault.root")
        return

    ckpt = Checkpoint(vault_root)
    stats = ckpt.get_stats()

    print(f"\n📊 Vault 状态: {vault_root}")
    print(f"{'─' * 40}")
    print(format_progress(stats))
    print(f"  总计: {stats['total']}")
    print(f"  完成: {stats['done']}")
    print(f"  已归档: {stats['archived']}")
    print(f"  重复: {stats['duplicate']}")
    print(f"  转换中: {stats['converting']}")
    print(f"  编译中: {stats['compiled']}")
    print(f"  失败: {stats['failed']}")
    print(f"  待处理: {stats['pending']}")

    # 显示失败文件
    failed = ckpt.get_failed()
    if failed:
        print(f"\n❌ 失败文件 ({len(failed)}):")
        for f in failed[:10]:
            entry = ckpt.data["files"][f]
            print(f"  - {f}: {entry.get('error', '?')}")
        if len(failed) > 10:
            print(f"  ... 还有 {len(failed) - 10} 个")

    # 显示中断文件
    interrupted = ckpt.get_interrupted()
    if interrupted:
        print(f"\n⚠️ 上次中断 ({len(interrupted)}):")
        for f in interrupted:
            print(f"  - {f}")


# ── 编译（主流程）──────────────────────────────────────────────────

def cmd_compile(args):
    """转换 + 编译"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    if not vault_root:
        print("❌ 未配置 vault.root，请编辑 config.yaml")
        return

    source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])
    output_dir = resolve_vault_path(cfg, cfg["dirs"]["output"])
    archive_dir = resolve_vault_path(cfg, cfg["dirs"]["archive"])

    if not os.path.isdir(source_dir):
        print(f"❌ source 目录不存在: {source_dir}")
        print(f"   运行 'python cli.py init' 创建目录结构")
        return

    ckpt = Checkpoint(vault_root)
    dry_run = getattr(args, "dry_run", False)

    # 获取待处理文件
    pending = ckpt.get_pending_files(source_dir, recursive=True, compile_only=True)

    # 检查中断文件
    interrupted = ckpt.get_interrupted()
    if interrupted:
        print(f"⚠️ 发现 {len(interrupted)} 个上次中断的文件，将重新处理")
        for f in interrupted:
            ckpt.set_status(f, STATUS_PENDING)

    if not pending:
        print("✅ 没有待处理文件")
        return

    print(f"\n📋 待处理: {len(pending)} 个文件")
    print(f"   源目录: {source_dir}")
    print(f"   输出: {output_dir}")
    print(f"   模式: {'预览' if dry_run else cfg['compile']['mode']}")
    print()

    if dry_run:
        # 预览模式
        for i, rel_path in enumerate(pending[:20]):
            abs_path = os.path.join(vault_root, rel_path)
            print(f"  [{i+1}] {rel_path}")
        if len(pending) > 20:
            print(f"  ... 还有 {len(pending) - 20} 个")
        return

    # 创建引擎
    try:
        engine = create_engine(cfg)
        print(f"🔧 引擎: {engine.get_name()}")
    except Exception as e:
        print(f"❌ 引擎初始化失败: {e}")
        return

    # 处理文件
    success_count = 0
    fail_count = 0
    skip_count = 0
    save_interval = 10

    for i, rel_path in enumerate(pending):
        if _should_stop:
            print(f"\n⚠️ 用户中断，已保存进度 ({i}/{len(pending)})")
            ckpt.save()
            return

        abs_path = os.path.join(vault_root, rel_path)
        source_basename = os.path.basename(rel_path)
        ext = os.path.splitext(rel_path)[1].lower()

        # XLSX 直接用 openpyxl 转换，不走 MinerU
        if ext == ".xlsx":
            from xlsx_converter import convert_xlsx_to_markdown
            md_output = os.path.join(os.path.dirname(abs_path))  # 输出到同目录
            conv = convert_xlsx_to_markdown(abs_path, md_output, skip_if_exists=True)
            if conv.get("success"):
                abs_path_md = conv["markdown_path"]
                rel_path_md = os.path.splitext(rel_path)[0] + ".md"
                if conv.get("skipped"):
                    ckpt.mark_fingerprint(rel_path, "")
                    # MD 已存在，尝试编译
                    compile_result = compile_md(abs_path_md, abs_path, cfg)
                    if compile_result.get("success"):
                        if compile_result.get("skipped"):
                            ckpt.mark_duplicate(rel_path, compile_result.get("duplicate_of", ""))
                            skip_count += 1
                            print(f" ⏭ 重复")
                        else:
                            wiki_name = compile_result.get("wiki_name", "")
                            ckpt.mark_compiled(rel_path, abs_path_md, [wiki_name])
                            ckpt.mark_done(rel_path)
                            success_count += 1
                            print(f" ✅ → wiki/sources/{wiki_name}.md")
                    else:
                        ckpt.mark_failed(rel_path, compile_result.get("error", "编译失败"))
                        fail_count += 1
                        print(f" ❌ 编译失败")
                else:
                    print(f"\n  📊 XLSX → MD ({os.path.basename(abs_path_md)})")
                    ckpt.mark_fingerprint(rel_path, "")
                    compile_result = compile_md(abs_path_md, abs_path, cfg)
                    if compile_result.get("success"):
                        if compile_result.get("skipped"):
                            ckpt.mark_duplicate(rel_path, compile_result.get("duplicate_of", ""))
                            skip_count += 1
                            print(f" ⏭ 重复")
                        else:
                            wiki_name = compile_result.get("wiki_name", "")
                            ckpt.mark_compiled(rel_path, abs_path_md, [wiki_name])
                            ckpt.mark_done(rel_path)
                            success_count += 1
                            print(f" ✅ → wiki/sources/{wiki_name}.md")
                    else:
                        ckpt.mark_failed(rel_path, compile_result.get("error", "编译失败"))
                        fail_count += 1
                        print(f" ❌ 编译失败")
            else:
                ckpt.mark_failed(rel_path, conv.get("error", "XLSX 转换失败"))
                fail_count += 1
                print(f"\n  ❌ XLSX 转换失败: {conv.get('error', '?')}")
            if (i + 1) % save_interval == 0:
                ckpt.save()
            continue

        # 旧格式自动转换（DOC→DOCX, PPT→PPTX）
        if ext in (".doc", ".ppt"):
            from legacy_converter import convert_file as legacy_convert
            conv = legacy_convert(abs_path)
            if conv["success"]:
                abs_path = conv["output_path"]
                rel_path = os.path.splitext(rel_path)[0] + (".docx" if ext == ".doc" else ".pptx")
                if not conv.get("skipped"):
                    print(f"  🔄 {source_basename} → {os.path.basename(abs_path)}")
            else:
                ckpt.mark_failed(rel_path, conv["error"])
                fail_count += 1
                print(f"\n  ❌ 旧格式转换失败: {conv['error'][:80]}")
                continue

        # 更新状态
        ckpt.mark_converting(rel_path)
        stats = ckpt.get_stats()
        progress = format_progress(stats)
        print(f"\r{progress} [{i+1}/{len(pending)}] {source_basename}", end="", flush=True)

        # 转换
        try:
            result = engine.convert_file(abs_path, output_dir, skip_if_exists=True)
        except Exception as e:
            ckpt.mark_failed(rel_path, str(e))
            fail_count += 1
            print(f"\n  ❌ 转换失败: {e}")
            continue

        if result.get("skipped"):
            # 转换跳过（已有MD），但仍需编译
            md_path = os.path.splitext(abs_path)[0] + ".md"
            if not os.path.exists(md_path):
                ckpt.mark_failed(rel_path, "MD 文件不存在")
                fail_count += 1
                print(f"\n  ❌ MD 文件不存在: {md_path}")
                continue
            # 编译已有 MD
            from checkpoint import file_fingerprint
            ckpt.mark_fingerprint(rel_path, file_fingerprint(abs_path))
            compile_result = compile_md(md_path, abs_path, cfg)
            if compile_result.get("success"):
                if compile_result.get("skipped"):
                    ckpt.mark_duplicate(rel_path, compile_result.get("duplicate_of", ""))
                    skip_count += 1
                    print(f" ⏭ 重复")
                else:
                    wiki_name = compile_result.get("wiki_name", "")
                    ckpt.mark_compiled(rel_path, md_path, [wiki_name])
                    ckpt.mark_done(rel_path)
                    success_count += 1
                    print(f" ✅ → wiki/sources/{wiki_name}.md")
            else:
                ckpt.mark_failed(rel_path, compile_result.get("error", "编译失败"))
                fail_count += 1
                print(f"\n  ❌ 编译失败: {compile_result.get('error', '?')}")
            continue

        if not result.get("success"):
            ckpt.mark_failed(rel_path, result.get("error", "未知错误"))
            fail_count += 1
            print(f"\n  ❌ {result.get('error', '?')}")
            continue

        # 转换成功，开始编译
        md_path = result.get("markdown_path", "")
        if not md_path or not os.path.exists(md_path):
            ckpt.mark_failed(rel_path, "MD 文件未生成")
            fail_count += 1
            print(f"\n  ❌ MD 文件不存在")
            continue

        # 记录文件指纹
        from checkpoint import file_fingerprint
        ckpt.mark_fingerprint(rel_path, file_fingerprint(abs_path))

        # 编译
        compile_result = compile_md(md_path, abs_path, cfg)
        if compile_result.get("success"):
            if compile_result.get("skipped"):
                ckpt.mark_duplicate(rel_path, compile_result.get("duplicate_of", ""))
                skip_count += 1
                print(f" ⏭ 重复")
            else:
                wiki_name = compile_result.get("wiki_name", "")
                ckpt.mark_compiled(rel_path, md_path, [wiki_name])
                ckpt.mark_done(rel_path)
                success_count += 1
                print(f" ✅ → wiki/sources/{wiki_name}.md")
        else:
            ckpt.mark_failed(rel_path, compile_result.get("error", "编译失败"))
            fail_count += 1
            print(f"\n  ❌ 编译失败: {compile_result.get('error', '?')}")

        # 定期保存
        if (i + 1) % save_interval == 0:
            ckpt.save()

    # 最终保存
    ckpt.save()

    # 归档
    if cfg["automation"]["auto_migrate"] and archive_dir:
        _archive_processed(vault_root, output_dir, archive_dir, ckpt)

    # 结果
    stats = ckpt.get_stats()
    print(f"\n{'─' * 40}")
    print(format_progress(stats))
    print(f"  ✅ 成功: {success_count}  ⏭ 跳过: {skip_count}  ❌ 失败: {fail_count}")


# ── 恢复 ──────────────────────────────────────────────────────────

def cmd_resume(args):
    """恢复中断任务"""
    cmd_compile(args)


# ── 仅转换（不编译）──────────────────────────────────────────────

# ── 持久化队列 ─────────────────────────────────────────────────

def cmd_queue(args):
    """查看/管理持久化队列"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    state_file = os.path.join(vault_root, ".obsidian-ingest", "queue_state.json")

    action = getattr(args, "action", "status")
    if action == "status":
        # 先扫描注册新文件
        source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])
        from persistent_queue import PersistentQueue
        queue = PersistentQueue(state_file)
        new = queue.scan_and_register(source_dir)
        if new:
            print(f"📥 扫描新增 {new} 个任务")
        cmd_queue_status(state_file)
    elif action == "retry":
        cmd_queue_retry(state_file)
    elif action == "clear":
        cmd_queue_clear(state_file)


def cmd_batch(args):
    """分批处理队列"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])
    output_dir = resolve_vault_path(cfg, cfg["dirs"]["output"])
    state_file = os.path.join(vault_root, ".obsidian-ingest", "queue_state.json")

    from persistent_queue import PersistentQueue, BatchProcessor, build_registry, Status
    from file_queue import FileCategory

    # 扫描 + 注册
    queue = PersistentQueue(state_file)
    new = queue.scan_and_register(source_dir)
    if new:
        print(f"📥 扫描新增 {new} 个任务")

    pending = queue.get_pending()
    if not pending:
        print(queue.format_stats())
        print("✅ 没有待处理任务")
        return

    # 创建引擎
    try:
        engine = create_engine(cfg)
    except Exception as e:
        print(f"❌ 引擎初始化失败: {e}")
        return

    registry = build_registry(engine, output_dir, cfg)
    processor = BatchProcessor(queue, registry)

    batch_size = getattr(args, "size", 50)
    process_all = getattr(args, "all", False)

    print(f"🔧 引擎: {engine.get_name()}")
    print(queue.format_stats())

    def on_progress(task, stats, idx, total):
        icon = {"done": "✅", "failed": "❌", "skipped": "⏭"}.get(task.status, "?")
        print(f"  [{idx}/{total}] {icon} {os.path.basename(task.path)}")
        if task.error:
            print(f"         ↳ {task.error[:60]}")

    if process_all:
        result = processor.process_all(batch_size, on_progress)
    else:
        result = processor.process_batch(batch_size, on_progress)

    print(queue.format_stats())
    print(f"\n  本批: ✅ {result['done']}  ⏭ {result['skipped']}  ❌ {result['failed']}")


def cmd_heartbeat(args):
    """心跳守护：扫描 + 处理 + 循环，不终止"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])
    output_dir = resolve_vault_path(cfg, cfg["dirs"]["output"])
    state_file = os.path.join(vault_root, ".obsidian-ingest", "queue_state.json")

    interval = getattr(args, "interval", 300)
    batch_size = getattr(args, "batch_size", 50)

    from persistent_queue import PersistentQueue, BatchProcessor, build_registry

    queue = PersistentQueue(state_file)

    try:
        engine = create_engine(cfg)
        print(f"🔧 引擎: {engine.get_name()}")
    except Exception as e:
        print(f"❌ 引擎初始化失败: {e}")
        return

    registry = build_registry(engine, output_dir, cfg)
    processor = BatchProcessor(queue, registry)

    print(f"💓 心跳守护启动")
    print(f"   间隔: {interval}s  批大小: {batch_size}")
    print(f"   状态文件: {state_file}")
    print(f"   按 Ctrl+C 停止\n")

    round_num = 0
    while True:
        try:
            round_num += 1
            print(f"\n{'═' * 50}")
            print(f"💓 心跳 #{round_num} — {datetime.now().strftime('%H:%M:%S')}")

            # 1. 扫描新文件
            new = queue.scan_and_register(source_dir)
            if new:
                print(f"📥 扫描新增 {new} 个任务")

            # 2. 统计
            stats = queue.stats()
            pending_count = stats["by_status"].get("pending", 0)

            if pending_count == 0:
                print(f"✅ 队列已清空 ({stats['total']} 个任务) "
                      f"✅ {stats['by_status'].get('done', 0)} "
                      f"❌ {stats['by_status'].get('failed', 0)} "
                      f"⏭ {stats['by_status'].get('skipped', 0)}")
                print(f"⏳ 等待下一次心跳... ({interval}s)")
                time.sleep(interval)
                continue

            print(f"⏳ 待处理: {pending_count} 个")

            # 3. 处理一批
            def on_progress(task, s, idx, total):
                icon = {"done": "✅", "failed": "❌", "skipped": "⏭"}.get(task.status, "?")
                print(f"  [{idx}/{total}] {icon} {os.path.basename(task.path)}")
                if task.error:
                    print(f"         ↳ {task.error[:60]}")

            result = processor.process_batch(batch_size, on_progress)

            # 4. 汇报
            print(f"  本批: ✅ {result['done']}  ⏭ {result['skipped']}  ❌ {result['failed']}")
            print(queue.format_stats())

            # 5. 等待
            print(f"⏳ 等待下一次心跳... ({interval}s)")
            time.sleep(interval)

        except KeyboardInterrupt:
            print(f"\n\n🛑 心跳停止")
            queue.save()
            print(queue.format_stats())
            break
        except Exception as e:
            print(f"\n⚠️ 心跳异常: {e}")
            queue.save()
            time.sleep(interval)


def cmd_convert(args):
    """仅转换文件→MD，不编译 wiki 页面。使用分类队列路由"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])
    output_dir = resolve_vault_path(cfg, cfg["dirs"]["output"])

    if not os.path.isdir(source_dir):
        print(f"❌ source 目录不存在: {source_dir}")
        return

    # 扫描 + 分类
    from file_queue import scan_directory, build_default_registry, FileQueue
    items = scan_directory(source_dir, recursive=True, skip_existing_md=True)
    if not items:
        print("✅ 没有需要转换的文件")
        return

    dry_run = getattr(args, "dry_run", False)

    # 创建引擎
    try:
        engine = create_engine(cfg)
    except Exception as e:
        print(f"❌ 引擎初始化失败: {e}")
        return

    # 构建队列
    registry = build_default_registry(engine, output_dir, cfg)
    queue = FileQueue(items, registry)

    # 打印分类统计
    print(queue.summary())

    if dry_run:
        print("\n预览前 20 个:")
        for i, item in enumerate(items[:20]):
            print(f"  [{i+1}] [{item.category.value:12s}] {item.rel_path}")
        if len(items) > 20:
            print(f"  ... 还有 {len(items) - 20} 个")
        return

    # 进度回调
    def on_progress(item, stats):
        icon = {"done": "✅", "failed": "❌", "skipped": "⏭"}.get(item.status, "?")
        elapsed = f"{item.elapsed:.0f}s" if item.elapsed else ""
        print(f"  [{stats['done']+stats['failed']+stats['skipped']}/{stats['total']}] "
              f"{icon} {item.basename} ({item.converter}) {elapsed}")
        if item.error:
            print(f"         ↳ {item.error[:80]}")

    # 处理
    print(f"\n🔧 引擎: {engine.get_name()}")
    print(f"{'─' * 40}")
    stats = queue.process(max_workers=1, progress_callback=on_progress)

    # 结果
    result = queue.export_results()
    print(f"\n{'─' * 40}")
    print(f"  ✅ 成功: {result['done']}  ⏭ 跳过: {result['skipped']}  ❌ 失败: {result['failed']}")

    # 打印失败详情
    if result["failed_files"]:
        print(f"\n❌ 失败文件:")
        for f in result["failed_files"]:
            print(f"  [{f['category']}] {f['file']}: {f['error'][:60]}")


# ── 归档 ──────────────────────────────────────────────────────────

def cmd_migrate(args):
    """归档已处理的源文件（PDF/DOCX/PPTX → 09-archive）"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])
    output_dir = resolve_vault_path(cfg, cfg["dirs"]["output"])
    archive_dir = resolve_vault_path(cfg, cfg["dirs"]["archive"])
    retention = cfg.get("automation", {}).get("retention_days", 7)

    if not os.path.isdir(source_dir):
        print(f"❌ source 目录不存在: {source_dir}")
        return

    print(f"\n📦 归档扫描: {source_dir}")
    print(f"   → 归档到: {archive_dir}")
    print(f"   保留天数: {retention}")

    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        from migrator import has_md_sibling
        count = 0
        for fname in os.listdir(source_dir):
            fpath = os.path.join(source_dir, fname)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in (".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt",
                       ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif", ".jp2"):
                if has_md_sibling(fpath):
                    print(f"  📦 {fname} → 09-archive/")
                    count += 1
        if not count:
            print("  ✅ 没有可归档的文件")
        else:
            print(f"\n  共 {count} 个文件将被归档")
        return

    from migrator import migrate
    result = migrate(
        source_dir=source_dir,
        output_dir=output_dir,
        archive_dir=archive_dir,
        retention_days=retention,
    )

    if result.get("error"):
        print(f"❌ {result['error']}")
        return

    print(f"\n  ✅ 归档源文件: {result['moved_source']}")
    print(f"  ✅ 移动 MD: {result['moved_md']}")
    print(f"  ⏭ 跳过（无 MD）: {result['skipped']}")
    print(f"  🗑 过期删除: {result['deleted_old']}")
    if result["errors"]:
        print(f"  ⚠️ 错误: {len(result['errors'])}")
        for e in result["errors"][:5]:
            print(f"    - {e}")


# ── 清理 ──────────────────────────────────────────────────────────

def cmd_cleanup(args):
    """清理过期归档 + 重置失败记录"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    archive_dir = resolve_vault_path(cfg, cfg["dirs"]["archive"])
    retention = cfg.get("automation", {}).get("retention_days", 7)

    print("\n🧹 清理\n")

    # 1. 清理过期归档
    if os.path.isdir(archive_dir):
        from migrator import cleanup_archive
        result = cleanup_archive(archive_dir, retention_days=retention)
        if result.get("error"):
            print(f"  ⚠️ 归档清理: {result['error']}")
        else:
            print(f"  🗑 过期归档已删除: {result['deleted']} 个")
    else:
        print(f"  ℹ️ 归档目录不存在: {archive_dir}")

    # 2. 重置 checkpoint 中的失败记录
    ckpt = Checkpoint(vault_root)
    failed = ckpt.get_failed()
    reset = getattr(args, "reset_failed", False)
    if failed:
        if reset:
            for f in failed:
                ckpt.set_status(f, STATUS_PENDING)
            ckpt.save()
            print(f"  🔄 已重置 {len(failed)} 个失败文件为待处理")
        else:
            print(f"  ⚠️ {len(failed)} 个失败文件（加 --reset-failed 可重置）")
    else:
        print(f"  ✅ 无失败文件")

    print()


# ── 监听 ──────────────────────────────────────────────────────────

def cmd_watch(args):
    """监听目录，自动处理新文件"""
    cfg = _load_cfg(args)
    vault_root = cfg["vault"]["root"]
    source_dir = resolve_vault_path(cfg, cfg["dirs"]["source"])

    if not os.path.isdir(source_dir):
        print(f"❌ source 目录不存在: {source_dir}")
        return

    interval = cfg["watcher"]["poll_interval"]
    print(f"👁️ 监听模式启动: {source_dir}")
    print(f"   轮询间隔: {interval}s")
    print(f"   Ctrl+C 退出\n")

    ckpt = Checkpoint(vault_root)
    engine = create_engine(cfg)

    while not _should_stop:
        try:
            pending = ckpt.get_pending_files(source_dir, recursive=True, compile_only=True)
            if pending:
                print(f"\n📥 发现 {len(pending)} 个新文件")
                # 委托给 compile 处理
                args_copy = argparse.Namespace(**vars(args))
                cmd_compile(args_copy)
            time.sleep(interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️ 异常: {e}")
            time.sleep(interval * 2)

    print("\n👋 监听已停止")


# ── 归档 ──────────────────────────────────────────────────────────

def _archive_processed(vault_root: str, output_dir: str, archive_dir: str, ckpt: Checkpoint):
    """归档已完成的源文件"""
    import shutil

    moved = 0
    for rel_path, entry in list(ckpt.data["files"].items()):
        if entry.get("status") != STATUS_DONE:
            continue

        abs_path = os.path.join(vault_root, rel_path)
        if not os.path.exists(abs_path):
            continue

        # 只归档 PDF/DOCX 等源文件，不归档 MD
        ext = os.path.splitext(abs_path)[1].lower()
        if ext in (".md", ".txt"):
            continue

        dst = os.path.join(archive_dir, os.path.basename(abs_path))
        try:
            shutil.move(abs_path, dst)
            ckpt.mark_archived(rel_path)
            moved += 1
        except Exception as e:
            print(f"  ⚠️ 归档失败 {rel_path}: {e}")

    if moved:
        ckpt.save()
        print(f"📦 归档: {moved} 个文件 → {archive_dir}")


# ── 辅助 ──────────────────────────────────────────────────────────

def _load_cfg(args) -> dict:
    config_path = getattr(args, "config", None)
    cfg = load_config(config_path)

    # 命令行覆盖 vault
    vault_cli = getattr(args, "vault", None)
    if vault_cli:
        cfg["vault"]["root"] = vault_cli

    return cfg


# ── 主入口 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="obsidian-ingest — 知识库文档摄入引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--vault", default=None, help="Vault 根目录（覆盖配置）")

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="初始化 vault 目录结构")
    p_init.add_argument("--vault", default=None, help="Vault 根目录")

    # status
    sub.add_parser("status", help="查看处理状态")

    # compile
    p_compile = sub.add_parser("compile", help="转换 + 编译")
    p_compile.add_argument("--dry-run", action="store_true", help="预览不执行")

    # resume
    sub.add_parser("resume", help="恢复中断任务")

    # convert
    p_convert = sub.add_parser("convert", help="仅转换 PDF→MD（不编译 wiki）")
    p_convert.add_argument("--dry-run", action="store_true", help="预览不执行")

    # migrate
    p_migrate = sub.add_parser("migrate", help="归档已处理的源文件")
    p_migrate.add_argument("--dry-run", action="store_true", help="预览不执行")

    # cleanup
    p_cleanup = sub.add_parser("cleanup", help="清理过期归档 + 失败记录")
    p_cleanup.add_argument("--reset-failed", action="store_true", help="重置失败文件为待处理")

    # watch
    sub.add_parser("watch", help="监听目录自动处理")

    # ── 持久化队列 ──

    # queue
    p_queue = sub.add_parser("queue", help="持久化队列状态")
    p_queue.add_argument("action", choices=["status", "retry", "clear"],
                         default="status", nargs="?", help="操作")

    # batch
    p_batch = sub.add_parser("batch", help="分批处理队列")
    p_batch.add_argument("--size", type=int, default=50, help="每批大小")
    p_batch.add_argument("--all", action="store_true", help="处理所有（多批次）")

    # heartbeat
    p_hb = sub.add_parser("heartbeat", help="心跳守护（扫描+处理+循环）")
    p_hb.add_argument("--interval", type=int, default=300, help="心跳间隔（秒）")
    p_hb.add_argument("--batch-size", type=int, default=50, help="每批大小")

    args = parser.parse_args()

    cmds = {
        "init": cmd_init,
        "status": cmd_status,
        "compile": cmd_compile,
        "resume": cmd_resume,
        "convert": cmd_convert,
        "migrate": cmd_migrate,
        "cleanup": cmd_cleanup,
        "watch": cmd_watch,
        "queue": cmd_queue,
        "batch": cmd_batch,
        "heartbeat": cmd_heartbeat,
    }
    handler = cmds.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
