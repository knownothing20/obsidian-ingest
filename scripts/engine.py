"""
engine.py - 多引擎抽象层

统一接口，可切换 MinerU / Marker / Docling / DocStrange 等后端。
"""

import os
import sys
from abc import ABC, abstractmethod
from typing import Optional


class ConversionEngine(ABC):
    """转换引擎抽象基类"""

    @abstractmethod
    def convert_file(self, input_path: str, output_dir: str, **kwargs) -> dict:
        """
        转换单个文件

        Returns:
            {
                "success": bool,
                "markdown_path": str,      # 生成的 MD 路径
                "markdown_text": str,      # MD 内容（可选）
                "images": list,            # 图片列表
                "error": str,              # 失败原因
                "skipped": bool,           # 是否跳过
            }
        """
        pass

    @abstractmethod
    def get_status(self) -> dict:
        """获取引擎状态"""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """引擎名称"""
        pass


class MinerUEngine(ConversionEngine):
    """MinerU API 引擎（本地 mineru_client）"""

    def __init__(self, config: dict):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            # 本地导入
            from mineru_client import MinerUClient
            # 转换配置格式：obsidian-ingest -> mineru_client 兼容
            mineru_cfg = self.config.get("engine", {}).get("mineru", {})
            client_cfg = {
                "tokens": self.config.get("tokens", []),
                "api": {
                    "base_url": mineru_cfg.get("base_url", "https://mineru.net"),
                    "timeout": mineru_cfg.get("timeout", 300),
                    "poll_interval": mineru_cfg.get("poll_interval", 3),
                    "max_poll_interval": mineru_cfg.get("max_poll_interval", 30),
                    "verify_ssl": mineru_cfg.get("verify_ssl", False),
                },
            }
            self._client = MinerUClient(client_cfg)
        return self._client

    def convert_file(self, input_path: str, output_dir: str, **kwargs) -> dict:
        client = self._get_client()
        skip_if_exists = kwargs.get("skip_if_exists", True)
        return client.parse_to_markdown_file(
            input_path, output_dir=output_dir, skip_if_exists=skip_if_exists
        )

    def get_status(self) -> dict:
        client = self._get_client()
        return client.get_token_status()

    def get_name(self) -> str:
        return "mineru"


class MarkerEngine(ConversionEngine):
    """Marker 引擎（本地 GPU）"""

    def __init__(self, config: dict):
        self.config = config.get("marker", {})

    def convert_file(self, input_path: str, output_dir: str, **kwargs) -> dict:
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
        except ImportError:
            return {"success": False, "error": "marker 未安装: pip install marker-pdf"}

        try:
            converter = PdfConverter(artifact_dict=create_model_dict())
            rendered = converter(input_path)
            md_text = rendered.markdown

            os.makedirs(output_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(input_path))[0]
            md_path = os.path.join(output_dir, f"{base}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_text)

            return {"success": True, "markdown_path": md_path, "markdown_text": md_text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_status(self) -> dict:
        return {"engine": "marker", "available": True}

    def get_name(self) -> str:
        return "marker"


class DoclingEngine(ConversionEngine):
    """Docling 引擎（IBM）"""

    def __init__(self, config: dict):
        self.config = config.get("docling", {})

    def convert_file(self, input_path: str, output_dir: str, **kwargs) -> dict:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            return {"success": False, "error": "docling 未安装: pip install docling"}

        try:
            converter = DocumentConverter()
            result = converter.convert(input_path)
            md_text = result.document.export_to_markdown()

            os.makedirs(output_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(input_path))[0]
            md_path = os.path.join(output_dir, f"{base}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_text)

            return {"success": True, "markdown_path": md_path, "markdown_text": md_text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_status(self) -> dict:
        return {"engine": "docling", "available": True}

    def get_name(self) -> str:
        return "docling"


# ── 引擎工厂 ──────────────────────────────────────────────────────

def create_engine(config: dict) -> ConversionEngine:
    """根据配置创建引擎实例"""
    provider = config.get("engine", {}).get("provider", "mineru")

    engines = {
        "mineru": MinerUEngine,
        "marker": MarkerEngine,
        "docling": DoclingEngine,
    }

    engine_cls = engines.get(provider)
    if not engine_cls:
        raise ValueError(f"不支持的引擎: {provider}，可选: {list(engines.keys())}")

    return engine_cls(config)
