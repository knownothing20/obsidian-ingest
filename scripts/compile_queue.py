"""
compile_queue.py - 编译队列管理

独立于 persistent_queue.py（PDF→MD 转换），专门追踪 MD→wiki 编译任务。

数据存储：compile_queue.json
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from enum import Enum


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class CompileTask:
    """编译任务"""
    status: str = "pending"
    size: int = 0
    category: str = ""  # pdf_md, office_md, etc.
    registered_at: str = ""
    updated_at: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    wiki_path: Optional[str] = None  # 生成的 wiki 页面路径


class CompileQueue:
    """编译队列管理器"""
    
    DEFAULT_PATH = ".obsidian-ingest/compile_queue.json"
    
    def __init__(self, queue_file: str = "", vault_root: str = ""):
        self.vault_root = vault_root
        self.queue_file = queue_file or os.path.join(vault_root, self.DEFAULT_PATH) if vault_root else ""
        self.data = {
            "version": 1,
            "vault_root": vault_root,
            "last_scan": "",
            "last_progress": "",
            "stats": {
                "pending": 0,
                "processing": 0,
                "done": 0,
                "failed": 0,
                "skipped": 0
            },
            "tasks": {}
        }
        self._ensure_dir()
        self._load()
    
    def _ensure_dir(self):
        if self.queue_file:
            os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
    
    def _load(self):
        """加载队列状态"""
        if self.queue_file and os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"[WARN] 加载队列失败: {e}")
    
    def save(self):
        """保存队列状态"""
        if self.queue_file:
            tmp = self.queue_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.queue_file)
    
    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    
    def _recalc_stats(self):
        """重新计算统计"""
        stats = {"pending": 0, "processing": 0, "done": 0, "failed": 0, "skipped": 0}
        for task in self.data["tasks"].values():
            s = task.get("status", "pending")
            if s in stats:
                stats[s] += 1
        self.data["stats"] = stats
        return stats
    
    # ── 扫描 & 注册 ─────────────────────────────────────────────
    
    def scan(self, todo_dir: str, exclude_dirs: list[str] = None) -> int:
        """
        扫描 todo/ 目录，注册所有 MD 文件到队列
        
        Args:
            todo_dir: todo 目录路径
            exclude_dirs: 排除的目录列表
            
        Returns:
            新增任务数量
        """
        if not os.path.isdir(todo_dir):
            print(f"[ERROR] 目录不存在: {todo_dir}")
            return 0
        
        exclude_set = set()
        if exclude_dirs:
            for d in exclude_dirs:
                d = d.strip("/\\").replace("\\", "/").lower()
                if d:
                    exclude_set.add(d)
        
        new_count = 0
        now = self._now()
        
        for root, dirs, files in os.walk(todo_dir):
            # 过滤排除目录
            dirs[:] = [d for d in dirs if d.lower() not in exclude_set]
            
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, todo_dir)  # 不包含 todo/ 前缀
                
                # 检查是否已注册（非 failed 状态）
                if rel_path in self.data["tasks"]:
                    existing = self.data["tasks"][rel_path]
                    if existing.get("status") != "failed":
                        continue  # 已处理，跳过
                
                # 获取文件大小
                size = os.path.getsize(fpath)
                
                # 自动跳过空文件（<100 bytes）
                if size < 100:
                    self.data["tasks"][rel_path] = {
                        "status": "skipped",
                        "size": size,
                        "category": "tiny",
                        "registered_at": now,
                        "updated_at": now,
                        "attempts": 0,
                        "last_error": "文件太小（<100 bytes），自动跳过"
                    }
                    new_count += 1
                    continue
                
                # 注册新任务
                self.data["tasks"][rel_path] = {
                    "status": "pending",
                    "size": size,
                    "category": "pdf_md",  # todo/ 里的都是转换后的 MD
                    "registered_at": now,
                    "updated_at": None,
                    "attempts": 0,
                    "last_error": None
                }
                new_count += 1
        
        self.data["last_scan"] = now
        self._recalc_stats()
        self.save()
        
        return new_count
    
    # ── 任务操作 ─────────────────────────────────────────────
    
    def get_pending(self, limit: int = 50) -> list[str]:
        """获取待处理任务列表"""
        pending = []
        for rel_path, task in self.data["tasks"].items():
            if task.get("status") == "pending":
                pending.append(rel_path)
                if len(pending) >= limit:
                    break
        return pending
    
    def start(self, rel_path: str) -> bool:
        """标记任务开始处理"""
        if rel_path not in self.data["tasks"]:
            return False
        
        self.data["tasks"][rel_path]["status"] = "processing"
        self.data["tasks"][rel_path]["updated_at"] = self._now()
        self.data["last_progress"] = self._now()
        self._recalc_stats()
        self.save()
        return True
    
    def done(self, rel_path: str, wiki_path: str = "") -> bool:
        """标记任务完成"""
        if rel_path not in self.data["tasks"]:
            return False
        
        self.data["tasks"][rel_path]["status"] = "done"
        self.data["tasks"][rel_path]["updated_at"] = self._now()
        self.data["tasks"][rel_path]["wiki_path"] = wiki_path
        self.data["last_progress"] = self._now()
        self._recalc_stats()
        self.save()
        return True
    
    def fail(self, rel_path: str, error: str = "") -> bool:
        """标记任务失败"""
        if rel_path not in self.data["tasks"]:
            return False
        
        task = self.data["tasks"][rel_path]
        task["status"] = "failed"
        task["attempts"] = task.get("attempts", 0) + 1
        task["updated_at"] = self._now()
        task["last_error"] = error
        self.data["last_progress"] = self._now()
        self._recalc_stats()
        self.save()
        return True
    
    def skip(self, rel_path: str, reason: str = "") -> bool:
        """标记任务跳过"""
        if rel_path not in self.data["tasks"]:
            return False
        
        self.data["tasks"][rel_path]["status"] = "skipped"
        self.data["tasks"][rel_path]["updated_at"] = self._now()
        self.data["tasks"][rel_path]["last_error"] = reason
        self._recalc_stats()
        self.save()
        return True
    
    def retry(self) -> int:
        """重置所有失败任务"""
        count = 0
        for task in self.data["tasks"].values():
            if task.get("status") == "failed":
                task["status"] = "pending"
                task["attempts"] = 0
                task["last_error"] = None
                task["updated_at"] = self._now()
                count += 1
        self._recalc_stats()
        self.save()
        return count
    
    # ── 查询 ─────────────────────────────────────────────
    
    def status(self) -> dict:
        """获取队列状态"""
        return {
            "pending": self.data["stats"]["pending"],
            "processing": self.data["stats"]["processing"],
            "done": self.data["stats"]["done"],
            "failed": self.data["stats"]["failed"],
            "skipped": self.data["stats"]["skipped"],
            "total": len(self.data["tasks"]),
            "last_scan": self.data.get("last_scan", ""),
            "last_progress": self.data.get("last_progress", "")
        }
    
    def get_task(self, rel_path: str) -> Optional[dict]:
        """获取任务详情"""
        return self.data["tasks"].get(rel_path)
    
    def format_status(self) -> str:
        """格式化状态输出"""
        s = self.status()
        lines = [
            "📊 编译队列状态",
            "─" * 40,
            f"  待处理 (pending):    {s['pending']}",
            f"  处理中 (processing): {s['processing']}",
            f"  已完成 (done):       {s['done']}",
            f"  失败 (failed):       {s['failed']}",
            f"  跳过 (skipped):      {s['skipped']}",
            "─" * 40,
            f"  总计: {s['total']}",
            f"",
            f"  上次扫描: {s['last_scan'] or '未扫描'}",
            f"  上次进度: {s['last_progress'] or '无'}",
        ]
        return "\n".join(lines)


# ── CLI 入口 ─────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="编译队列管理")
    parser.add_argument("--vault", "-v", help="Vault 根目录")
    parser.add_argument("--limit", "-l", type=int, default=50, help="获取任务数量限制")
    
    sub = parser.add_subparsers(dest="cmd", help="子命令")
    
    # scan
    sub.add_parser("scan", help="扫描 todo/ 注册新任务")
    
    # status
    sub.add_parser("status", help="查看队列状态")
    
    # pending
    sub.add_parser("pending", help="获取待处理任务")
    
    # retry
    sub.add_parser("retry", help="重置失败任务")
    
    # stats
    sub.add_parser("stats", help="统计摘要")
    
    # start
    p_start = sub.add_parser("start", help="标记任务开始处理")
    p_start.add_argument("path", help="任务相对路径")
    
    # done
    p_done = sub.add_parser("done", help="标记任务完成")
    p_done.add_argument("path", help="任务相对路径")
    p_done.add_argument("--wiki", "-w", default="", help="生成的 wiki 页面路径")
    
    # fail
    p_fail = sub.add_parser("fail", help="标记任务失败")
    p_fail.add_argument("path", help="任务相对路径")
    p_fail.add_argument("--reason", "-r", default="", help="失败原因")
    
    # skip
    p_skip = sub.add_parser("skip", help="标记任务跳过")
    p_skip.add_argument("path", help="任务相对路径")
    p_skip.add_argument("--reason", "-r", default="", help="跳过原因")
    
    args = parser.parse_args()
    
    vault = args.vault or os.environ.get("OBSIDIAN_VAULT")
    if not vault:
        print("[ERROR] 未指定 vault 路径（--vault 或 OBSIDIAN_VAULT）")
        return
    
    # 加载配置获取目录
    config_file = os.path.join(os.path.dirname(__file__), "..", "local", "config.yaml")
    dirs = {"todo": "todo", "exclude": []}
    
    if os.path.exists(config_file):
        import yaml
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                dirs = cfg.get("dirs", {})
        except:
            pass
    
    todo_dir = os.path.join(vault, dirs.get("todo", "todo"))
    queue = CompileQueue(vault_root=vault)
    
    if args.cmd == "scan":
        new = queue.scan(todo_dir, exclude_dirs=dirs.get("exclude", []))
        print(f"✅ 扫描完成，新增 {new} 个任务")
        print(queue.format_status())
    
    elif args.cmd == "status" or not args.cmd:
        print(queue.format_status())
    
    elif args.cmd == "pending":
        tasks = queue.get_pending(limit=args.limit)
        print(f"📋 待处理任务（前 {len(tasks)} 个）：")
        for i, t in enumerate(tasks, 1):
            print(f"  {i}. {t}")
    
    elif args.cmd == "retry":
        count = queue.retry()
        print(f"✅ 已重置 {count} 个失败任务")
    
    elif args.cmd == "start":
        ok = queue.start(args.path)
        if ok:
            print(f"✅ 已标记开始: {args.path}")
        else:
            print(f"❌ 任务不存在: {args.path}")
    
    elif args.cmd == "done":
        ok = queue.done(args.path, wiki_path=args.wiki)
        if ok:
            print(f"✅ 已标记完成: {args.path}")
        else:
            print(f"❌ 任务不存在: {args.path}")
    
    elif args.cmd == "fail":
        ok = queue.fail(args.path, error=args.reason)
        if ok:
            print(f"✅ 已标记失败: {args.path}")
        else:
            print(f"❌ 任务不存在: {args.path}")
    
    elif args.cmd == "skip":
        ok = queue.skip(args.path, reason=args.reason)
        if ok:
            print(f"✅ 已标记跳过: {args.path}")
        else:
            print(f"❌ 任务不存在: {args.path}")
    
    elif args.cmd == "stats":
        s = queue.status()
        print(f"📊 统计：")
        print(f"  pending:     {s['pending']}")
        print(f"  processing:  {s['processing']}")
        print(f"  done:        {s['done']}")
        print(f"  failed:      {s['failed']}")
        print(f"  skipped:     {s['skipped']}")
        print(f"  ─────────")
        print(f"  total:       {s['total']}")


if __name__ == "__main__":
    main()