"""
anomaly_check.py — 编译队列和转换队列异常检测

只读检查，不修改任何文件，不发送任何消息。
由 cron job 调用：cron delivery 根据返回结果决定是否推送通知。

用法：
  python anomaly_check.py compile --vault /path/to/vault [--config /path/to/config.yaml]
  python anomaly_check.py convert --vault /path/to/vault [--config /path/to/config.yaml]
  python anomaly_check.py all     --vault /path/to/vault [--config /path/to/config.yaml]

返回 JSON（exit code 0 = 正常，1 = 异常）：
  {"anomaly": true/false, "level": "critical|warning|normal", "message": "...", "stats": {...}}
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 默认阈值（config.yaml 中 monitoring 段可以覆盖） ──
DEFAULT_THRESHOLDS = {
    "compile": {
        "stuck_pending_threshold": 500,
        "stuck_minutes": 30,
        "backlog_threshold": 2000,
        "alert_failed_threshold": 0,
    },
    "convert": {
        "stuck_pending_threshold": 100,
        "stuck_minutes": 60,
        "backlog_threshold": 500,
        "alert_failed_threshold": 0,
    },
}

# ── 帮助函数 ──
def load_yaml_config(config_path: str) -> dict:
    """加载 YAML 配置文件，返回 monitoring 段（如果有的话）"""
    try:
        import yaml
    except ImportError:
        return {}

    if not os.path.exists(config_path):
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config.get("monitoring", {})


def parse_iso_timestamp(ts_str: str):
    """解析 ISO 时间戳字符串，返回 aware datetime 或 None"""
    if not ts_str:
        return None
    try:
        # Python 3.7+ fromisoformat handles timezone offsets
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        # 尝试手动词添加时区
        try:
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            if "+" in ts_str or ts_str.endswith("Z"):
                return datetime.fromisoformat(ts_str)
            # 假设为 UTC+8 (Asia/Shanghai)
            return dt.replace(tzinfo=timezone(timedelta(hours=8)))
        except (ValueError, TypeError):
            return None


def minutes_since(ts_str: str) -> float:
    """计算距当前时间的分钟数"""
    dt = parse_iso_timestamp(ts_str)
    if dt is None:
        return float("inf")  # 无法解析，默认很久

    now = datetime.now(timezone.utc).astimezone()
    delta = now - dt
    return delta.total_seconds() / 60


# ── 静默期检查 ──
def is_muted(monitoring_config: dict) -> bool:
    """检查是否在静默期内"""
    muted_until_str = monitoring_config.get("muted_until")
    if not muted_until_str:
        return False
    dt = parse_iso_timestamp(muted_until_str)
    if dt is None:
        return False
    now = datetime.now(timezone.utc).astimezone()
    return now < dt


def apply_muted(results: list, monitoring_config: dict) -> list:
    """如果在静默期内，将所有结果标记为正常"""
    if not is_muted(monitoring_config):
        return results
    muted_until = monitoring_config.get("muted_until", "")
    for r in results:
        r["anomaly"] = False
        r["level"] = "normal_muted"
        r["message"] = f"🔇 静默期（至 {muted_until}）：{r['message']}"
    return results


# ── 队列检查函数 ──
def _find_queue_file(vault_root: str, filename: str) -> str:
    """查找队列文件路径（兼容 .obsidian-ingest/ 子目录和 vault 根目录）"""
    # 优先检查 .obsidian-ingest/ 子目录（compile_queue.py 的默认位置）
    hidden_dir = os.path.join(vault_root, ".obsidian-ingest", filename)
    if os.path.exists(hidden_dir):
        return hidden_dir
    # 兜底检查 vault 根目录
    root_path = os.path.join(vault_root, filename)
    return root_path


def check_compile_queue(vault_root: str, thresholds: dict) -> dict:
    """检查编译队列 (compile_queue.json)"""

    queue_path = _find_queue_file(vault_root, "compile_queue.json")
    if not os.path.exists(queue_path):
        return {
            "anomaly": True,
            "level": "warning",
            "message": "⚠️ 编译队列文件不存在（可能系统未初始化）",
            "stats": None,
            "queue_type": "compile",
        }

    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return {
            "anomaly": True,
            "level": "critical",
            "message": f"❌ 编译队列文件损坏：{e}",
            "stats": None,
            "queue_type": "compile",
        }

    stats = queue.get("stats", {})
    pending = stats.get("pending", 0)
    processing = stats.get("processing", 0)
    done = stats.get("done", 0)
    failed = stats.get("failed", 0)
    skipped = stats.get("skipped", 0)

    last_progress_str = queue.get("last_progress", "")
    last_scan_str = queue.get("last_scan", "")

    # ── 检查失败任务（最高优先级） ──
    alert_failed = thresholds.get("alert_failed_threshold", 0)
    if failed > alert_failed:
        return {
            "anomaly": True,
            "level": "critical",
            "message": f"❌ 编译队列：{failed} 个任务失败\n{pending} 待处理 · {processing} 处理中 · {done} 已完成 · {skipped} 跳过",
            "stats": {
                "pending": pending,
                "processing": processing,
                "done": done,
                "failed": failed,
                "skipped": skipped,
                "last_progress": last_progress_str,
                "last_scan": last_scan_str,
            },
            "queue_type": "compile",
        }

    # ── 检查卡住（pending 多 + processing = 0 + 超时无进展） ──
    stuck_threshold = thresholds.get("stuck_pending_threshold", 500)
    stuck_min = thresholds.get("stuck_minutes", 30)

    if pending > stuck_threshold and processing == 0:
        mins_since_progress = minutes_since(last_progress_str)
        if mins_since_progress > stuck_min:
            return {
                "anomaly": True,
                "level": "warning",
                "message": f"⚠️ 编译队列可能卡住：{pending} 待处理，{processing} 处理中，{mins_since_progress:.0f} 分钟无进展\n上次扫描：{last_scan_str}",
                "stats": {
                    "pending": pending,
                    "processing": processing,
                    "done": done,
                    "failed": failed,
                    "skipped": skipped,
                    "minutes_stuck": round(mins_since_progress, 1),
                    "last_progress": last_progress_str,
                    "last_scan": last_scan_str,
                },
                "queue_type": "compile",
            }

    # ── 检查堆积 ──
    backlog_threshold = thresholds.get("backlog_threshold", 2000)
    if pending > backlog_threshold:
        return {
            "anomaly": True,
            "level": "warning",
            "message": f"⚠️ 编译队列堆积严重：{pending} 待处理，{processing} 处理中\n{done} 已完成 · {failed} 失败 · {skipped} 跳过",
            "stats": {
                "pending": pending,
                "processing": processing,
                "done": done,
                "failed": failed,
                "skipped": skipped,
                "last_progress": last_progress_str,
                "last_scan": last_scan_str,
            },
            "queue_type": "compile",
        }

    # ── 正常 ──
    return {
        "anomaly": False,
        "level": "normal",
        "message": f"✅ 编译队列正常：{pending} 待处理 · {processing} 处理中 · {done} 已完成 · {failed} 失败 · {skipped} 跳过",
        "stats": {
            "pending": pending,
            "processing": processing,
            "done": done,
            "failed": failed,
            "skipped": skipped,
            "last_progress": last_progress_str,
            "last_scan": last_scan_str,
        },
        "queue_type": "compile",
    }


def check_convert_queue(vault_root: str, thresholds: dict) -> dict:
    """检查转换队列 (queue_state.json，转化阶段的持久化队列)"""

    queue_path = _find_queue_file(vault_root, "queue_state.json")
    if not os.path.exists(queue_path):
        return {
            "anomaly": True,
            "level": "warning",
            "message": "⚠️ 转换队列文件不存在（可能系统未初始化）",
            "stats": None,
            "queue_type": "convert",
        }

    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return {
            "anomaly": True,
            "level": "critical",
            "message": f"❌ 转换队列文件损坏：{e}",
            "stats": None,
            "queue_type": "convert",
        }

    # queue_state.json 结构因持久化队列而异，这里尝试兼容两种格式
    # 格式 1：直接有 stats 字段
    # 格式 2：tasks 数组 + 汇总统计字段
    stats = queue.get("stats", {})

    if not stats:
        # 尝试从 tasks 自己统计
        tasks = queue.get("tasks", [])
        pending = sum(1 for t in tasks if t.get("status") == "pending")
        processing = sum(1 for t in tasks if t.get("status") == "processing")
        done = sum(1 for t in tasks if t.get("status") == "done")
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        skipped = sum(1 for t in tasks if t.get("status") == "skipped")
    else:
        pending = stats.get("pending", 0)
        processing = stats.get("processing", 0)
        done = stats.get("done", 0)
        failed = stats.get("failed", 0)
        skipped = stats.get("skipped", 0)

    # 获取时间戳
    last_progress = queue.get("last_progress", "") or queue.get("updated_at", "")
    last_scan = queue.get("last_scan", "") or queue.get("created_at", "")

    # ── 检查失败 ──
    alert_failed = thresholds.get("alert_failed_threshold", 0)
    if failed > alert_failed:
        return {
            "anomaly": True,
            "level": "critical",
            "message": f"❌ 转换队列：{failed} 个任务失败\n{pending} 待处理 · {processing} 处理中 · {done} 已完成",
            "stats": {
                "pending": pending,
                "processing": processing,
                "done": done,
                "failed": failed,
                "skipped": skipped,
            },
            "queue_type": "convert",
        }

    # ── 检查卡住 ──
    stuck_threshold = thresholds.get("stuck_pending_threshold", 100)
    stuck_min = thresholds.get("stuck_minutes", 60)
    if pending > stuck_threshold and processing == 0:
        mins_since_progress = minutes_since(last_progress)
        if mins_since_progress > stuck_min:
            return {
                "anomaly": True,
                "level": "warning",
                "message": f"⚠️ 转换队列可能卡住：{pending} 待处理，{processing} 处理中，{mins_since_progress:.0f} 分钟无进展",
                "stats": {
                    "pending": pending,
                    "processing": processing,
                    "done": done,
                    "failed": failed,
                    "skipped": skipped,
                    "minutes_stuck": round(mins_since_progress, 1),
                },
                "queue_type": "convert",
            }

    # ── 检查堆积 ──
    backlog_threshold = thresholds.get("backlog_threshold", 500)
    if pending > backlog_threshold:
        return {
            "anomaly": True,
            "level": "warning",
            "message": f"⚠️ 转换队列堆积：{pending} 待处理，{processing} 处理中\n{done} 已完成 · {failed} 失败",
            "stats": {
                "pending": pending,
                "processing": processing,
                "done": done,
                "failed": failed,
                "skipped": skipped,
            },
            "queue_type": "convert",
        }

    # ── 正常 ──
    return {
        "anomaly": False,
        "level": "normal",
        "message": f"✅ 转换队列正常：{pending} 待处理 · {processing} 处理中 · {done} 已完成 · {failed} 失败",
        "stats": {
            "pending": pending,
            "processing": processing,
            "done": done,
            "failed": failed,
            "skipped": skipped,
        },
        "queue_type": "convert",
    }


# ── 主入口 ──
def main():
    parser = argparse.ArgumentParser(
        description="obsidian-ingest 队列异常检测（只读，不修改任何文件）"
    )
    parser.add_argument(
        "queue_type",
        choices=["compile", "convert", "all"],
        help="检查哪个队列",
    )
    parser.add_argument(
        "--vault", "-v",
        required=True,
        help="Vault 根目录路径（例如 /path/to/vault）",
    )
    parser.add_argument(
        "--config", "-c",
        help="配置文件路径（YAML，可选）。默认读取 vault 根目录下的 config.yaml",
    )

    args = parser.parse_args()

    vault_root = args.vault.rstrip("/\\")
    if not os.path.isdir(vault_root):
        json.dump(
            {
                "anomaly": True,
                "level": "critical",
                "message": f"❌ Vault 目录不存在：{vault_root}",
                "stats": None,
            },
            sys.stdout,
            ensure_ascii=False,
        )
        print()
        sys.exit(1)

    # 加载配置
    config_path = args.config
    if not config_path:
        # 自动查找 local/config.yaml 或 config.yaml
        local_config = os.path.join(vault_root, "local", "config.yaml")
        default_config = os.path.join(vault_root, "config.yaml")

        # 优先使用 skill 内的 local/config.yaml，其次 vault 下的
        skill_local = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "local", "config.yaml"
        )
        skill_config = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config.yaml"
        )

        for candidate in [skill_local, skill_config, local_config, default_config]:
            if os.path.exists(candidate):
                config_path = candidate
                break

    monitoring_config = load_yaml_config(config_path) if config_path else {}

    results = []

    if args.queue_type in ("compile", "all"):
        t = {**DEFAULT_THRESHOLDS["compile"], **monitoring_config.get("compile", {})}
        results.append(check_compile_queue(vault_root, t))

    if args.queue_type in ("convert", "all"):
        t = {**DEFAULT_THRESHOLDS["convert"], **monitoring_config.get("convert", {})}
        results.append(check_convert_queue(vault_root, t))

    # 静默期检查
    results = apply_muted(results, monitoring_config)

    # 输出 JSON
    if len(results) == 1:
        output = results[0]
    else:
        # all 模式：合并两个结果
        has_anomaly = any(r["anomaly"] for r in results)
        worst_level = "normal"
        for r in results:
            if r["level"] == "critical":
                worst_level = "critical"
                break
            elif r["level"] == "warning":
                worst_level = "warning"

        messages = [r["message"] for r in results if r["anomaly"] or args.queue_type == "all"]
        output = {
            "anomaly": has_anomaly,
            "level": worst_level,
            "message": "\n".join(messages) if messages else "✅ 所有队列正常",
            "stats": [r["stats"] for r in results],
            "details": results,
        }

    json.dump(output, sys.stdout, ensure_ascii=True, indent=2)
    print()

    # exit code: 0 = 正常，1 = 异常
    sys.exit(1 if output["anomaly"] else 0)


if __name__ == "__main__":
    main()

