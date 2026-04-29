"""
旧格式兼容转换器
自动将 .doc/.ppt 转换为 .docx/.pptx

后端优先级（自动检测，按顺序选择）：
  1. Microsoft Word/PowerPoint COM（Windows + 已装 Office）
  2. WPS Writer/Presentation COM（Windows + 已装 WPS）
  3. LibreOffice headless（跨平台兜底）
"""
import os
import shutil
import sys
from typing import Optional

LEGACY_MAP = {
    ".doc": ".docx",
    ".ppt": ".pptx",
}

# ── 后端注册表 ────────────────────────────────────────────────────
# 每个后端: {"name", "exts", "probe", "convert"}

BACKENDS = []


def _register_backend(name: str, exts: list, probe_fn, convert_fn):
    BACKENDS.append({"name": name, "exts": exts, "probe": probe_fn, "convert": convert_fn})


# ── 1. MS Office COM ──────────────────────────────────────────────

def _probe_ms_word():
    try:
        import win32com.client
        w = win32com.client.Dispatch("Word.Application")
        ver = w.Version
        w.Quit()
        return f"Word {ver}"
    except Exception:
        return None


def _probe_ms_ppt():
    try:
        import win32com.client
        p = win32com.client.Dispatch("PowerPoint.Application")
        ver = p.Version
        p.Quit()
        return f"PowerPoint {ver}"
    except Exception:
        return None


def _convert_doc_ms(input_path: str) -> str:
    import win32com.client
    abs_path = os.path.abspath(input_path)
    output_path = os.path.splitext(abs_path)[0] + ".docx"
    if os.path.exists(output_path):
        return output_path
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(abs_path)
        doc.SaveAs(os.path.abspath(output_path), FileFormat=16)  # wdFormatXMLDocument
        doc.Close()
    finally:
        word.Quit()
    return output_path


def _convert_ppt_ms(input_path: str) -> str:
    import win32com.client
    abs_path = os.path.abspath(input_path)
    output_path = os.path.splitext(abs_path)[0] + ".pptx"
    if os.path.exists(output_path):
        return output_path
    ppt = win32com.client.Dispatch("PowerPoint.Application")
    try:
        pres = ppt.Presentations.Open(abs_path, WithWindow=False)
        pres.SaveAs(os.path.abspath(output_path), FileFormat=24)  # ppSaveAsOpenXMLPresentation
        pres.Close()
    finally:
        ppt.Quit()
    return output_path


# ── 2. WPS COM ────────────────────────────────────────────────────

def _probe_wps_writer():
    """WPS Writer COM 探测"""
    try:
        import win32com.client
        # WPS ProgID: Kwps.Application / wps.Application
        for pid in ("Kwps.Application", "wps.Application"):
            try:
                w = win32com.client.Dispatch(pid)
                ver = w.Version
                w.Quit()
                return f"WPS Writer {ver}"
            except Exception:
                continue
        return None
    except Exception:
        return None


def _probe_wps_ppt():
    """WPS Presentation COM 探测"""
    try:
        import win32com.client
        for pid in ("Kwpp.Application", "wpp.Application"):
            try:
                p = win32com.client.Dispatch(pid)
                ver = p.Version
                p.Quit()
                return f"WPS Presentation {ver}"
            except Exception:
                continue
        return None
    except Exception:
        return None


def _convert_doc_wps(input_path: str) -> str:
    """WPS Writer 转换 .doc → .docx"""
    import win32com.client
    abs_path = os.path.abspath(input_path)
    output_path = os.path.splitext(abs_path)[0] + ".docx"
    if os.path.exists(output_path):
        return output_path
    for pid in ("Kwps.Application", "wps.Application"):
        try:
            w = win32com.client.Dispatch(pid)
            w.Visible = False
            doc = w.Documents.Open(abs_path)
            # WPS FileFormat: 16 = docx
            doc.SaveAs(os.path.abspath(output_path), FileFormat=16)
            doc.Close()
            w.Quit()
            return output_path
        except Exception:
            continue
    raise RuntimeError("WPS Writer COM 打开失败")


def _convert_ppt_wps(input_path: str) -> str:
    """WPS Presentation 转换 .ppt → .pptx"""
    import win32com.client
    abs_path = os.path.abspath(input_path)
    output_path = os.path.splitext(abs_path)[0] + ".pptx"
    if os.path.exists(output_path):
        return output_path
    for pid in ("Kwpp.Application", "wpp.Application"):
        try:
            p = win32com.client.Dispatch(pid)
            pres = p.Presentations.Open(abs_path, WithWindow=False)
            pres.SaveAs(os.path.abspath(output_path), FileFormat=24)
            pres.Close()
            p.Quit()
            return output_path
        except Exception:
            continue
    raise RuntimeError("WPS Presentation COM 打开失败")


# ── 3. LibreOffice headless ───────────────────────────────────────

def _probe_libreoffice():
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/snap/bin/libreoffice",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return shutil.which("soffice")


def _convert_libreoffice(input_path: str, target_ext: str) -> str:
    import subprocess
    lo = _probe_libreoffice()
    if not lo:
        raise RuntimeError("未找到 LibreOffice")
    abs_path = os.path.abspath(input_path)
    output_path = os.path.splitext(abs_path)[0] + target_ext
    if os.path.exists(output_path):
        return output_path
    out_dir = os.path.dirname(abs_path)
    fmt = "docx" if target_ext == ".docx" else "pptx"
    result = subprocess.run(
        [lo, "--headless", "--convert-to", fmt, "--outdir", out_dir, abs_path],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice 失败: {result.stderr.decode('utf-8', errors='replace')[:200]}")
    if not os.path.exists(output_path):
        raise RuntimeError(f"输出文件未生成: {output_path}")
    return output_path


def _convert_doc_lo(input_path: str) -> str:
    return _convert_libreoffice(input_path, ".docx")


def _convert_ppt_lo(input_path: str) -> str:
    return _convert_libreoffice(input_path, ".pptx")


# ── 注册后端（按优先级顺序） ──────────────────────────────────────

_register_backend("MS Word",        [".doc"],  _probe_ms_word,  _convert_doc_ms)
_register_backend("MS PowerPoint",  [".ppt"],  _probe_ms_ppt,   _convert_ppt_ms)
_register_backend("WPS Writer",      [".doc"],  _probe_wps_writer, _convert_doc_wps)
_register_backend("WPS Presentation",[".ppt"],  _probe_wps_ppt,  _convert_ppt_wps)
_register_backend("LibreOffice",    [".doc", ".ppt"],
                  lambda: _probe_libreoffice() and "LibreOffice",
                  None)  # LibreOffice 用统一入口，见下方


# ── 探测函数 ──────────────────────────────────────────────────────

def probe_all() -> list:
    """
    探测所有可用后端，返回列表。
    每项: {"name", "status": "available"|"missing", "detail"}
    """
    results = []
    for be in BACKENDS:
        try:
            info = be["probe"]()
            if info:
                results.append({"name": be["name"], "status": "available", "detail": info})
            else:
                results.append({"name": be["name"], "status": "missing", "detail": ""})
        except Exception as e:
            results.append({"name": be["name"], "status": "missing", "detail": str(e)})
    return results


def is_available() -> bool:
    """至少有一个后端可用"""
    for be in BACKENDS:
        try:
            if be["probe"]():
                return True
        except Exception:
            continue
    return False


def get_backend_info() -> dict:
    """返回可用后端摘要"""
    ms_word = _probe_ms_word()
    ms_ppt = _probe_ms_ppt()
    wps_writer = _probe_wps_writer()
    wps_ppt = _probe_wps_ppt()
    lo = _probe_libreoffice()
    return {
        "ms_word": ms_word,
        "ms_powerpoint": ms_ppt,
        "wps_writer": wps_writer,
        "wps_presentation": wps_ppt,
        "libreoffice": lo,
        "can_convert_doc": bool(ms_word or wps_writer or lo),
        "can_convert_ppt": bool(ms_ppt or wps_ppt or lo),
    }


# ── 转换入口 ──────────────────────────────────────────────────────

def _select_backend(ext: str):
    """为指定扩展名选择最优后端（按注册顺序）"""
    for be in BACKENDS:
        if ext not in be["exts"]:
            continue
        # LibreOffice 特殊处理
        if be["name"] == "LibreOffice":
            lo = _probe_libreoffice()
            if lo:
                return be
            continue
        try:
            if be["probe"]():
                return be
        except Exception:
            continue
    return None


def convert_file(input_path: str) -> dict:
    """
    自动转换旧格式文件（.doc → .docx，.ppt → .pptx）。
    已有新格式则跳过。按优先级自动选择最优后端。

    Returns:
        {"input_path", "output_path", "format", "success", "error", "skipped", "backend"}
    """
    ext = os.path.splitext(input_path)[1].lower()
    target_ext = LEGACY_MAP.get(ext)

    if not target_ext:
        return {"input_path": input_path, "output_path": input_path,
                "format": ext, "success": True, "error": None, "skipped": True, "backend": ""}

    output_path = os.path.splitext(input_path)[0] + target_ext

    if os.path.exists(output_path):
        return {"input_path": input_path, "output_path": output_path,
                "format": target_ext, "success": True, "error": None, "skipped": True, "backend": ""}

    be = _select_backend(ext)
    if not be:
        return {"input_path": input_path, "output_path": input_path,
                "format": ext, "success": False,
                "error": f"无可用后端转换 {ext}，请安装 MS Office / WPS / LibreOffice",
                "skipped": False, "backend": ""}

    # LibreOffice 用统一入口
    if be["name"] == "LibreOffice":
        try:
            result_path = _convert_libreoffice(input_path, target_ext)
            return {"input_path": input_path, "output_path": result_path,
                    "format": target_ext, "success": True, "error": None,
                    "skipped": False, "backend": be["name"]}
        except Exception as e:
            return {"input_path": input_path, "output_path": input_path,
                    "format": ext, "success": False, "error": str(e),
                    "skipped": False, "backend": be["name"]}

    # COM 后端
    try:
        result_path = be["convert"](input_path)
        return {"input_path": input_path, "output_path": result_path,
                "format": target_ext, "success": True, "error": None,
                "skipped": False, "backend": be["name"]}
    except Exception as e:
        return {"input_path": input_path, "output_path": input_path,
                "format": ext, "success": False, "error": str(e),
                "skipped": False, "backend": be["name"]}


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="旧格式兼容转换器 (doc→docx, ppt→pptx)")
    parser.add_argument("files", nargs="*", help="待转换文件路径")
    parser.add_argument("--check", action="store_true", help="检测所有可用后端")
    args = parser.parse_args()

    if args.check:
        print("=== 后端探测 ===\n")
        results = probe_all()
        for r in results:
            icon = "✅" if r["status"] == "available" else "❌"
            detail = f" ({r['detail']})" if r["detail"] else ""
            print(f"  {icon} {r['name']}{detail}")
        print()
        info = get_backend_info()
        print(f"  可转换 .doc: {'✅' if info['can_convert_doc'] else '❌'}")
        print(f"  可转换 .ppt: {'✅' if info['can_convert_ppt'] else '❌'}")
        sys.exit(0)

    if not args.files:
        parser.print_help()
        sys.exit(1)

    for fpath in args.files:
        if not os.path.isfile(fpath):
            print(f"❌ 文件不存在: {fpath}")
            continue
        result = convert_file(fpath)
        if result["skipped"]:
            print(f"⏭ {os.path.basename(fpath)} → 已是新格式")
        elif result["success"]:
            print(f"✅ {os.path.basename(fpath)} → {os.path.basename(result['output_path'])} [{result['backend']}]")
        else:
            print(f"❌ {os.path.basename(fpath)}: {result['error'][:80]}")
