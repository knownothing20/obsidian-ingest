"""
mineru2md - MinerU PDF/DOCX/PPTX → Markdown 转换 SDK
多 Token 自动故障转移 + 并行处理 + 进度条
"""

import sys
import io

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
import os
import time
import zipfile
import tempfile
import glob as glob_mod
import requests
from dataclasses import dataclass
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from config_loader import (
    load_config,
    seconds_until,
    format_countdown,
    build_expiry_info,
)


# ── Token 槽位 ────────────────────────────────────────────────────

@dataclass
class TokenSlot:
    token: str
    expires: str = ""
    exhausted: bool = False
    cooldown_until: float = 0.0

    def is_available(self) -> bool:
        if self.exhausted and time.time() < self.cooldown_until:
            return False
        if seconds_until(self.expires) <= 0:
            return False
        return True


# ── 客户端 ────────────────────────────────────────────────────────

class MinerUClient:
    def __init__(self, config=None, token_cooldown: int = 300, pool_retry_wait: int = 60):
        if isinstance(config, dict):
            self.cfg = config
        else:
            self.cfg = load_config(config)
        api = self.cfg["api"]
        self.base_url = api["base_url"]
        self.agent_url = f"{self.base_url}/api/v1/agent"
        self.timeout = api["timeout"]
        self.poll_interval = api["poll_interval"]
        self.max_poll_interval = api["max_poll_interval"]
        self.verify_ssl = api["verify_ssl"]
        self.token_cooldown = token_cooldown
        self.pool_retry_wait = pool_retry_wait
        self._pool: List[TokenSlot] = [TokenSlot(t["token"], t.get("expires", "")) for t in self.cfg["tokens"]]
        self._idx = -1

    # ── Token 管理 ────────────────────────────────────────────────

    def _next_token(self) -> Optional[str]:
        for _ in range(len(self._pool)):
            self._idx = (self._idx + 1) % len(self._pool)
            if self._pool[self._idx].is_available():
                return self._pool[self._idx].token
        return None

    def _mark_exhausted(self, token: str, wait: int):
        for s in self._pool:
            if s.token == token:
                s.exhausted = True
                s.cooldown_until = time.time() + wait
                return

    def _release_cooled(self):
        for s in self._pool:
            if s.exhausted and time.time() >= s.cooldown_until:
                s.exhausted = False

    def _should_retry(self, result: dict) -> bool:
        code = result.get("code", 0)
        msg = str(result.get("msg", ""))
        return code in (-60009, -60018) or "429" in msg or code in ("A0202", "A0211")

    def get_token_status(self) -> dict:
        self._release_cooled()
        tokens = []
        for i, s in enumerate(self._pool):
            remaining = max(0, int(s.cooldown_until - time.time())) if s.exhausted else 0
            tokens.append({"index": i + 1, "available": s.is_available(), "cooling_seconds": remaining})
        return {"tokens_count": len(self._pool), "tokens": tokens, "token_expiry": build_expiry_info(self.cfg["tokens"])}

    # ── HTTP 请求 ─────────────────────────────────────────────────

    def _auth(self, token: str) -> dict:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    def _req(self, method: str, url: str, retries: int = 3, **kw) -> requests.Response:
        kw.setdefault("verify", self.verify_ssl)
        last = None
        for i in range(retries):
            try:
                return getattr(requests, method)(url, **kw)
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last = e
                if i < retries - 1:
                    time.sleep(3 * (i + 1))
        raise last

    def _enrich(self, r: dict) -> dict:
        r["token_expiry"] = build_expiry_info(self.cfg["tokens"])
        return r

    # ── 轮询 ─────────────────────────────────────────────────────

    def _poll(self, task_id: str, token: str, agent: bool = False) -> dict:
        interval = self.poll_interval
        start = time.time()
        retries = 0
        while time.time() - start < self.timeout:
            self._release_cooled()
            try:
                if agent:
                    resp = self._req("get", f"{self.agent_url}/parse/{task_id}", timeout=15)
                else:
                    resp = self._req("get", f"{self.base_url}/api/v4/extract/task/{task_id}", headers=self._auth(token), timeout=15)
                data = resp.json()
                retries = 0
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                retries += 1
                if retries > 3:
                    return self._enrich({"code": -1, "msg": f"轮询网络错误: {e}"})
                time.sleep(5 * retries)
                continue

            state = data.get("data", {}).get("state", "")
            if state == "done":
                return self._enrich(data)
            if state == "failed":
                return self._enrich({"code": -1, "msg": f"Task failed: {data.get('data', {}).get('err_msg', '?')}", "data": data.get("data")})

            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] {state}...")
            time.sleep(interval)
            interval = min(interval * 1.5, self.max_poll_interval)

            if not self._next_token():
                print(f"  所有 Token 冷却中，等待 {self.pool_retry_wait}s...")
                time.sleep(self.pool_retry_wait)
                self._release_cooled()

        raise TimeoutError(f"轮询超时 {self.timeout}s, task={task_id}")

    def _poll_batch(self, batch_id: str, token: str, poll_timeout: int = 300) -> dict:
        interval = self.poll_interval
        start = time.time()
        retries = 0
        last = None
        while time.time() - start < poll_timeout:
            try:
                resp = self._req("get", f"{self.base_url}/api/v4/extract-results/batch/{batch_id}", headers=self._auth(token), timeout=15)
                result = resp.json()
                last = result
                retries = 0
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                retries += 1
                if retries > 3:
                    return self._enrich({"code": -1, "msg": f"轮询网络错误: {e}"})
                time.sleep(5 * retries)
                continue

            states = [r.get("state") for r in result.get("data", {}).get("extract_result", [])]
            if all(s == "done" for s in states):
                return self._enrich(result)
            if any(s == "failed" for s in states):
                return self._enrich(result)

            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] 批量进度: {states.count('done')}/{len(states)} done")
            time.sleep(interval)
            interval = min(interval * 1.5, self.max_poll_interval)

        return self._enrich({"code": -1, "msg": "批量轮询超时", "data": last.get("data") if last else {}})

    # ── 提交 + 自动切换 ──────────────────────────────────────────

    def _submit_and_poll(self, submit_fn) -> dict:
        self._release_cooled()
        token = self._next_token()
        if not token:
            raise RuntimeError("所有 Token 均在冷却中")
        result = submit_fn(token)
        if self._should_retry(result):
            self._mark_exhausted(token, self.token_cooldown)
            new = self._next_token()
            if not new:
                raise RuntimeError("多个 Token 均触发限流")
            result = submit_fn(new)
            if self._should_retry(result):
                self._mark_exhausted(new, self.token_cooldown)
                raise RuntimeError("所有 Token 均触发限流")
        if result.get("code") != 0:
            return self._enrich({"code": result.get("code", -1), "msg": result.get("msg", "未知错误"), "data": result.get("data")})
        task_id = result["data"]["task_id"]
        print(f"  任务已提交: {task_id}")
        return self._poll(task_id, token)

    # ── 公开 API：Precision ──────────────────────────────────────

    def parse_by_url(self, url: str, model: str = "vlm", **kw) -> dict:
        def submit(token):
            return self._req("post", f"{self.base_url}/api/v4/extract/task", headers=self._auth(token), json={"url": url, "model_version": model, **kw}, timeout=30).json()
        return self._submit_and_poll(submit)

    def parse_by_file(self, file_path: str, model: str = "vlm", **kw) -> dict:
        fname = os.path.basename(file_path)
        self._release_cooled()
        token = self._next_token()
        if not token:
            raise RuntimeError("所有 Token 均在冷却中")

        # 获取上传 URL
        r = self._req("post", f"{self.base_url}/api/v4/file-urls/batch", headers=self._auth(token), json={"files": [{"name": fname}], "model_version": model}, timeout=30).json()
        if self._should_retry(r):
            self._mark_exhausted(token, self.token_cooldown)
            token = self._next_token()
            if not token:
                raise RuntimeError("所有 Token 均触发限流")
            r = self._req("post", f"{self.base_url}/api/v4/file-urls/batch", headers=self._auth(token), json={"files": [{"name": fname}], "model_version": model}, timeout=30).json()
        if r.get("code") != 0:
            return self._enrich({"code": r.get("code", -1), "msg": r.get("msg", "未知错误")})

        batch_id = r["data"]["batch_id"]
        upload_url = r["data"]["file_urls"][0]
        print(f"  上传: {fname}")

        for i in range(3):
            try:
                with open(file_path, "rb") as f:
                    put = requests.put(upload_url, data=f, timeout=120)
                    if put.status_code not in (200, 201):
                        return self._enrich({"code": -1, "msg": f"上传失败 HTTP {put.status_code}"})
                break
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                if i >= 2:
                    return self._enrich({"code": -1, "msg": f"上传失败: {e}"})
                time.sleep(3 * (i + 1))

        print(f"  上传完成，等待解析...")
        time.sleep(2)
        return self._poll_batch(batch_id, token, poll_timeout=self.timeout)

    def parse_batch_urls(self, files: List[dict], model: str = "vlm", **kw) -> dict:
        def submit(token):
            return self._req("post", f"{self.base_url}/api/v4/extract/task/batch", headers=self._auth(token), json={"files": files, "model_version": model, **kw}, timeout=30).json()
        result = self._submit_and_poll(submit)
        bid = result.get("data", {}).get("batch_id")
        if bid:
            return self._poll_batch(bid, self._next_token())
        return self._enrich(result)

    # ── 公开 API：Agent（免 Token）────────────────────────────────

    def parse_agent_by_url(self, url: str, **kw) -> dict:
        r = self._req("post", f"{self.agent_url}/parse/url", json={"url": url, **kw}, timeout=30).json()
        if r.get("code") != 0:
            return self._enrich(r)
        return self._poll(r["data"]["task_id"], None, agent=True)

    def parse_agent_by_file(self, file_path: str, **kw) -> dict:
        fname = os.path.basename(file_path)
        r = self._req("post", f"{self.agent_url}/parse/file", json={"file_name": fname, **kw}, timeout=30).json()
        if r.get("code") != 0:
            return self._enrich(r)
        for i in range(3):
            try:
                with open(file_path, "rb") as f:
                    put = requests.put(r["data"]["file_url"], data=f, timeout=120, verify=self.verify_ssl)
                    if put.status_code not in (200, 201):
                        return self._enrich({"code": -1, "msg": f"上传失败 HTTP {put.status_code}"})
                break
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                if i >= 2:
                    return self._enrich({"code": -1, "msg": f"上传失败: {e}"})
                time.sleep(3 * (i + 1))
        return self._poll(r["data"]["task_id"], None, agent=True)

    # ── 统一入口 ─────────────────────────────────────────────────

    def parse(self, source: str, **kw) -> dict:
        is_url = source.startswith("http://") or source.startswith("https://")
        has = bool(self._pool)
        if is_url:
            return self.parse_by_url(source, **kw) if has else self.parse_agent_by_url(source, **kw)
        return self.parse_by_file(source, **kw) if has else self.parse_agent_by_file(source, **kw)

    # ── 方案 A：PDF → Markdown 存回 raw/ ──────────────────────────

    def parse_to_markdown_file(self, file_path: str, output_dir: str = None, model: str = "vlm", delete_pdf: bool = False, skip_if_exists: bool = True) -> dict:
        if not os.path.isfile(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}

        base = os.path.splitext(os.path.basename(file_path))[0]
        target = output_dir or os.path.dirname(file_path)
        md_path = os.path.join(target, f"{base}.md")

        # 跳过：同名 .md 已存在
        if skip_if_exists and os.path.exists(md_path):
            print(f"[MinerU] 跳过（同名MD已存在）: {os.path.basename(file_path)}")
            return {"success": True, "skipped": True, "markdown_path": md_path, "source_file": file_path}

        print(f"[MinerU] 解析: {os.path.basename(file_path)}")
        result = self.parse(file_path, model=model)

        if result.get("code") != 0 and result.get("code") is not None:
            if not result.get("data", {}).get("full_zip_url") and not result.get("data", {}).get("extract_result"):
                return {"success": False, "error": result.get("msg", "解析失败"), "source_file": file_path, "token_expiry": result.get("token_expiry")}

        md = self._extract_markdown(result, base, target)
        if not md:
            return {"success": False, "error": "未能提取 Markdown", "source_file": file_path, "token_expiry": result.get("token_expiry")}

        os.makedirs(target, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)

        sz = os.path.getsize(md_path)
        print(f"[MinerU] 已保存: {md_path} ({sz:,} bytes)")

        if delete_pdf and os.path.isfile(file_path):
            os.remove(file_path)
            print(f"[MinerU] 已删除: {os.path.basename(file_path)}")

        return {"success": True, "markdown_path": md_path, "markdown_size": sz, "source_file": file_path, "deleted_pdf": delete_pdf, "token_expiry": result.get("token_expiry", {})}

    def parse_batch_to_markdown(self, file_paths: List[str], output_dir: str = None, model: str = "vlm", delete_pdf: bool = False, delay_between: float = 2.0, skip_if_exists: bool = True) -> dict:
        files = list(dict.fromkeys(file_paths))
        total = len(files)
        results, ok, fail, skipped = [], 0, 0, 0
        t0 = time.time()

        for i, fp in enumerate(files, 1):
            pct = int(i / total * 100)
            elapsed = time.time() - t0
            eta = int((total - i) * (elapsed / i)) if i > 0 else 0
            bar = "█" * int(20 * i / total) + "░" * (20 - int(20 * i / total))
            eta_s = f"{eta // 60}分{eta % 60}秒" if eta > 60 else f"{eta}秒"
            print(f"\n[{bar}] {pct}% [{i}/{total}] ETA: {eta_s}")
            print(f"  处理: {os.path.basename(fp)}")

            r = self.parse_to_markdown_file(fp, output_dir=output_dir, model=model, delete_pdf=delete_pdf, skip_if_exists=skip_if_exists)
            results.append(r)
            if r.get("skipped"):
                skipped += 1
            elif r.get("success"):
                ok += 1
            else:
                fail += 1
                print(f"  ❌ 失败: {r.get('error', '?')}")

            if i < total and delay_between > 0:
                time.sleep(delay_between)

        tt = time.time() - t0
        ts = f"{int(tt // 60)}分{int(tt % 60)}秒" if tt > 60 else f"{int(tt)}秒"
        print(f"\n===== 批量完成 =====\n成功: {ok}/{total} | 跳过: {skipped} | 失败: {fail} | 耗时: {ts}")
        return {"total": total, "success": ok, "failed": fail, "skipped": skipped, "results": results}

    def parse_batch_parallel(self, file_paths: List[str], output_dir: str = None, model: str = "vlm", delete_pdf: bool = False, max_workers: int = 4, skip_if_exists: bool = True) -> dict:
        files = list(dict.fromkeys(file_paths))
        total = len(files)
        results, ok, fail, skipped = [], 0, 0, 0
        done_count = [0]
        t0 = time.time()
        from threading import Lock
        lock = Lock()

        print(f"\n===== 并行批量处理 ({max_workers} 线程) =====\n总文件数: {total}")

        def task(fp):
            try:
                return self.parse_to_markdown_file(fp, output_dir=output_dir, model=model, delete_pdf=delete_pdf, skip_if_exists=skip_if_exists)
            except Exception as e:
                return {"success": False, "source_file": fp, "error": str(e)}

        def worker(fp):
            r = task(fp)
            with lock:
                done_count[0] += 1
                pct = int(done_count[0] / total * 100)
                elapsed = time.time() - t0
                eta = int((total - done_count[0]) * (elapsed / done_count[0])) if done_count[0] > 0 else 0
                bar = "█" * int(20 * done_count[0] / total) + "░" * (20 - int(20 * done_count[0] / total))
                eta_s = f"{eta // 60}分{eta % 60}秒" if eta > 60 else f"{eta}秒"
                if r.get("skipped"):
                    st = "⏭"
                elif r.get("success"):
                    st = "✓"
                else:
                    st = "✗"
                print(f"[{bar}] {pct}% [{done_count[0]}/{total}] {eta_s} | {st} {os.path.basename(fp)}")
            return r

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(worker, fp): fp for fp in files}
            for f in as_completed(futs):
                r = f.result()
                results.append(r)
                if r.get("skipped"):
                    skipped += 1
                elif r.get("success"):
                    ok += 1
                else:
                    fail += 1

        tt = time.time() - t0
        ts = f"{int(tt // 60)}分{int(tt % 60)}秒" if tt > 60 else f"{int(tt)}秒"
        print(f"\n===== 并行完成 =====\n成功: {ok}/{total} | 跳过: {skipped} | 失败: {fail} | 耗时: {ts}")
        return {"total": total, "success": ok, "failed": fail, "skipped": skipped, "results": results}

    # ── 结果提取 ─────────────────────────────────────────────────

    def _extract_markdown(self, result: dict, title: str = "", out_dir: str = None) -> Optional[str]:
        data = result.get("data", {})
        extract = data.get("extract_result", [])

        # 方式 1: extract_result 中的 full_zip_url
        if extract:
            for item in extract:
                if isinstance(item, dict) and item.get("full_zip_url"):
                    md = self._download_zip(item["full_zip_url"], title, out_dir)
                    if md:
                        return md

        # 方式 2: 顶层 full_zip_url
        if data.get("full_zip_url"):
            return self._download_zip(data["full_zip_url"], title, out_dir)

        # 方式 3: extract_result 中的内容拼接
        if extract:
            parts = []
            for item in extract:
                if isinstance(item, dict):
                    md = item.get("markdown_content") or item.get("content") or ""
                    md_url = item.get("markdown_url") or item.get("url")
                    if not md and md_url:
                        try:
                            r = self._req("get", md_url, timeout=30)
                            md = r.text
                        except Exception:
                            pass
                    if md:
                        parts.append(md)
            if parts:
                return "\n\n".join(parts)

        # 方式 4: markdown_url
        md_url = data.get("markdown_url") or data.get("url")
        if md_url:
            try:
                return self._req("get", md_url, timeout=30).text
            except Exception as e:
                print(f"  下载失败: {e}")

        # 方式 5: content 字段
        return data.get("content") or data.get("markdown") or None

    def _download_zip(self, url: str, title: str = "", out_dir: str = None) -> Optional[str]:
        try:
            print(f"  下载结果 zip...")
            r = self._req("get", url, timeout=120)
            r.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp.write(r.content)
                tmp_path = tmp.name

            md_content = ""
            with zipfile.ZipFile(tmp_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".md") and not name.startswith("images"):
                        md_content = zf.read(name).decode("utf-8", errors="replace")
                        break
                if out_dir and md_content:
                    img_dir = os.path.join(out_dir, "images")
                    os.makedirs(img_dir, exist_ok=True)
                    for name in zf.namelist():
                        if name.startswith("images/") and not name.endswith("/"):
                            data = zf.read(name)
                            p = os.path.join(img_dir, os.path.basename(name))
                            with open(p, "wb") as f:
                                f.write(data)
                            print(f"    图片: {os.path.basename(name)}")

            os.unlink(tmp_path)
            return md_content or None
        except Exception as e:
            print(f"  ZIP 处理失败: {e}")
            return None


# ── CLI ───────────────────────────────────────────────────────────

def main():
    import argparse
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="mineru2md - PDF/DOCX/PPTX → Markdown")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Token 状态 + 过期倒计时")

    s = sub.add_parser("parse", help="解析单个文档")
    s.add_argument("source")
    s.add_argument("--model", default="vlm")

    s = sub.add_parser("to-md", help="PDF → Markdown 存回同目录")
    s.add_argument("file")
    s.add_argument("--output-dir", default=None)
    s.add_argument("--model", default="vlm")
    s.add_argument("--delete-pdf", action="store_true")

    s = sub.add_parser("batch-md", help="批量 PDF → Markdown")
    s.add_argument("paths", nargs="+")
    s.add_argument("--output-dir", default=None)
    s.add_argument("--model", default="vlm")
    s.add_argument("--delete-pdf", action="store_true")
    s.add_argument("--delay", type=float, default=2.0)
    s.add_argument("--parallel", action="store_true")
    s.add_argument("--workers", type=int, default=4)

    args = p.parse_args()
    client = MinerUClient()

    if args.cmd == "status":
        print(json.dumps(client.get_token_status(), ensure_ascii=False, indent=2))
    elif args.cmd == "parse":
        print(json.dumps(client.parse(args.source, model=args.model), ensure_ascii=False, indent=2))
    elif args.cmd == "to-md":
        print(json.dumps(client.parse_to_markdown_file(args.file, output_dir=args.output_dir, model=args.model, delete_pdf=args.delete_pdf), ensure_ascii=False, indent=2))
    elif args.cmd == "batch-md":
        expanded = []
        for pat in args.paths:
            expanded.extend(glob_mod.glob(pat, recursive=True) if "*" in pat or "?" in pat else [pat])
        if not expanded:
            print("未找到匹配文件"); sys.exit(1)
        print(f"找到 {len(expanded)} 个文件")
        if args.parallel:
            r = client.parse_batch_parallel(expanded, output_dir=args.output_dir, model=args.model, delete_pdf=args.delete_pdf, max_workers=args.workers)
        else:
            r = client.parse_batch_to_markdown(expanded, output_dir=args.output_dir, model=args.model, delete_pdf=args.delete_pdf, delay_between=args.delay)
        print(json.dumps({k: v for k, v in r.items() if k != "results"}, ensure_ascii=False, indent=2))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
