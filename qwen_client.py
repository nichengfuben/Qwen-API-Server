# qwen_client.py
# Qwen客户端模块 - 负责与外部Qwen服务器通信
# 版本: 5.0.0
# 算法: Track-and-Stop 多臂赌博机最优账号选择 + 断点续传

import asyncio
import json
import os
import re
import time
import uuid
import hashlib
import inspect
import secrets
import base64
import hmac
import threading
import signal
import math
import mimetypes
import shutil
import requests
import aiohttp
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any, AsyncGenerator, Dict, List, Optional, Union, Tuple, Set, Callable,
    Deque,
)
from collections import deque
from asyncio import Lock, Semaphore, Event
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, quote
from enum import Enum

# 修复asyncio兼容性
asyncio.iscoroutinefunction = inspect.iscoroutinefunction

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("qwen_client")

# 账号配置导入
try:
    from qwen_accounts import ACCOUNTS
except ImportError:
    ACCOUNTS = {}
    logger.warning("未找到账号列表，请配置 qwen_accounts.py")


# ==================== 枚举类定义 ====================


class ChatType(str, Enum):
    """聊天类型枚举"""

    TEXT = "t2t"
    IMAGE_EDIT = "image_edit"
    LEARN = "learn"
    DEEP_RESEARCH = "deep_research"
    DEEP_THINKING = "deep_thinking"
    TEXT_TO_VIDEO = "t2v"
    ARTIFACTS = "artifacts"
    WEB_DEV = "web_dev"


class ResearchMode(str, Enum):
    """研究模式枚举"""

    NORMAL = "normal"
    ADVANCE = "advance"


class VideoSize(str, Enum):
    """视频尺寸枚举"""

    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


class ResponsePhase(str, Enum):
    """响应阶段枚举"""

    ANSWER = "answer"
    THINKING_SUMMARY = "thinking_summary"
    IMAGE_GEN = "image_gen"
    VIDEO_GEN = "video_gen"
    RESEARCH_NOTICE = "ResearchNotice"
    RESEARCH_PLANNING = "ResearchPlanning"


# ==================== 配置类 ====================


class Config:
    """全局配置类"""

    # 文件类型映射
    FILE_TYPE_MAPPING: Dict[str, str] = {
        "image/jpeg": "image",
        "image/jpg": "image",
        "image/png": "image",
        "image/gif": "image",
        "image/webp": "image",
        "image/bmp": "image",
        "image/svg+xml": "image",
        "image/tiff": "image",
        "image/ico": "image",
        "video/mp4": "video",
        "video/avi": "video",
        "video/mov": "video",
        "video/wmv": "video",
        "video/flv": "video",
        "video/webm": "video",
        "video/mkv": "video",
        "video/3gp": "video",
        "video/m4v": "video",
        "video/quicktime": "video",
        "audio/mp3": "audio",
        "audio/wav": "audio",
        "audio/flac": "audio",
        "audio/aac": "audio",
        "audio/ogg": "audio",
        "audio/wma": "audio",
        "audio/m4a": "audio",
        "audio/opus": "audio",
        "audio/mpeg": "audio",
        "audio/x-wav": "audio",
        "application/pdf": "file",
        "application/msword": "file",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "file",
        "application/vnd.ms-excel": "file",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "file",
        "application/vnd.ms-powerpoint": "file",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "file",
        "text/plain": "file",
        "text/csv": "file",
        "application/rtf": "file",
        "application/zip": "file",
        "application/x-rar-compressed": "file",
        "application/x-7z-compressed": "file",
        "application/json": "file",
        "application/xml": "file",
        "text/xml": "file",
        "text/html": "file",
        "text/css": "file",
        "text/javascript": "file",
        "application/javascript": "file",
        "text/x-python": "file",
        "text/x-java": "file",
        "text/x-c": "file",
        "text/x-c++": "file",
        "text/x-csharp": "file",
        "text/x-php": "file",
        "text/x-ruby": "file",
        "text/x-go": "file",
        "text/x-rust": "file",
        "text/x-swift": "file",
        "text/x-kotlin": "file",
        "text/x-scala": "file",
        "text/x-sql": "file",
        "text/x-shell": "file",
        "text/rtf": "file",
        "text/markdown": "file",
        "text/yaml": "file",
        "text/yml": "file",
        "text/toml": "file",
        "text/ini": "file",
        "text/log": "file",
    }

    # 扩展名到MIME类型映射
    EXTENSION_TO_MIME: Dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".ico": "image/ico",
        ".mp4": "video/mp4",
        ".avi": "video/avi",
        ".mov": "video/quicktime",
        ".wmv": "video/wmv",
        ".flv": "video/flv",
        ".webm": "video/webm",
        ".mkv": "video/mkv",
        ".3gp": "video/3gp",
        ".m4v": "video/m4v",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".wma": "audio/wma",
        ".m4a": "audio/m4a",
        ".opus": "audio/opus",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".rtf": "application/rtf",
        ".zip": "application/zip",
        ".rar": "application/x-rar-compressed",
        ".7z": "application/x-7z-compressed",
        ".json": "application/json",
        ".xml": "application/xml",
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".py": "text/x-python",
        ".java": "text/x-java",
        ".c": "text/x-c",
        ".cpp": "text/x-c++",
        ".cxx": "text/x-c++",
        ".cc": "text/x-c++",
        ".cs": "text/x-csharp",
        ".php": "text/x-php",
        ".rb": "text/x-ruby",
        ".go": "text/x-go",
        ".rs": "text/x-rust",
        ".swift": "text/x-swift",
        ".kt": "text/x-kotlin",
        ".scala": "text/x-scala",
        ".sql": "text/x-sql",
        ".sh": "text/x-shell",
        ".bash": "text/x-shell",
        ".zsh": "text/x-shell",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".toml": "text/toml",
        ".ini": "text/ini",
        ".conf": "text/ini",
        ".log": "text/log",
        ".text": "text/plain",
    }

    # 存储目录配置
    BASE64_IMAGE_DIR = "data/img"
    BASE64_AUDIO_DIR = "data/audio"
    BASE64_VIDEO_DIR = "data/video"
    BASE64_FILE_DIR = "data/files"
    GENERATED_IMAGE_DIR = "data/generated_images"
    GENERATED_VIDEO_DIR = "data/generated_videos"
    LARGE_TEXT_DIR = "data/large_texts"
    TTS_DIR = "data/tts"
    PERSISTENCE_FILE = str(Path("data/qwen_account_usage.json"))

    # 自定义Base64字符集
    CUSTOM_BASE64_CHARS = (
        "DGi0YA7BemWnQjCl4_bR3f8SKIF9tUz/xhr2oEOgPpac=61ZqwTudLkM5vHyNXsVJ"
    )

    # 时间间隔配置
    COOKIE_REFRESH_INTERVAL = 15 * 60
    CHAT_ID_REFRESH_INTERVAL = 5 * 60
    CHAT_ID_POOL_SIZE = 10

    # 重试配置
    MAX_RETRY_ATTEMPTS = 10
    INITIAL_RETRY_DELAY = 1
    MAX_RETRY_DELAY = 300

    # 超时配置
    HTTP_TIMEOUT = 30
    OSS_UPLOAD_TIMEOUT = 60
    TTS_TIMEOUT = 600
    IMAGE_GENERATION_TIMEOUT = 120
    VIDEO_GENERATION_TIMEOUT = 600
    CHAT_COMPLETION_TIMEOUT = 600
    DEEP_RESEARCH_TIMEOUT = 1800
    EMBEDDING_TIMEOUT = 30

    # 空响应重试配置
    EMPTY_RESPONSE_MAX_RETRIES = 3
    EMPTY_RESPONSE_INITIAL_DELAY = 1.0

    # 大文本阈值
    LARGE_TEXT_THRESHOLD = 163840

    # 并发配置
    CONCURRENT_GENERATORS = 3
    MIN_TOKENS_FOR_SELECTION = 10

    # 文件缓存配置
    FILE_CACHE_TTL = 3600
    FILE_CACHE_MAX_SIZE = 100

    # 嵌入API配置
    EMBEDDING_API_URL = "http://110.42.196.178:4546/v1/embeddings"
    EMBEDDING_MODEL = "qwen3-embedding:0.6b"

    # 视频下载CDN
    VIDEO_CDN_BASE = "https://cdn.qwenlm.ai/output"

    # API版本
    API_VERSION = "2.1"

    # Track-and-Stop 算法配置
    TAS_WINDOW_SIZE = 200  # 滑动窗口大小
    TAS_MIN_SAMPLES = 3  # 最小采样数才开始利用
    TAS_EXPLORATION_RATE = 0.1  # 基础探索率 epsilon
    TAS_DECAY_FACTOR = 0.995  # 探索率衰减因子
    TAS_MIN_EXPLORATION = 0.02  # 最小探索率
    TAS_CONFIDENCE_THRESHOLD = 0.95  # 停止探索的置信度阈值
    TAS_LATENCY_WEIGHT = 0.4  # 延迟权重
    TAS_SUCCESS_WEIGHT = 0.4  # 成功率权重
    TAS_THROUGHPUT_WEIGHT = 0.2  # 吞吐量权重
    TAS_COOLDOWN_PERIOD = 30.0  # 失败冷却期(秒)
    TAS_STATS_PERSIST_INTERVAL = 60  # 统计持久化间隔(秒)
    TAS_CHECKPOINT_DIR = "data/checkpoints"  # 断点续传目录
    TAS_CHECKPOINT_TTL = 3600  # 断点有效期(秒)


class ClientConfig:
    """客户端配置类"""

    # 可用模型列表
    AVAILABLE_MODELS = [
        "qwen3-max-2026-01-23",
        "qwen3-vl-plus",
        "qwen3-coder-plus",
        "qwen3-vl-32b",
        "qwen3-vl-30b-a3b",
        "qwen3-omni-flash-2025-12-01",
        "qwen-plus-2025-09-11",
        "qwen-plus-2025-07-28",
        "qwen3-30b-a3b",
        "qwen3-coder-30b-a3b-instruct",
        "qwen-max-latest",
        "qwen-plus-2025-01-25",
        "qwq-32b",
        "qwen-turbo-2025-02-11",
        "qwen2.5-omni-7b",
        "qvq-72b-preview-0310",
        "qwen2.5-vl-32b-instruct",
        "qwen2.5-14b-instruct-1m",
        "qwen2.5-coder-32b-instruct",
        "qwen2.5-72b-instruct",
    ]

    AVAILABLE_IMAGE_MODELS = ["flux", "turbo", "sana", "zimage"]
    DEFAULT_MODEL = "qwen3-coder-plus"
    DEFAULT_IMAGE_MODEL = "flux"

    # 视频尺寸选项
    VIDEO_SIZES = {
        "16:9": (1600, 900),
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
    }


# ==================== 工具类 ====================


class Base64FileHandler:
    """Base64文件处理器"""

    MIME_TO_EXT: Dict[str, str] = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
        "image/tiff": ".tiff",
        "image/ico": ".ico",
        "image/x-icon": ".ico",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/m4a": ".m4a",
        "audio/webm": ".weba",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/ogg": ".ogv",
        "video/avi": ".avi",
        "video/quicktime": ".mov",
        "video/x-msvideo": ".avi",
        "video/x-matroska": ".mkv",
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/json": ".json",
        "application/xml": ".xml",
        "text/html": ".html",
        "application/octet-stream": ".bin",
        "text/markdown": ".md",
        "text/yaml": ".yaml",
        "text/toml": ".toml",
        "text/ini": ".ini",
        "text/log": ".log",
        "text/rtf": ".rtf",
    }

    DATA_URI_PATTERN = re.compile(
        r"^data:(?P<mime>[\w/+.-]+)(?:;(?P<params>[^,]*))?,(?P<data>.+)$",
        re.DOTALL,
    )

    @classmethod
    def is_base64_data_uri(cls, data: str) -> bool:
        """检查是否为Base64数据URI"""
        if not data or not isinstance(data, str):
            return False
        return data.startswith("data:") and ";base64," in data

    @classmethod
    def parse_data_uri(cls, data_uri: str) -> Optional[Tuple[str, bytes]]:
        """解析数据URI"""
        if not cls.is_base64_data_uri(data_uri):
            return None

        try:
            match = cls.DATA_URI_PATTERN.match(data_uri)
            if not match:
                return None

            mime_type = match.group("mime")
            params = match.group("params") or ""
            base64_data = match.group("data")

            if "base64" not in params.lower():
                logger.warning(f"DataURI不是base64编码: {params}")
                return None

            base64_data = (
                base64_data.strip()
                .replace("\n", "")
                .replace("\r", "")
                .replace(" ", "")
            )

            padding = 4 - len(base64_data) % 4
            if padding != 4:
                base64_data += "=" * padding

            binary_data = base64.b64decode(base64_data)
            return mime_type, binary_data

        except Exception as e:
            logger.error(f"解析dataURI失败: {e}")
            return None

    @classmethod
    def get_save_directory(cls, mime_type: str) -> str:
        """获取保存目录"""
        if mime_type.startswith("image/"):
            return Config.BASE64_IMAGE_DIR
        elif mime_type.startswith("audio/"):
            return Config.BASE64_AUDIO_DIR
        elif mime_type.startswith("video/"):
            return Config.BASE64_VIDEO_DIR
        elif mime_type.startswith("text/"):
            return Config.LARGE_TEXT_DIR
        else:
            return Config.BASE64_FILE_DIR

    @classmethod
    def save_base64_file(
        cls, data_uri: str, save_dir: Optional[str] = None
    ) -> Optional[str]:
        """保存Base64文件"""
        result = cls.parse_data_uri(data_uri)
        if not result:
            return None

        mime_type, binary_data = result

        if save_dir is None:
            save_dir = cls.get_save_directory(mime_type)

        ext = cls.MIME_TO_EXT.get(mime_type, ".bin")
        Path(save_dir).mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time() * 1000)
        unique_id = uuid.uuid4().hex[:8]
        filename = f"{timestamp}_{unique_id}{ext}"
        filepath = Path(save_dir) / filename

        try:
            with open(filepath, "wb") as f:
                f.write(binary_data)
            logger.info(
                f"Base64文件已保存: {filepath} ({len(binary_data)} bytes)"
            )
            return str(filepath)
        except Exception as e:
            logger.error(f"保存Base64文件失败: {e}")
            return None

    @classmethod
    def get_file_info_from_data_uri(
        cls, data_uri: str
    ) -> Optional[Dict[str, Any]]:
        """从数据URI获取文件信息"""
        result = cls.parse_data_uri(data_uri)
        if not result:
            return None

        mime_type, binary_data = result
        ext = cls.MIME_TO_EXT.get(mime_type, ".bin")

        return {
            "mime_type": mime_type,
            "size": len(binary_data),
            "extension": ext,
            "file_type": Config.FILE_TYPE_MAPPING.get(mime_type, "file"),
        }


class LargeTextHandler:
    """大文本处理器"""

    @staticmethod
    def is_large_text(text: str) -> bool:
        """检查是否为大文本"""
        if not text or not isinstance(text, str):
            return False
        return len(text) > Config.LARGE_TEXT_THRESHOLD

    @staticmethod
    def process_large_text(text: str) -> Tuple[str, Optional[str]]:
        """
        处理大文本: 保留最新的阈值个字符作为文本, 其余部分保存为文件

        Args:
            text: 原始文本

        Returns:
            Tuple[str, Optional[str]]: (保留的文本, 文件路径) 或 (原始文本, None)
        """
        if not text:
            return text, None

        if not LargeTextHandler.is_large_text(text):
            return text, None

        # 保留最新的阈值个字符
        keep_text = text[-Config.LARGE_TEXT_THRESHOLD:]
        # 其余部分保存为文件
        save_text = text[: -Config.LARGE_TEXT_THRESHOLD]

        try:
            save_dir = Path(Config.LARGE_TEXT_DIR)
            save_dir.mkdir(parents=True, exist_ok=True)

            timestamp = int(time.time() * 1000)
            unique_id = uuid.uuid4().hex[:8]
            filename = f"large_text_{timestamp}_{unique_id}.txt"
            filepath = save_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(save_text)

            logger.info(
                f"大文本部分已保存为文件: {filepath} ({len(save_text)} 字符)"
            )
            logger.info(f"保留文本长度: {len(keep_text)} 字符")

            return keep_text, str(filepath)

        except Exception as e:
            logger.error(f"保存大文本失败: {e}")
            return text, None

    @staticmethod
    def save_large_text(
        text: str, filename: Optional[str] = None
    ) -> Optional[str]:
        """保存大文本为文件"""
        if not text:
            return None

        try:
            save_dir = Path(Config.LARGE_TEXT_DIR)
            save_dir.mkdir(parents=True, exist_ok=True)

            if filename:
                if not filename.lower().endswith(".txt"):
                    filename = f"{filename}.txt"
            else:
                timestamp = int(time.time() * 1000)
                unique_id = uuid.uuid4().hex[:8]
                filename = f"large_text_{timestamp}_{unique_id}.txt"

            filepath = save_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)

            logger.info(
                f"大文本已保存为文件: {filepath} ({len(text)} 字符)"
            )
            return str(filepath)

        except Exception as e:
            logger.error(f"保存大文本失败: {e}")
            return None

    @staticmethod
    def read_large_text(filepath: str) -> Optional[str]:
        """读取大文本文件"""
        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            return content
        except Exception as e:
            logger.error(f"读取大文本文件失败: {e}")
            return None

    @staticmethod
    def cleanup_large_text(filepath: str) -> None:
        """清理大文本临时文件"""
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
                logger.debug(f"已清理大文本临时文件: {filepath}")
        except Exception as e:
            logger.warning(f"清理大文本临时文件失败 {filepath}: {e}")


class RetryManager:
    """重试管理器"""

    @staticmethod
    async def exponential_backoff(attempt: int) -> float:
        """指数退避计算"""
        if attempt <= 0:
            return Config.INITIAL_RETRY_DELAY
        delay = Config.INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
        return min(delay, Config.MAX_RETRY_DELAY)

    @staticmethod
    async def should_retry(
        attempt: int, max_attempts: int = Config.MAX_RETRY_ATTEMPTS
    ) -> bool:
        """判断是否应该重试"""
        return attempt < max_attempts

    @staticmethod
    async def retry_with_backoff(func: Callable, *args: Any, **kwargs: Any) -> Any:
        """带退避的重试"""
        attempt = 0
        last_exception: Optional[Exception] = None

        while await RetryManager.should_retry(attempt):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                attempt += 1
                if await RetryManager.should_retry(attempt):
                    delay = await RetryManager.exponential_backoff(attempt)
                    logger.warning(
                        f"重试 {attempt}/{Config.MAX_RETRY_ATTEMPTS}: {e}, "
                        f"等待 {delay} 秒后重试"
                    )
                    await asyncio.sleep(delay)

        raise last_exception

    @staticmethod
    async def retry_on_empty_response(
        func: Callable,
        *args: Any,
        max_retries: int = Config.EMPTY_RESPONSE_MAX_RETRIES,
        initial_delay: float = Config.EMPTY_RESPONSE_INITIAL_DELAY,
        **kwargs: Any,
    ) -> Any:
        """空响应重试"""
        attempt = 0
        last_result: Any = None

        while attempt < max_retries:
            try:
                result = await func(*args, **kwargs)

                if result is None:
                    raise ValueError("Response is None")

                if isinstance(result, str) and not result.strip():
                    raise ValueError("Response is empty string")

                if isinstance(result, dict):
                    content = result.get("text", result.get("content", ""))
                    if isinstance(content, str) and not content.strip():
                        raise ValueError("Response content is empty")

                return result

            except ValueError as e:
                last_result = None
                attempt += 1
                if attempt < max_retries:
                    delay = initial_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"空响应重试 {attempt}/{max_retries}: {e}, "
                        f"等待 {delay:.1f} 秒后重试"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"空响应重试次数已耗尽: {e}")
                    raise
            except Exception:
                raise

        return last_result


class FingerprintGenerator:
    """指纹生成器"""

    DEFAULT_TEMPLATE = {
        "device_id": "84985177a19a010dea49",
        "sdk_version": "websdk-2.3.15d",
        "init_timestamp": "1765348410850",
        "field3": "91",
        "field4": "1|15",
        "language": "zh-CN",
        "timezone_offset": "-480",
        "color_depth": "16705151|12791",
        "screen_info": "1470|956|283|797|158|0|1470|956|1470|798|0|0",
        "field9": "5",
        "platform": "MacIntel",
        "field11": "10",
        "webgl_renderer": (
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M4, "
            "Unspecified Version) | Google Inc. (Apple)"
        ),
        "field13": "30|30",
        "field14": "0",
        "field15": "28",
        "plugin_count": "5",
        "vendor": "Google Inc.",
        "field29": "8",
        "touch_info": "-1|0|0|0|0",
        "field32": "11",
        "field35": "0",
        "mode": "P",
    }

    HASH_FIELDS = {
        16: "split",
        17: "full",
        18: "full",
        31: "full",
        34: "full",
        36: "full",
    }

    @staticmethod
    def _generate_device_id() -> str:
        """生成设备ID"""
        return "".join(
            secrets.choice("0123456789abcdef") for _ in range(20)
        )

    @staticmethod
    def _generate_hash() -> int:
        """生成哈希值"""
        return secrets.randbelow(4294967296)

    @staticmethod
    def _lzw_compress(data: str, bits: int, char_func: Callable) -> str:
        """LZW压缩"""
        if not data:
            return ""

        dictionary: Dict[str, int] = {}
        dict_to_create: Dict[str, bool] = {}
        w = ""
        enlarge_in = 2
        dict_size = 3
        num_bits = 2
        result: List[str] = []
        value = 0
        position = 0

        for c in data:
            if c not in dictionary:
                dictionary[c] = dict_size
                dict_size += 1
                dict_to_create[c] = True

            wc = w + c
            if wc in dictionary:
                w = wc
            else:
                if w in dict_to_create:
                    if ord(w[0]) < 256:
                        for _ in range(num_bits):
                            value <<= 1
                            if position == bits - 1:
                                position = 0
                                result.append(char_func(value))
                                value = 0
                            else:
                                position += 1

                        char_code = ord(w[0])
                        for _ in range(8):
                            value = (value << 1) | (char_code & 1)
                            if position == bits - 1:
                                position = 0
                                result.append(char_func(value))
                                value = 0
                            else:
                                position += 1
                            char_code >>= 1
                    else:
                        char_code = 1
                        for _ in range(num_bits):
                            value = (value << 1) | char_code
                            if position == bits - 1:
                                position = 0
                                result.append(char_func(value))
                                value = 0
                            else:
                                position += 1
                            char_code = 0

                        char_code = ord(w[0])
                        for _ in range(16):
                            value = (value << 1) | (char_code & 1)
                            if position == bits - 1:
                                position = 0
                                result.append(char_func(value))
                                value = 0
                            else:
                                position += 1
                            char_code >>= 1

                    enlarge_in -= 1
                    if enlarge_in == 0:
                        enlarge_in = 2**num_bits
                        num_bits += 1
                    del dict_to_create[w]
                else:
                    char_code = dictionary[w]
                    for _ in range(num_bits):
                        value = (value << 1) | (char_code & 1)
                        if position == bits - 1:
                            position = 0
                            result.append(char_func(value))
                            value = 0
                        else:
                            position += 1
                        char_code >>= 1

                    enlarge_in -= 1
                    if enlarge_in == 0:
                        enlarge_in = 2**num_bits
                        num_bits += 1

                dictionary[wc] = dict_size
                dict_size += 1
                w = c

        if w:
            if w in dict_to_create:
                if ord(w[0]) < 256:
                    for _ in range(num_bits):
                        value <<= 1
                        if position == bits - 1:
                            position = 0
                            result.append(char_func(value))
                            value = 0
                        else:
                            position += 1

                    char_code = ord(w[0])
                    for _ in range(8):
                        value = (value << 1) | (char_code & 1)
                        if position == bits - 1:
                            position = 0
                            result.append(char_func(value))
                            value = 0
                        else:
                            position += 1
                        char_code >>= 1
                else:
                    char_code = 1
                    for _ in range(num_bits):
                        value = (value << 1) | char_code
                        if position == bits - 1:
                            position = 0
                            result.append(char_func(value))
                            value = 0
                        else:
                            position += 1
                        char_code = 0

                    char_code = ord(w[0])
                    for _ in range(16):
                        value = (value << 1) | (char_code & 1)
                        if position == bits - 1:
                            position = 0
                            result.append(char_func(value))
                            value = 0
                        else:
                            position += 1
                        char_code >>= 1

                enlarge_in -= 1
                if enlarge_in == 0:
                    enlarge_in = 2**num_bits
                    num_bits += 1
                del dict_to_create[w]
            else:
                char_code = dictionary[w]
                for _ in range(num_bits):
                    value = (value << 1) | (char_code & 1)
                    if position == bits - 1:
                        position = 0
                        result.append(char_func(value))
                        value = 0
                    else:
                        position += 1
                    char_code >>= 1

                enlarge_in -= 1
                if enlarge_in == 0:
                    enlarge_in = 2**num_bits
                    num_bits += 1

        char_code = 2
        for _ in range(num_bits):
            value = (value << 1) | (char_code & 1)
            if position == bits - 1:
                position = 0
                result.append(char_func(value))
                value = 0
            else:
                position += 1
            char_code >>= 1

        while True:
            value <<= 1
            if position == bits - 1:
                result.append(char_func(value))
                break
            position += 1

        return "".join(result)

    @staticmethod
    def _custom_encode(data: str, url_safe: bool = True) -> str:
        """自定义编码"""
        if not data:
            return ""

        compressed = FingerprintGenerator._lzw_compress(
            data, 6, lambda index: Config.CUSTOM_BASE64_CHARS[index]
        )

        if not url_safe:
            remainder = len(compressed) % 4
            if remainder == 1:
                return compressed + "==="
            elif remainder == 2:
                return compressed + "=="
            elif remainder == 3:
                return compressed + "="

        return compressed

    @classmethod
    def generate_fingerprint(cls) -> str:
        """生成指纹"""
        device_id = cls._generate_device_id()
        current_timestamp = int(time.time() * 1000)
        plugin_hash = cls._generate_hash()
        canvas_hash = cls._generate_hash()
        ua_hash1 = cls._generate_hash()
        ua_hash2 = cls._generate_hash()
        url_hash = cls._generate_hash()
        doc_hash = secrets.randbelow(91) + 10

        fields = [
            device_id,
            cls.DEFAULT_TEMPLATE["sdk_version"],
            cls.DEFAULT_TEMPLATE["init_timestamp"],
            cls.DEFAULT_TEMPLATE["field3"],
            cls.DEFAULT_TEMPLATE["field4"],
            cls.DEFAULT_TEMPLATE["language"],
            cls.DEFAULT_TEMPLATE["timezone_offset"],
            cls.DEFAULT_TEMPLATE["color_depth"],
            cls.DEFAULT_TEMPLATE["screen_info"],
            cls.DEFAULT_TEMPLATE["field9"],
            cls.DEFAULT_TEMPLATE["platform"],
            cls.DEFAULT_TEMPLATE["field11"],
            cls.DEFAULT_TEMPLATE["webgl_renderer"],
            cls.DEFAULT_TEMPLATE["field13"],
            cls.DEFAULT_TEMPLATE["field14"],
            cls.DEFAULT_TEMPLATE["field15"],
            f"{cls.DEFAULT_TEMPLATE['plugin_count']}|{plugin_hash}",
            str(canvas_hash),
            str(ua_hash1),
            "1",
            "0",
            "1",
            "0",
            cls.DEFAULT_TEMPLATE["mode"],
            "0",
            "0",
            "0",
            "416",
            cls.DEFAULT_TEMPLATE["vendor"],
            cls.DEFAULT_TEMPLATE["field29"],
            cls.DEFAULT_TEMPLATE["touch_info"],
            str(ua_hash2),
            cls.DEFAULT_TEMPLATE["field32"],
            str(current_timestamp),
            str(url_hash),
            cls.DEFAULT_TEMPLATE["field35"],
            str(doc_hash),
        ]

        return "^".join(fields)

    @classmethod
    def generate_cookies(cls) -> Dict[str, Any]:
        """生成Cookie"""
        fingerprint_data = cls.generate_fingerprint()
        fields = fingerprint_data.split("^")
        current_timestamp = int(time.time() * 1000)
        fields[33] = str(current_timestamp)

        for index, field_type in cls.HASH_FIELDS.items():
            if field_type == "split":
                parts = fields[index].split("|")
                if len(parts) == 2:
                    fields[index] = f"{parts[0]}|{cls._generate_hash()}"
            elif field_type == "full":
                if index == 36:
                    fields[index] = str(secrets.randbelow(91) + 10)
                else:
                    fields[index] = str(cls._generate_hash())

        ssxmod_itna_data = "^".join(fields)
        ssxmod_itna = "1-" + cls._custom_encode(ssxmod_itna_data, True)

        ssxmod_itna2_fields = [
            fields[0],
            fields[1],
            fields[23],
            "0",
            "",
            "0",
            "",
            "",
            "0",
            "0",
            "0",
            fields[32],
            fields[33],
            "0",
            "0",
            "0",
            "0",
            "0",
        ]
        ssxmod_itna2_data = "^".join(ssxmod_itna2_fields)
        ssxmod_itna2 = "1-" + cls._custom_encode(ssxmod_itna2_data, True)

        return {
            "ssxmod_itna": ssxmod_itna,
            "ssxmod_itna2": ssxmod_itna2,
            "timestamp": current_timestamp,
        }


class CookieManager:
    """Cookie管理器 (单例模式)"""

    _instance: Optional["CookieManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "CookieManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._cookies = None
                    cls._instance._last_refresh = 0.0
        return cls._instance

    def ensure_fresh(self) -> None:
        """确保Cookie新鲜"""
        current_time = time.time()
        if (
            self._cookies is None
            or current_time - self._last_refresh
            > Config.COOKIE_REFRESH_INTERVAL
        ):
            self._cookies = FingerprintGenerator.generate_cookies()
            self._last_refresh = current_time

    @property
    def ssxmod_itna(self) -> str:
        """获取ssxmod_itna"""
        self.ensure_fresh()
        return self._cookies["ssxmod_itna"]

    @property
    def ssxmod_itna2(self) -> str:
        """获取ssxmod_itna2"""
        self.ensure_fresh()
        return self._cookies["ssxmod_itna2"]


_cookie_manager = CookieManager()


def get_ssxmod_itna() -> str:
    """获取ssxmod_itna"""
    return _cookie_manager.ssxmod_itna


def get_ssxmod_itna2() -> str:
    """获取ssxmod_itna2"""
    return _cookie_manager.ssxmod_itna2


# ==================== 数据类定义 ====================


@dataclass
class AccountPerformanceSample:
    """单次账号性能采样记录"""

    timestamp: float
    latency: float  # 首Token延迟(秒)
    success: bool
    tokens_generated: int
    total_duration: float  # 总耗时(秒)
    model: str


@dataclass
class AccountStats:
    """
    账号统计数据 - Track-and-Stop 算法核心状态

    使用 Beta 分布建模成功率: Beta(alpha, beta)
    使用滑动窗口维护近期性能指标
    """

    # Beta分布参数 (成功率先验)
    alpha: float = 1.0  # 成功次数 + 1 (Laplace平滑)
    beta_param: float = 1.0  # 失败次数 + 1

    # 性能统计
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0

    # 滑动窗口
    recent_samples: Deque[AccountPerformanceSample] = field(
        default_factory=lambda: deque(maxlen=Config.TAS_WINDOW_SIZE)
    )

    # 延迟统计 (指数移动平均)
    ema_latency: float = 1.0  # 初始假设1秒
    ema_throughput: float = 10.0  # 初始假设10 tokens/s

    # 冷却状态
    last_failure_time: float = 0.0
    consecutive_failures: int = 0

    # 探索计数
    exploration_count: int = 0
    exploitation_count: int = 0

    def record_success(
        self,
        latency: float,
        tokens: int,
        duration: float,
        model: str,
    ) -> None:
        """记录成功请求"""
        self.total_requests += 1
        self.total_successes += 1
        self.consecutive_failures = 0

        # 更新 Beta 分布
        self.alpha += 1.0

        # 指数移动平均 (EMA), 平滑因子 0.2
        ema_alpha = 0.2
        self.ema_latency = (
            ema_alpha * latency + (1 - ema_alpha) * self.ema_latency
        )

        if duration > 0 and tokens > 0:
            throughput = tokens / duration
            self.ema_throughput = (
                ema_alpha * throughput
                + (1 - ema_alpha) * self.ema_throughput
            )

        self.recent_samples.append(
            AccountPerformanceSample(
                timestamp=time.time(),
                latency=latency,
                success=True,
                tokens_generated=tokens,
                total_duration=duration,
                model=model,
            )
        )

    def record_failure(self, model: str) -> None:
        """记录失败请求"""
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        # 更新 Beta 分布
        self.beta_param += 1.0

        self.recent_samples.append(
            AccountPerformanceSample(
                timestamp=time.time(),
                latency=float("inf"),
                success=False,
                tokens_generated=0,
                total_duration=0.0,
                model=model,
            )
        )

    @property
    def success_rate(self) -> float:
        """后验成功率均值: E[Beta(a,b)] = a / (a + b)"""
        return self.alpha / (self.alpha + self.beta_param)

    @property
    def success_rate_variance(self) -> float:
        """后验成功率方差: Var[Beta(a,b)]"""
        total = self.alpha + self.beta_param
        return (self.alpha * self.beta_param) / (
            total * total * (total + 1)
        )

    @property
    def is_cooling_down(self) -> bool:
        """是否在冷却期"""
        if self.consecutive_failures == 0:
            return False
        cooldown = Config.TAS_COOLDOWN_PERIOD * min(
            self.consecutive_failures, 10
        )
        return (time.time() - self.last_failure_time) < cooldown

    @property
    def window_success_rate(self) -> float:
        """滑动窗口内成功率"""
        if not self.recent_samples:
            return 0.5
        successes = sum(1 for s in self.recent_samples if s.success)
        return successes / len(self.recent_samples)

    @property
    def window_avg_latency(self) -> float:
        """滑动窗口内平均延迟"""
        successful = [
            s.latency for s in self.recent_samples if s.success
        ]
        if not successful:
            return self.ema_latency
        return sum(successful) / len(successful)

    def thompson_sample(self) -> float:
        """
        Thompson Sampling: 从 Beta(alpha, beta) 分布中采样

        使用 Gamma 分布近似采样以提升数值稳定性:
        X ~ Gamma(alpha, 1), Y ~ Gamma(beta, 1)
        则 X / (X + Y) ~ Beta(alpha, beta)
        """
        import random

        try:
            x = random.gammavariate(self.alpha, 1.0)
            y = random.gammavariate(self.beta_param, 1.0)
            if x + y == 0:
                return 0.5
            return x / (x + y)
        except (ValueError, ZeroDivisionError):
            return 0.5

    def compute_composite_score(self) -> float:
        """
        计算综合质量评分

        Score = w_s * success_rate + w_l * (1 - norm_latency) + w_t * norm_throughput

        其中延迟和吞吐量使用 sigmoid 归一化到 [0, 1]
        """
        # 成功率分量 (直接使用后验均值)
        sr = self.success_rate

        # 延迟分量: sigmoid(-(latency - 2)) 将2秒延迟映射到0.5
        lat = self.ema_latency
        norm_latency = 1.0 / (1.0 + math.exp(lat - 2.0))

        # 吞吐量分量: sigmoid((throughput - 5) / 5) 将5 tok/s映射到0.5
        thr = self.ema_throughput
        norm_throughput = 1.0 / (1.0 + math.exp(-(thr - 5.0) / 5.0))

        score = (
            Config.TAS_SUCCESS_WEIGHT * sr
            + Config.TAS_LATENCY_WEIGHT * norm_latency
            + Config.TAS_THROUGHPUT_WEIGHT * norm_throughput
        )
        return score

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "alpha": self.alpha,
            "beta_param": self.beta_param,
            "total_requests": self.total_requests,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "ema_latency": self.ema_latency,
            "ema_throughput": self.ema_throughput,
            "last_failure_time": self.last_failure_time,
            "consecutive_failures": self.consecutive_failures,
            "exploration_count": self.exploration_count,
            "exploitation_count": self.exploitation_count,
            "success_rate": self.success_rate,
            "composite_score": self.compute_composite_score(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountStats":
        """从字典反序列化"""
        stats = cls()
        stats.alpha = data.get("alpha", 1.0)
        stats.beta_param = data.get("beta_param", 1.0)
        stats.total_requests = data.get("total_requests", 0)
        stats.total_successes = data.get("total_successes", 0)
        stats.total_failures = data.get("total_failures", 0)
        stats.ema_latency = data.get("ema_latency", 1.0)
        stats.ema_throughput = data.get("ema_throughput", 10.0)
        stats.last_failure_time = data.get("last_failure_time", 0.0)
        stats.consecutive_failures = data.get("consecutive_failures", 0)
        stats.exploration_count = data.get("exploration_count", 0)
        stats.exploitation_count = data.get("exploitation_count", 0)
        return stats


@dataclass
class Account:
    """账号数据类"""

    email: str
    password: str
    password_hash: str = field(init=False)
    token: str = ""
    token_expires: float = 0
    user_id: str = ""
    last_used: float = 0
    is_busy: bool = False
    is_logged_in: bool = False
    is_initializing: bool = False
    login_attempts: int = 0
    memory_disabled: bool = False
    last_error: Optional[str] = None
    error_time: float = 0

    # Track-and-Stop 统计
    stats: AccountStats = field(default_factory=AccountStats)

    def __post_init__(self) -> None:
        self.password_hash = hashlib.sha256(
            self.password.encode("utf-8")
        ).hexdigest()

    def mark_error(self, error: str) -> None:
        """标记错误"""
        self.last_error = error
        self.error_time = time.time()

    def clear_error(self) -> None:
        """清除错误"""
        self.last_error = None
        self.error_time = 0


@dataclass
class FileInfo:
    """文件信息数据类"""

    file_id: str
    file_url: str
    filename: str
    size: int
    content_type: str
    user_id: str
    file_type: str
    file_class: str


@dataclass
class ExtractedFile:
    """提取的文件数据类"""

    source: str  # base64, url, local
    path_or_url: str
    mime_type: Optional[str] = None
    original_data: Optional[str] = None


@dataclass
class TokenCountResult:
    """Token计数结果"""

    input_tokens: int
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class LargeTextInfo:
    """大文本信息"""

    file_path: str
    original_text: str
    file_name: str
    file_size: int
    is_converted: bool = True


@dataclass
class PreCreatedChatId:
    """预创建的ChatID"""

    chat_id: str
    model: str
    account_email: str
    created_at: float
    expires_at: float
    is_used: bool = False


@dataclass
class CachedFileUpload:
    """缓存的文件上传"""

    file_hash: str
    file_objects: List[Dict[str, Any]]
    account_email: str
    created_at: float
    expires_at: float


@dataclass
class VideoGenerationResult:
    """视频生成结果"""

    success: bool
    request_id: str
    chat_id: str
    message_id: str
    task_id: str
    size: str
    video_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DeepResearchResult:
    """深度研究结果"""

    content: str
    stage: str
    lang_code: str
    status: str
    version: str
    usage: Optional[Dict[str, Any]] = None


@dataclass
class EmbeddingResult:
    """嵌入向量结果"""

    embedding: List[float]
    model: str
    usage: Optional[Dict[str, int]] = None


@dataclass
class ThinkingSummary:
    """思考摘要"""

    title: List[str]
    thought: List[str]


@dataclass
class ImageEditResult:
    """图像编辑结果"""

    image_url: str
    width: int
    height: int
    revised_prompt: Optional[str] = None


@dataclass
class FeatureConfig:
    """功能配置"""

    thinking_enabled: bool = False
    output_schema: str = "phase"
    research_mode: str = "normal"
    auto_search: bool = True
    thinking_format: str = "summary"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "thinking_enabled": self.thinking_enabled,
            "output_schema": self.output_schema,
            "research_mode": self.research_mode,
            "auto_search": self.auto_search,
            "thinking_format": self.thinking_format,
        }


@dataclass
class StreamCheckpoint:
    """流式请求断点记录 - 用于断点续传"""

    checkpoint_id: str
    message: str
    model: str
    chat_id: str
    account_email: str
    parent_id: Optional[str]
    accumulated_content: str
    accumulated_chunks: List[Any]
    tokens_received: int
    last_chunk_time: float
    created_at: float
    chat_type: str
    sub_chat_type: Optional[str]
    feature_config_dict: Dict[str, Any]
    file_objects: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """序列化"""
        return {
            "checkpoint_id": self.checkpoint_id,
            "message": self.message,
            "model": self.model,
            "chat_id": self.chat_id,
            "account_email": self.account_email,
            "parent_id": self.parent_id,
            "accumulated_content": self.accumulated_content,
            "tokens_received": self.tokens_received,
            "last_chunk_time": self.last_chunk_time,
            "created_at": self.created_at,
            "chat_type": self.chat_type,
            "sub_chat_type": self.sub_chat_type,
            "feature_config_dict": self.feature_config_dict,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamCheckpoint":
        """反序列化"""
        return cls(
            checkpoint_id=data["checkpoint_id"],
            message=data["message"],
            model=data["model"],
            chat_id=data["chat_id"],
            account_email=data["account_email"],
            parent_id=data.get("parent_id"),
            accumulated_content=data.get("accumulated_content", ""),
            accumulated_chunks=[],
            tokens_received=data.get("tokens_received", 0),
            last_chunk_time=data.get("last_chunk_time", 0.0),
            created_at=data.get("created_at", 0.0),
            chat_type=data.get("chat_type", "t2t"),
            sub_chat_type=data.get("sub_chat_type"),
            feature_config_dict=data.get("feature_config_dict", {}),
            file_objects=[],
        )

    @property
    def is_expired(self) -> bool:
        """检查断点是否过期"""
        return (
            time.time() - self.created_at > Config.TAS_CHECKPOINT_TTL
        )


# ==================== 账号解析器 ====================


class AccountParser:
    """账号解析器"""

    USER_KEYS = frozenset(
        {"user", "username", "email", "account", "name", "login"}
    )
    PWD_KEYS = frozenset(
        {"pwd", "password", "passwd", "pass", "secret", "token"}
    )

    @classmethod
    def parse(cls, accounts_config: Any) -> List[Tuple[str, str]]:
        """解析账号配置"""
        result: List[Tuple[str, str]] = []
        seen_users: set = set()

        if accounts_config is None:
            logger.warning("账号配置为空")
            return result

        if isinstance(accounts_config, dict):
            if cls._is_standard_dict(accounts_config):
                parsed = cls._parse_standard_dict(accounts_config)
                if parsed:
                    user, pwd = parsed
                    normalized_user = user.strip().lower()
                    if (
                        normalized_user
                        and normalized_user not in seen_users
                    ):
                        result.append((user.strip(), pwd.strip()))
                        seen_users.add(normalized_user)
            else:
                for user, pwd in accounts_config.items():
                    user_str = str(user).strip()
                    pwd_str = str(pwd).strip()
                    normalized_user = user_str.lower()
                    if (
                        user_str
                        and normalized_user not in seen_users
                    ):
                        result.append((user_str, pwd_str))
                        seen_users.add(normalized_user)
            return result

        if hasattr(accounts_config, "__iter__") and not isinstance(
            accounts_config, str
        ):
            for item in accounts_config:
                parsed_items = cls._parse_item(item)
                for user, pwd in parsed_items:
                    normalized_user = user.strip().lower()
                    if (
                        normalized_user
                        and normalized_user not in seen_users
                    ):
                        result.append((user.strip(), pwd.strip()))
                        seen_users.add(normalized_user)

        return result

    @classmethod
    def _is_standard_dict(cls, d: dict) -> bool:
        """检查是否为标准字典格式"""
        keys_lower = {str(k).lower() for k in d.keys()}
        has_user_key = bool(keys_lower & cls.USER_KEYS)
        has_pwd_key = bool(keys_lower & cls.PWD_KEYS)
        return has_user_key and has_pwd_key

    @classmethod
    def _parse_standard_dict(
        cls, d: dict
    ) -> Optional[Tuple[str, str]]:
        """解析标准字典"""
        user: Optional[str] = None
        pwd: Optional[str] = None

        for key, value in d.items():
            key_lower = str(key).lower()
            if key_lower in cls.USER_KEYS and user is None:
                user = str(value)
            elif key_lower in cls.PWD_KEYS and pwd is None:
                pwd = str(value)

        if user is not None and pwd is not None:
            return (user, pwd)
        return None

    @classmethod
    def _parse_item(cls, item: Any) -> List[Tuple[str, str]]:
        """解析单个配置项"""
        result: List[Tuple[str, str]] = []

        if item is None:
            return result

        if isinstance(item, str):
            user = item.strip()
            if user:
                result.append((user, user))
            return result

        if isinstance(item, dict):
            if not item:
                return result

            if cls._is_standard_dict(item):
                parsed = cls._parse_standard_dict(item)
                if parsed:
                    result.append(parsed)
                return result

            for key, value in item.items():
                user = str(key).strip()
                pwd = str(value).strip()
                if user:
                    result.append((user, pwd))
            return result

        if isinstance(item, (tuple, list)):
            if len(item) >= 2:
                user = str(item[0]).strip()
                pwd = str(item[1]).strip()
                if user:
                    result.append((user, pwd))
            elif len(item) == 1:
                user = str(item[0]).strip()
                if user:
                    result.append((user, user))
            return result

        try:
            user = str(item).strip()
            if user:
                result.append((user, user))
        except Exception as e:
            logger.warning(f"无法解析账号配置项: {item}, 错误: {e}")

        return result

    @classmethod
    def validate_credentials(cls, user: str, pwd: str) -> bool:
        """验证凭据"""
        if not user or not isinstance(user, str):
            return False
        if not pwd or not isinstance(pwd, str):
            return False
        if not user.strip():
            return False
        return True


# ==================== 文件工具类 ====================


class FileUtils:
    """文件工具类"""

    @staticmethod
    def get_mime_type(filename: str) -> str:
        """获取MIME类型"""
        ext = os.path.splitext(filename)[1].lower()
        if ext in Config.EXTENSION_TO_MIME:
            return Config.EXTENSION_TO_MIME[ext]
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"

    @staticmethod
    def get_file_category(content_type: str) -> Tuple[str, str]:
        """获取文件分类"""
        if content_type in Config.FILE_TYPE_MAPPING:
            file_type = Config.FILE_TYPE_MAPPING[content_type]
        else:
            file_type = "file"

        if content_type.startswith("image/"):
            file_class = "vision"
        elif content_type.startswith("video/"):
            file_class = "vision"
        elif content_type.startswith("audio/"):
            file_class = "audio"
        elif content_type.startswith("text/"):
            file_class = "document"
        else:
            file_class = "document"

        return file_type, file_class

    @staticmethod
    def is_url(path: str) -> bool:
        """检查是否为URL"""
        return path.startswith(("http://", "https://"))

    @staticmethod
    def is_base64_data_uri(data: str) -> bool:
        """检查是否为Base64数据URI"""
        return Base64FileHandler.is_base64_data_uri(data)

    @staticmethod
    def get_filename_from_url(url: str) -> str:
        """从URL获取文件名"""
        parsed = urlparse(url)
        path = parsed.path
        if path:
            filename = os.path.basename(path)
            if filename and "." in filename:
                return filename
        return f"url_file_{int(time.time())}.jpg"

    @staticmethod
    async def get_url_file_info(
        session: aiohttp.ClientSession, url: str, user_id: str
    ) -> FileInfo:
        """获取URL文件信息"""
        try:
            async with session.head(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                content_type = response.headers.get(
                    "Content-Type", "image/jpeg"
                )
                if ";" in content_type:
                    content_type = content_type.split(";")[0].strip()

                content_length = response.headers.get("Content-Length")
                size = int(content_length) if content_length else 0

                filename = FileUtils.get_filename_from_url(url)
                if filename != f"url_file_{int(time.time())}.jpg":
                    inferred_type = FileUtils.get_mime_type(filename)
                    if inferred_type != "application/octet-stream":
                        content_type = inferred_type

                file_type, file_class = FileUtils.get_file_category(
                    content_type
                )

                return FileInfo(
                    file_id=str(uuid.uuid4()),
                    file_url=url,
                    filename=filename,
                    size=size,
                    content_type=content_type,
                    user_id=user_id,
                    file_type=file_type,
                    file_class=file_class,
                )

        except Exception as e:
            logger.warning(f"获取URL文件信息失败 {url}: {e}")
            filename = FileUtils.get_filename_from_url(url)
            return FileInfo(
                file_id=str(uuid.uuid4()),
                file_url=url,
                filename=filename,
                size=0,
                content_type="image/jpeg",
                user_id=user_id,
                file_type="image",
                file_class="vision",
            )

    @staticmethod
    def cleanup_temp_file(filepath: str) -> None:
        """清理临时文件"""
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
                logger.debug(f"已清理临时文件: {filepath}")
        except Exception as e:
            logger.warning(f"清理临时文件失败 {filepath}: {e}")

    @staticmethod
    def cleanup_temp_files(filepaths: List[str]) -> None:
        """批量清理临时文件"""
        for filepath in filepaths:
            FileUtils.cleanup_temp_file(filepath)

    @staticmethod
    def copy_local_file_to_temp(filepath: str) -> Optional[str]:
        """复制本地文件到临时目录"""
        try:
            if not os.path.exists(filepath):
                logger.error(f"本地文件不存在: {filepath}")
                return None

            mime_type = FileUtils.get_mime_type(filepath)
            save_dir = Base64FileHandler.get_save_directory(mime_type)
            Path(save_dir).mkdir(parents=True, exist_ok=True)

            _, ext = os.path.splitext(filepath)
            if not ext:
                ext = Base64FileHandler.MIME_TO_EXT.get(
                    mime_type, ".bin"
                )

            timestamp = int(time.time() * 1000)
            unique_id = uuid.uuid4().hex[:8]
            new_filename = f"{timestamp}_{unique_id}{ext}"
            new_filepath = Path(save_dir) / new_filename

            shutil.copy2(filepath, new_filepath)
            logger.info(
                f"本地文件已复制到临时目录: {filepath} -> {new_filepath}"
            )
            return str(new_filepath)

        except Exception as e:
            logger.error(f"复制本地文件失败 {filepath}: {e}")
            return None

    @staticmethod
    def save_text_as_file(
        text: str, filename: Optional[str] = None
    ) -> Optional[str]:
        """保存文本为文件"""
        return LargeTextHandler.save_large_text(text, filename)

    @staticmethod
    def process_large_text(text: str) -> Tuple[str, Optional[str]]:
        """处理大文本: 保留最新的阈值个字符作为文本, 其余部分保存为文件"""
        return LargeTextHandler.process_large_text(text)

    @staticmethod
    def is_large_text(text: str) -> bool:
        """检查是否为大文本"""
        return LargeTextHandler.is_large_text(text)

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """计算文件哈希"""
        try:
            hasher = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return str(uuid.uuid4())

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """计算内容哈希"""
        return hashlib.md5(
            content[:10000].encode("utf-8", errors="ignore")
        ).hexdigest()


# ==================== 嵌入向量服务 ====================


class EmbeddingService:
    """嵌入向量服务"""

    def __init__(
        self,
        url: str = Config.EMBEDDING_API_URL,
        model: str = Config.EMBEDDING_MODEL,
        timeout: int = Config.EMBEDDING_TIMEOUT,
    ) -> None:
        self.url = url
        self.model = model
        self.timeout = timeout

    def get_embedding(self, text: str) -> List[float]:
        """
        获取文本的嵌入向量 (同步方法)

        Args:
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        headers = {"Content-Type": "application/json"}

        data = {"model": self.model, "input": text}

        try:
            response = requests.post(
                self.url,
                headers=headers,
                data=json.dumps(data),
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
            return result["data"][0]["embedding"]
        except Exception as e:
            logger.error(f"获取嵌入向量失败: {e}")
            raise

    async def get_embedding_async(
        self, session: aiohttp.ClientSession, text: str
    ) -> List[float]:
        """
        获取文本的嵌入向量 (异步方法)

        Args:
            session: aiohttp会话
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        headers = {"Content-Type": "application/json"}

        data = {"model": self.model, "input": text}

        try:
            async with session.post(
                self.url,
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                response.raise_for_status()
                result = await response.json()
                return result["data"][0]["embedding"]
        except Exception as e:
            logger.error(f"获取嵌入向量失败: {e}")
            raise

    async def get_embeddings_batch(
        self, session: aiohttp.ClientSession, texts: List[str]
    ) -> List[List[float]]:
        """
        批量获取文本的嵌入向量

        Args:
            session: aiohttp会话
            texts: 输入文本列表

        Returns:
            嵌入向量列表的列表
        """
        tasks = [
            self.get_embedding_async(session, text) for text in texts
        ]
        return await asyncio.gather(*tasks)


# ==================== 缓存管理器 ====================


class FileCacheManager:
    """文件缓存管理器"""

    def __init__(self) -> None:
        self._cache: Dict[str, CachedFileUpload] = {}
        self._lock = Lock()
        self._max_size = Config.FILE_CACHE_MAX_SIZE
        self._ttl = Config.FILE_CACHE_TTL

    async def get(
        self, file_hash: str, account_email: str
    ) -> Optional[List[Dict[str, Any]]]:
        """获取缓存的文件对象"""
        async with self._lock:
            cache_key = f"{file_hash}:{account_email}"
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                if time.time() < cached.expires_at:
                    return cached.file_objects
                else:
                    del self._cache[cache_key]
            return None

    async def set(
        self,
        file_hash: str,
        account_email: str,
        file_objects: List[Dict[str, Any]],
    ) -> None:
        """设置文件缓存"""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                k
                for k, v in self._cache.items()
                if current_time >= v.expires_at
            ]
            for key in expired_keys:
                del self._cache[key]

            if len(self._cache) >= self._max_size:
                oldest_key = min(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].created_at,
                )
                del self._cache[oldest_key]

            cache_key = f"{file_hash}:{account_email}"
            self._cache[cache_key] = CachedFileUpload(
                file_hash=file_hash,
                file_objects=file_objects,
                account_email=account_email,
                created_at=current_time,
                expires_at=current_time + self._ttl,
            )

    async def clear(self) -> None:
        """清空缓存"""
        async with self._lock:
            self._cache.clear()

    async def cleanup_expired(self) -> int:
        """清理过期缓存"""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                k
                for k, v in self._cache.items()
                if current_time >= v.expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)


# ==================== ChatID池管理器 ====================


class ChatIdPool:
    """ChatID池管理器"""

    def __init__(self) -> None:
        self._pool: Dict[str, List[PreCreatedChatId]] = {}
        self._lock = Lock()
        self._pool_size = Config.CHAT_ID_POOL_SIZE
        self._refresh_interval = Config.CHAT_ID_REFRESH_INTERVAL

    async def get(
        self, model: str, account_email: str
    ) -> Optional[str]:
        """获取预创建的ChatID"""
        async with self._lock:
            if model not in self._pool:
                return None

            current_time = time.time()
            available = [
                cid
                for cid in self._pool[model]
                if not cid.is_used
                and cid.account_email == account_email
                and current_time < cid.expires_at
            ]

            if available:
                chat_id_obj = available[0]
                chat_id_obj.is_used = True
                return chat_id_obj.chat_id

            return None

    async def add(
        self,
        chat_id: str,
        model: str,
        account_email: str,
        ttl: Optional[float] = None,
    ) -> None:
        """添加预创建的ChatID"""
        async with self._lock:
            if model not in self._pool:
                self._pool[model] = []

            current_time = time.time()
            ttl = ttl or self._refresh_interval

            self._pool[model].append(
                PreCreatedChatId(
                    chat_id=chat_id,
                    model=model,
                    account_email=account_email,
                    created_at=current_time,
                    expires_at=current_time + ttl,
                )
            )

    async def cleanup_expired(self) -> int:
        """清理过期的ChatID"""
        async with self._lock:
            current_time = time.time()
            cleaned = 0

            for model in list(self._pool.keys()):
                original_count = len(self._pool[model])
                self._pool[model] = [
                    cid
                    for cid in self._pool[model]
                    if current_time < cid.expires_at
                    and not cid.is_used
                ]
                cleaned += original_count - len(self._pool[model])

                if not self._pool[model]:
                    del self._pool[model]

            return cleaned

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        async with self._lock:
            current_time = time.time()
            stats: Dict[str, Any] = {}

            for model, chat_ids in self._pool.items():
                available = len(
                    [
                        cid
                        for cid in chat_ids
                        if not cid.is_used
                        and current_time < cid.expires_at
                    ]
                )
                stats[model] = {
                    "total": len(chat_ids),
                    "available": available,
                    "used": len(
                        [cid for cid in chat_ids if cid.is_used]
                    ),
                    "expired": len(
                        [
                            cid
                            for cid in chat_ids
                            if current_time >= cid.expires_at
                        ]
                    ),
                }

            return stats


# ==================== 断点续传管理器 ====================


class CheckpointManager:
    """
    断点续传管理器

    在流式传输中断时保存断点, 支持从断点恢复继续传输
    """

    def __init__(self) -> None:
        self._checkpoints: Dict[str, StreamCheckpoint] = {}
        self._lock = Lock()
        self._checkpoint_dir = Path(Config.TAS_CHECKPOINT_DIR)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

    async def save_checkpoint(
        self, checkpoint: StreamCheckpoint
    ) -> None:
        """保存断点"""
        async with self._lock:
            self._checkpoints[
                checkpoint.checkpoint_id
            ] = checkpoint

            try:
                filepath = (
                    self._checkpoint_dir
                    / f"{checkpoint.checkpoint_id}.json"
                )
                data = checkpoint.to_dict()
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"保存断点到磁盘失败: {e}")

    async def get_checkpoint(
        self, checkpoint_id: str
    ) -> Optional[StreamCheckpoint]:
        """获取断点"""
        async with self._lock:
            cp = self._checkpoints.get(checkpoint_id)
            if cp and not cp.is_expired:
                return cp

            # 尝试从磁盘加载
            try:
                filepath = (
                    self._checkpoint_dir / f"{checkpoint_id}.json"
                )
                if filepath.exists():
                    with open(
                        filepath, "r", encoding="utf-8"
                    ) as f:
                        data = json.load(f)
                    cp = StreamCheckpoint.from_dict(data)
                    if not cp.is_expired:
                        self._checkpoints[checkpoint_id] = cp
                        return cp
                    else:
                        filepath.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"从磁盘加载断点失败: {e}")

            return None

    async def remove_checkpoint(self, checkpoint_id: str) -> None:
        """移除断点"""
        async with self._lock:
            self._checkpoints.pop(checkpoint_id, None)

            try:
                filepath = (
                    self._checkpoint_dir / f"{checkpoint_id}.json"
                )
                filepath.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"删除断点文件失败: {e}")

    async def find_checkpoint_for_message(
        self, message: str, model: str
    ) -> Optional[StreamCheckpoint]:
        """根据消息和模型查找可用断点"""
        msg_hash = hashlib.md5(
            message.encode("utf-8")
        ).hexdigest()
        async with self._lock:
            for cp in self._checkpoints.values():
                if cp.is_expired:
                    continue
                cp_msg_hash = hashlib.md5(
                    cp.message.encode("utf-8")
                ).hexdigest()
                if (
                    cp_msg_hash == msg_hash
                    and cp.model == model
                    and cp.tokens_received > 0
                ):
                    return cp
            return None

    async def cleanup_expired(self) -> int:
        """清理过期断点"""
        async with self._lock:
            expired_ids = [
                cid
                for cid, cp in self._checkpoints.items()
                if cp.is_expired
            ]
            for cid in expired_ids:
                del self._checkpoints[cid]
                try:
                    filepath = (
                        self._checkpoint_dir / f"{cid}.json"
                    )
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass

            # 清理磁盘上的过期文件
            try:
                for fp in self._checkpoint_dir.glob("*.json"):
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        created_at = data.get("created_at", 0)
                        if (
                            time.time() - created_at
                            > Config.TAS_CHECKPOINT_TTL
                        ):
                            fp.unlink(missing_ok=True)
                            expired_ids.append(fp.stem)
                    except Exception:
                        fp.unlink(missing_ok=True)
            except Exception:
                pass

            return len(expired_ids)


# ==================== Track-and-Stop 账号选择器 ====================


class TrackAndStopSelector:
    """
    Track-and-Stop 最优账号选择算法

    基于多臂赌博机 (Multi-Armed Bandit) 理论:
    - Track 阶段: 使用 Thompson Sampling 探索各账号性能
    - Stop 阶段: 当置信度足够高时, 锁定最优账号进行利用

    数学基础:
    - 成功率: Beta(alpha, beta) 后验分布
    - 选择策略: Thompson Sampling + epsilon-greedy 混合
    - 停止准则: 基于后验方差的置信度检验
    - 综合评分: 加权组合 (成功率, 延迟, 吞吐量)
    """

    def __init__(self, debug: bool = False) -> None:
        self._exploration_rate = Config.TAS_EXPLORATION_RATE
        self._total_selections = 0
        self._debug = debug
        self._lock = Lock()

    def _debug_print(self, message: str) -> None:
        """调试输出"""
        if self._debug:
            logger.debug(f"[TAS选择器] {message}")

    async def select_account(
        self,
        available_accounts: List[Account],
        model: str = "",
    ) -> Optional[Account]:
        """
        选择最优账号

        算法流程:
        1. 过滤冷却中的账号
        2. 检查是否满足停止条件 (置信度足够高)
        3. 若停止: 直接选择综合评分最高的账号
        4. 若继续: 使用 Thompson Sampling 选择账号
        5. 以 epsilon 概率强制探索 (防止陷入局部最优)

        Args:
            available_accounts: 可用账号列表
            model: 当前使用的模型

        Returns:
            选中的账号或None
        """
        async with self._lock:
            if not available_accounts:
                return None

            self._total_selections += 1

            # 过滤冷却中的账号
            active_accounts = [
                acc
                for acc in available_accounts
                if not acc.stats.is_cooling_down
            ]

            if not active_accounts:
                # 所有账号都在冷却, 选冷却时间最短的
                active_accounts = sorted(
                    available_accounts,
                    key=lambda a: a.stats.last_failure_time,
                )
                active_accounts = active_accounts[:1]

            # 检查停止条件
            should_stop = self._check_stop_condition(
                active_accounts
            )

            # epsilon-greedy 探索
            current_epsilon = self._get_current_epsilon()
            import random

            force_explore = random.random() < current_epsilon

            if should_stop and not force_explore:
                # 利用阶段: 选择综合评分最高的账号
                selected = max(
                    active_accounts,
                    key=lambda a: a.stats.compute_composite_score(),
                )
                selected.stats.exploitation_count += 1
                self._debug_print(
                    f"[利用] 选择 {selected.email} "
                    f"(评分={selected.stats.compute_composite_score():.4f}, "
                    f"成功率={selected.stats.success_rate:.4f}, "
                    f"延迟={selected.stats.ema_latency:.3f}s)"
                )
            else:
                # 探索阶段: Thompson Sampling
                selected = self._thompson_sampling_select(
                    active_accounts
                )
                selected.stats.exploration_count += 1
                reason = "强制探索" if force_explore else "Thompson采样"
                self._debug_print(
                    f"[探索/{reason}] 选择 {selected.email} "
                    f"(评分={selected.stats.compute_composite_score():.4f}, "
                    f"总请求={selected.stats.total_requests})"
                )

            # 衰减探索率
            self._exploration_rate = max(
                Config.TAS_MIN_EXPLORATION,
                self._exploration_rate * Config.TAS_DECAY_FACTOR,
            )

            return selected

    def _check_stop_condition(
        self, accounts: List[Account]
    ) -> bool:
        """
        检查停止条件

        停止准则: 最优账号的后验均值与次优账号的后验均值之差
        超过两者各自标准差之和, 即具有统计显著性

        数学表达:
        设 mu_1, mu_2 为最优和次优账号的后验均值
        sigma_1, sigma_2 为对应标准差
        停止条件: mu_1 - mu_2 > sigma_1 + sigma_2
        """
        # 最少需要每个账号都有足够采样
        min_samples = Config.TAS_MIN_SAMPLES
        for acc in accounts:
            if acc.stats.total_requests < min_samples:
                return False

        if len(accounts) < 2:
            return accounts[0].stats.total_requests >= min_samples

        # 按综合评分排序
        sorted_accounts = sorted(
            accounts,
            key=lambda a: a.stats.compute_composite_score(),
            reverse=True,
        )

        best = sorted_accounts[0]
        second = sorted_accounts[1]

        best_score = best.stats.compute_composite_score()
        second_score = second.stats.compute_composite_score()

        best_std = math.sqrt(best.stats.success_rate_variance)
        second_std = math.sqrt(second.stats.success_rate_variance)

        gap = best_score - second_score
        threshold = best_std + second_std

        # 附加条件: 最优账号需要足够多的样本
        has_enough_samples = (
            best.stats.total_requests >= min_samples * 2
        )

        is_confident = gap > threshold and has_enough_samples

        if is_confident:
            self._debug_print(
                f"[停止条件满足] 最优={best.email} "
                f"(score={best_score:.4f}), "
                f"次优={second.email} "
                f"(score={second_score:.4f}), "
                f"gap={gap:.4f} > threshold={threshold:.4f}"
            )

        return is_confident

    def _thompson_sampling_select(
        self, accounts: List[Account]
    ) -> Account:
        """
        Thompson Sampling 选择

        对每个账号从其 Beta 后验分布中采样,
        选择采样值最高的账号

        增强: 将延迟和吞吐量信息融入采样
        """
        best_score = -1.0
        best_account = accounts[0]

        for acc in accounts:
            # 从 Beta 分布采样成功率
            sampled_sr = acc.stats.thompson_sample()

            # 加入延迟和吞吐量的随机扰动
            import random

            latency_bonus = random.gauss(0, 0.05)
            throughput_bonus = random.gauss(0, 0.05)

            lat = acc.stats.ema_latency
            norm_latency = 1.0 / (1.0 + math.exp(lat - 2.0))

            thr = acc.stats.ema_throughput
            norm_throughput = 1.0 / (
                1.0 + math.exp(-(thr - 5.0) / 5.0)
            )

            score = (
                Config.TAS_SUCCESS_WEIGHT * sampled_sr
                + Config.TAS_LATENCY_WEIGHT
                * (norm_latency + latency_bonus)
                + Config.TAS_THROUGHPUT_WEIGHT
                * (norm_throughput + throughput_bonus)
            )

            if score > best_score:
                best_score = score
                best_account = acc

        return best_account

    def _get_current_epsilon(self) -> float:
        """获取当前探索率"""
        return self._exploration_rate

    async def get_algorithm_stats(
        self, accounts: List[Account]
    ) -> Dict[str, Any]:
        """获取算法统计信息"""
        async with self._lock:
            account_stats = {}
            for acc in accounts:
                account_stats[acc.email] = {
                    **acc.stats.to_dict(),
                    "is_cooling_down": acc.stats.is_cooling_down,
                    "window_success_rate": acc.stats.window_success_rate,
                    "window_avg_latency": acc.stats.window_avg_latency,
                }

            total_explore = sum(
                acc.stats.exploration_count for acc in accounts
            )
            total_exploit = sum(
                acc.stats.exploitation_count for acc in accounts
            )

            return {
                "algorithm": "Track-and-Stop (Thompson Sampling + epsilon-greedy)",
                "total_selections": self._total_selections,
                "current_epsilon": self._exploration_rate,
                "exploration_total": total_explore,
                "exploitation_total": total_exploit,
                "explore_ratio": (
                    total_explore / max(total_explore + total_exploit, 1)
                ),
                "account_stats": account_stats,
            }


# ==================== 异步账号池管理器 ====================


class AsyncAccountPool:
    """
    异步账号池管理器

    使用 Track-and-Stop 算法替代 LRU 策略
    """

    def __init__(self, debug: bool = False) -> None:
        self.accounts: List[Account] = []
        self.available_accounts: List[Account] = []
        self.failed_accounts: Dict[str, float] = {}
        self.lock = Lock()
        self.debug = debug
        self.session: Optional[aiohttp.ClientSession] = None
        self.refresh_task: Optional[asyncio.Task] = None
        self.recovery_task: Optional[asyncio.Task] = None
        self.chat_id_prefetch_task: Optional[asyncio.Task] = None
        self.stats_persist_task: Optional[asyncio.Task] = None
        self.running = True
        self.initialization_task: Optional[asyncio.Task] = None
        self.initialized_count = 0
        self._initialized = False
        self.recovery_interval = 60
        self.persistence_file = Config.PERSISTENCE_FILE
        self._persist_executor: Optional[ThreadPoolExecutor] = None
        self.shutdown_event = Event()

        self.chat_id_pool = ChatIdPool()
        self.file_cache = FileCacheManager()
        self.checkpoint_manager = CheckpointManager()

        # Track-and-Stop 选择器
        self.selector = TrackAndStopSelector(debug=debug)

        self._init_accounts()
        self._load_usage_from_disk()

    def _debug_print(self, message: str) -> None:
        """调试输出"""
        if self.debug:
            logger.debug(f"[账号池] {message}")

    def _init_accounts(self) -> None:
        """初始化账号"""
        parsed_accounts = AccountParser.parse(ACCOUNTS)

        if not parsed_accounts:
            self._debug_print("警告: 未解析到任何有效账号")
            return

        for email, password in parsed_accounts:
            if AccountParser.validate_credentials(email, password):
                self.accounts.append(Account(email, password))
            else:
                self._debug_print(f"跳过无效账号: {email}")

        self._debug_print(f"初始化了 {len(self.accounts)} 个账号")

    def _load_usage_from_disk(self) -> None:
        """从磁盘加载使用记录和TAS统计"""
        if not os.path.exists(self.persistence_file):
            self._debug_print(
                f"持久化文件不存在, 将创建新的: {self.persistence_file}"
            )
            return

        try:
            with open(
                self.persistence_file, "r", encoding="utf-8"
            ) as f:
                data = json.load(f)

            usage_data = data.get("account_usage", {})
            tas_data = data.get("tas_stats", {})

            for account in self.accounts:
                if account.email in usage_data:
                    account.last_used = usage_data[account.email].get(
                        "last_used", 0
                    )

                # 加载TAS统计
                if account.email in tas_data:
                    account.stats = AccountStats.from_dict(
                        tas_data[account.email]
                    )

            last_updated = data.get("last_updated")
            if last_updated:
                last_updated_str = datetime.fromtimestamp(
                    last_updated
                ).strftime("%Y-%m-%d %H:%M:%S")
            else:
                last_updated_str = "未知"

            self._debug_print(
                f"从磁盘加载使用记录: {len(usage_data)} 个账号, "
                f"TAS统计: {len(tas_data)} 个, "
                f"最后更新 {last_updated_str}"
            )

        except Exception as e:
            self._debug_print(f"加载持久化数据失败: {e}")

    def _save_usage_to_disk(self) -> None:
        """保存使用记录和TAS统计到磁盘"""
        try:
            Path(self.persistence_file).parent.mkdir(
                parents=True, exist_ok=True
            )

            data = {
                "account_usage": {
                    account.email: {
                        "last_used": account.last_used,
                        "is_logged_in": account.is_logged_in,
                        "login_attempts": account.login_attempts,
                    }
                    for account in self.accounts
                },
                "tas_stats": {
                    account.email: account.stats.to_dict()
                    for account in self.accounts
                },
                "last_updated": time.time(),
            }

            temp_file = f"{self.persistence_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, self.persistence_file)

            self._debug_print(
                f"使用记录已保存到 {self.persistence_file}"
            )

        except Exception as e:
            self._debug_print(f"保存持久化数据失败: {e}")

    async def initialize(
        self, session: aiohttp.ClientSession
    ) -> None:
        """初始化账号池"""
        if self._initialized:
            return

        self.session = session
        self._persist_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="persist"
        )

        self._start_background_initialization()
        self._start_refresh_task()
        self._start_recovery_task()
        self._start_chat_id_prefetch_task()
        self._start_stats_persist_task()

        self._initialized = True

    async def _update_user_settings_with_retry(
        self, account: Account
    ) -> bool:
        """带重试的更新用户设置"""

        async def _update_settings() -> bool:
            if account.memory_disabled:
                return True

            try:
                url = "https://chat.qwen.ai/api/v2/users/user/settings/update"
                headers = {
                    "Accept": "*/*",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Content-Type": "application/json; charset=UTF-8",
                    "Origin": "https://chat.qwen.ai",
                    "Referer": "https://chat.qwen.ai/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                    "source": "web",
                    "version": "0.0.234",
                    "x-request-id": str(uuid.uuid4()),
                    "authorization": f"Bearer {account.token}",
                }
                payload = {
                    "memory": {"enable_memory": False}
                }

                async with self.session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(
                        total=Config.HTTP_TIMEOUT
                    ),
                ) as response:
                    if response.status == 200:
                        account.memory_disabled = True
                        self._debug_print(
                            f"成功关闭账号 {account.email} 的记忆功能"
                        )
                        return True
                    else:
                        error_text = await response.text()
                        raise Exception(
                            f"HTTP {response.status}: {error_text}"
                        )

            except Exception as e:
                account.mark_error(f"更新设置失败: {e}")
                raise

        try:
            return await RetryManager.retry_with_backoff(
                _update_settings
            )
        except Exception as e:
            self._debug_print(
                f"更新账号 {account.email} 设置最终失败: {e}"
            )
            return False

    async def _login_account_with_retry(
        self, account: Account
    ) -> bool:
        """带重试的账号登录"""

        async def _login() -> bool:
            try:
                headers = {
                    "Host": "chat.qwen.ai",
                    "Content-Type": "application/json; charset=UTF-8",
                    "User-Agent": (
                        "Mozilla/5.0 (Linux; Android 10; BAH3-W09) "
                        "AppleWebKit/537.36"
                    ),
                    "Accept": "*/*",
                    "Origin": "https://chat.qwen.ai",
                    "Referer": "https://chat.qwen.ai/auth?action=signin",
                }
                data = {
                    "email": account.email,
                    "password": account.password_hash,
                }

                async with self.session.post(
                    "https://chat.qwen.ai/api/v1/auths/signin",
                    headers=headers,
                    json=data,
                    timeout=aiohttp.ClientTimeout(
                        total=Config.HTTP_TIMEOUT
                    ),
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        account.token = result.get("token", "")
                        account.token_expires = result.get(
                            "expires_at", 0
                        )
                        account.user_id = result.get("id", "")
                        account.is_logged_in = True
                        account.login_attempts = 0
                        account.clear_error()

                        await self._update_user_settings_with_retry(
                            account
                        )
                        return True
                    else:
                        error_text = await response.text()
                        raise Exception(
                            f"HTTP {response.status}: {error_text}"
                        )

            except Exception as e:
                account.mark_error(f"登录失败: {e}")
                raise

        try:
            return await RetryManager.retry_with_backoff(_login)
        except Exception as e:
            self._debug_print(
                f"账号 {account.email} 登录最终失败: {e}"
            )
            account.is_logged_in = False
            account.login_attempts += 1
            return False

    def _start_background_initialization(self) -> None:
        """启动后台初始化"""
        self.initialization_task = asyncio.create_task(
            self._background_initialization()
        )

    async def _background_initialization(self) -> None:
        """后台初始化所有账号"""
        semaphore = Semaphore(3)

        async def login_single_account(account: Account) -> bool:
            async with semaphore:
                account.is_initializing = True
                success = await self._login_account_with_retry(
                    account
                )
                account.is_initializing = False

                if success:
                    async with self.lock:
                        if account not in self.available_accounts:
                            self.available_accounts.append(account)
                        self.initialized_count += 1

                return success

        batch_size = 5
        for i in range(0, len(self.accounts), batch_size):
            if not self.running:
                break

            batch = self.accounts[i : i + batch_size]
            tasks = [
                login_single_account(account) for account in batch
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0.5)

    def _start_refresh_task(self) -> None:
        """启动Token刷新任务"""
        self.refresh_task = asyncio.create_task(
            self._token_refresh_worker()
        )

    def _start_recovery_task(self) -> None:
        """启动失败账号恢复任务"""
        self.recovery_task = asyncio.create_task(
            self._failed_account_recovery_worker()
        )

    def _start_chat_id_prefetch_task(self) -> None:
        """启动ChatID预创建任务"""
        self.chat_id_prefetch_task = asyncio.create_task(
            self._chat_id_prefetch_worker()
        )

    def _start_stats_persist_task(self) -> None:
        """启动TAS统计定期持久化任务"""
        self.stats_persist_task = asyncio.create_task(
            self._stats_persist_worker()
        )

    async def _stats_persist_worker(self) -> None:
        """TAS统计定期持久化工作器"""
        while self.running and not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(
                    Config.TAS_STATS_PERSIST_INTERVAL
                )
                if self._persist_executor and self.running:
                    self._persist_executor.submit(
                        self._save_usage_to_disk
                    )

                # 清理过期断点
                cleaned = (
                    await self.checkpoint_manager.cleanup_expired()
                )
                if cleaned > 0:
                    self._debug_print(
                        f"清理了 {cleaned} 个过期断点"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._debug_print(f"统计持久化任务异常: {e}")
                await asyncio.sleep(10)

    async def _chat_id_prefetch_worker(self) -> None:
        """ChatID预创建工作器"""
        while self.running and not self.shutdown_event.is_set():
            try:
                if self.initialized_count == 0:
                    await asyncio.sleep(2)
                    continue

                models_to_prefetch = [
                    ClientConfig.DEFAULT_MODEL,
                    "qwen3-max-2025-09-23",
                    "qwq-32b",
                ]

                for model in models_to_prefetch:
                    if not self.running:
                        break

                    stats = await self.chat_id_pool.get_stats()
                    model_stats = stats.get(
                        model, {"available": 0}
                    )

                    if (
                        model_stats.get("available", 0)
                        < Config.CHAT_ID_POOL_SIZE // 2
                    ):
                        account = (
                            await self.get_available_account(
                                wait_timeout=5.0
                            )
                        )
                        if account:
                            try:
                                chat_id = await self._create_new_chat_internal(
                                    account.token, model
                                )
                                if chat_id:
                                    await self.chat_id_pool.add(
                                        chat_id,
                                        model,
                                        account.email,
                                    )
                                    self._debug_print(
                                        f"预创建ChatID成功: {model} -> "
                                        f"{chat_id[:8]}..."
                                    )
                            except Exception as e:
                                self._debug_print(
                                    f"预创建ChatID失败: {e}"
                                )
                            finally:
                                await self.release_account(
                                    account, True
                                )

                cleaned = (
                    await self.chat_id_pool.cleanup_expired()
                )
                if cleaned > 0:
                    self._debug_print(
                        f"清理了 {cleaned} 个过期的ChatID"
                    )

                file_cleaned = (
                    await self.file_cache.cleanup_expired()
                )
                if file_cleaned > 0:
                    self._debug_print(
                        f"清理了 {file_cleaned} 个过期的文件缓存"
                    )

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._debug_print(f"ChatID预创建任务异常: {e}")
                await asyncio.sleep(10)

    async def _create_new_chat_internal(
        self, token: str, model: str
    ) -> Optional[str]:
        """内部创建新对话"""
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json; charset=UTF-8",
            "source": "web",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "origin": "https://chat.qwen.ai",
            "referer": "https://chat.qwen.ai/",
            "accept": "application/json",
            "accept-language": "zh-CN,zh;q=0.9",
            "x-request-id": str(uuid.uuid4()),
        }

        payload = {
            "title": "新建对话",
            "models": [model],
            "chat_mode": "normal",
            "chat_type": "t2t",
            "timestamp": int(time.time() * 1000),
        }

        try:
            async with self.session.post(
                "https://chat.qwen.ai/api/v2/chats/new",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("success"):
                        return data.get("data", {}).get("id")
                return None
        except Exception as e:
            self._debug_print(f"创建Chat失败: {e}")
            return None

    async def _failed_account_recovery_worker(self) -> None:
        """失败账号恢复工作器"""
        while self.running and not self.shutdown_event.is_set():
            try:
                current_time = time.time()
                accounts_to_recover: List[str] = []

                async with self.lock:
                    for email, failure_time in list(
                        self.failed_accounts.items()
                    ):
                        if (
                            current_time - failure_time
                            >= self.recovery_interval
                        ):
                            accounts_to_recover.append(email)

                    for email in accounts_to_recover:
                        del self.failed_accounts[email]
                        self._debug_print(
                            f"恢复失败账号: {email}"
                        )

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._debug_print(f"恢复任务异常: {e}")
                await asyncio.sleep(10)

    async def _token_refresh_worker(self) -> None:
        """Token刷新工作器"""
        while self.running and not self.shutdown_event.is_set():
            try:
                current_time = time.time()
                accounts_to_refresh: List[Account] = []

                async with self.lock:
                    for account in self.available_accounts:
                        if (
                            account.is_logged_in
                            and account.token_expires
                            <= current_time + 600
                        ):
                            accounts_to_refresh.append(account)

                for account in accounts_to_refresh:
                    if not await self._login_account_with_retry(
                        account
                    ):
                        async with self.lock:
                            if (
                                account
                                in self.available_accounts
                            ):
                                self.available_accounts.remove(
                                    account
                                )

                async with self.lock:
                    failed_accounts = [
                        acc
                        for acc in self.accounts
                        if not acc.is_logged_in
                        and acc.login_attempts < 3
                        and not acc.is_initializing
                    ]

                for account in failed_accounts[:3]:
                    if await self._login_account_with_retry(
                        account
                    ):
                        async with self.lock:
                            if (
                                account
                                not in self.available_accounts
                            ):
                                self.available_accounts.append(
                                    account
                                )

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._debug_print(f"刷新任务异常: {e}")
                await asyncio.sleep(10)

    async def get_available_account(
        self, wait_timeout: float = 15.0, model: str = ""
    ) -> Optional[Account]:
        """
        获取可用账号 (Track-and-Stop 策略)

        使用 TAS 算法选择最优账号, 替代原有 LRU 策略

        Args:
            wait_timeout: 等待超时时间
            model: 当前使用的模型

        Returns:
            选中的账号或None
        """
        start_time = time.time()

        while time.time() - start_time < wait_timeout:
            async with self.lock:
                candidates = [
                    acc
                    for acc in self.available_accounts
                    if not acc.is_busy
                    and acc.is_logged_in
                    and acc.email not in self.failed_accounts
                ]

            if candidates:
                selected = await self.selector.select_account(
                    candidates, model
                )
                if selected:
                    async with self.lock:
                        selected.is_busy = True
                        selected.last_used = time.time()
                    self._debug_print(
                        f"TAS选择账号: {selected.email} "
                        f"(score={selected.stats.compute_composite_score():.4f})"
                    )
                    return selected

            if self.initialized_count < len(self.accounts):
                await asyncio.sleep(0.5)
            else:
                break

        return None

    async def get_multiple_accounts(
        self,
        count: int,
        wait_timeout: float = 15.0,
        model: str = "",
    ) -> List[Account]:
        """
        获取多个可用账号 (TAS策略排序)

        Args:
            count: 需要的账号数量
            wait_timeout: 等待超时时间
            model: 当前使用的模型

        Returns:
            选中的账号列表
        """
        accounts: List[Account] = []
        start_time = time.time()

        while (
            len(accounts) < count
            and time.time() - start_time < wait_timeout
        ):
            async with self.lock:
                candidates = [
                    acc
                    for acc in self.available_accounts
                    if not acc.is_busy
                    and acc.is_logged_in
                    and acc.email not in self.failed_accounts
                    and acc not in accounts
                ]

            if candidates:
                selected = await self.selector.select_account(
                    candidates, model
                )
                if selected:
                    async with self.lock:
                        selected.is_busy = True
                        selected.last_used = time.time()
                    accounts.append(selected)
                    self._debug_print(
                        f"并发选择账号 {len(accounts)}/{count}: "
                        f"{selected.email}"
                    )

            if len(accounts) < count:
                if self.initialized_count < len(self.accounts):
                    await asyncio.sleep(0.3)
                else:
                    break

        return accounts

    async def mark_account_failed(self, account: Account) -> None:
        """标记账号失败"""
        async with self.lock:
            self.failed_accounts[account.email] = time.time()
            self._debug_print(f"标记账号失败: {account.email}")

    async def release_account(
        self,
        account: Account,
        success: bool = True,
        latency: float = 0.0,
        tokens: int = 0,
        duration: float = 0.0,
        model: str = "",
    ) -> None:
        """
        释放账号并记录 Track-and-Stop 统计

        Args:
            account: 要释放的账号
            success: 请求是否成功
            latency: 首Token延迟(秒)
            tokens: 生成的Token数
            duration: 总耗时(秒)
            model: 使用的模型
        """
        async with self.lock:
            account.is_busy = False

            if not success:
                self.failed_accounts[account.email] = time.time()
                account.stats.record_failure(model)
                self._debug_print(
                    f"账号使用失败: {account.email} "
                    f"(连续失败={account.stats.consecutive_failures})"
                )
            else:
                account.last_used = time.time()
                if latency > 0 or tokens > 0 or duration > 0:
                    account.stats.record_success(
                        latency, tokens, duration, model
                    )
                    self._debug_print(
                        f"账号使用成功: {account.email} "
                        f"(延迟={latency:.3f}s, "
                        f"tokens={tokens}, "
                        f"吞吐={tokens / max(duration, 0.001):.1f} tok/s)"
                    )
                else:
                    self._debug_print(
                        f"账号使用成功: {account.email}"
                    )

        if self._persist_executor and self.running:
            self._persist_executor.submit(self._save_usage_to_disk)

    async def release_accounts(
        self, accounts: List[Account], success: bool = True
    ) -> None:
        """批量释放账号"""
        for account in accounts:
            await self.release_account(account, success)

    async def get_status(self) -> Dict[str, Any]:
        """获取状态 (包含 TAS 算法统计)"""
        async with self.lock:
            total = len(self.accounts)
            logged_in = len(
                [a for a in self.accounts if a.is_logged_in]
            )
            available = len(
                [
                    a
                    for a in self.available_accounts
                    if not a.is_busy
                ]
            )
            busy = len(
                [a for a in self.available_accounts if a.is_busy]
            )
            initializing = len(
                [a for a in self.accounts if a.is_initializing]
            )
            failed_count = len(self.failed_accounts)
            memory_disabled = len(
                [a for a in self.accounts if a.memory_disabled]
            )

            # 按综合评分排序 (TAS)
            sorted_accounts = sorted(
                [acc for acc in self.accounts if acc.is_logged_in],
                key=lambda x: x.stats.compute_composite_score(),
                reverse=True,
            )

            account_list = [
                {
                    "email": acc.email,
                    "last_used": (
                        datetime.fromtimestamp(
                            acc.last_used
                        ).strftime("%Y-%m-%d %H:%M:%S")
                        if acc.last_used > 0
                        else "从未使用"
                    ),
                    "is_busy": acc.is_busy,
                    "is_logged_in": acc.is_logged_in,
                    "last_error": acc.last_error,
                    "error_time": (
                        datetime.fromtimestamp(
                            acc.error_time
                        ).strftime("%Y-%m-%d %H:%M:%S")
                        if acc.error_time > 0
                        else None
                    ),
                    "tas_score": round(
                        acc.stats.compute_composite_score(), 4
                    ),
                    "success_rate": round(
                        acc.stats.success_rate, 4
                    ),
                    "ema_latency": round(
                        acc.stats.ema_latency, 3
                    ),
                    "ema_throughput": round(
                        acc.stats.ema_throughput, 1
                    ),
                    "total_requests": acc.stats.total_requests,
                    "consecutive_failures": acc.stats.consecutive_failures,
                    "is_cooling_down": acc.stats.is_cooling_down,
                }
                for acc in sorted_accounts
            ]

            chat_id_stats = await self.chat_id_pool.get_stats()

        # TAS 算法统计
        tas_stats = await self.selector.get_algorithm_stats(
            self.accounts
        )

        return {
            "total_accounts": total,
            "logged_in": logged_in,
            "available": available,
            "busy": busy,
            "initializing": initializing,
            "initialized_count": self.initialized_count,
            "failed_count": failed_count,
            "memory_disabled": memory_disabled,
            "failed_accounts": list(self.failed_accounts.keys()),
            "strategy": "Track-and-Stop (Thompson Sampling + epsilon-greedy)",
            "accounts": account_list,
            "chat_id_pool": chat_id_stats,
            "tas_algorithm": tas_stats,
        }

    async def shutdown(self) -> None:
        """关闭账号池"""
        self.running = False
        self.shutdown_event.set()

        tasks_to_cancel: List[asyncio.Task] = []
        if self.refresh_task:
            tasks_to_cancel.append(self.refresh_task)
        if self.initialization_task:
            tasks_to_cancel.append(self.initialization_task)
        if self.recovery_task:
            tasks_to_cancel.append(self.recovery_task)
        if self.chat_id_prefetch_task:
            tasks_to_cancel.append(self.chat_id_prefetch_task)
        if self.stats_persist_task:
            tasks_to_cancel.append(self.stats_persist_task)

        for task in tasks_to_cancel:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._persist_executor:
            self._persist_executor.shutdown(wait=True)

        self._save_usage_to_disk()
        self._debug_print("账号池已关闭")


# ==================== OSS上传器 ====================


class UniversalOSSUploader:
    """通用OSS上传器 (支持多种OSS服务和自动回退)"""

    def __init__(
        self, session: aiohttp.ClientSession, debug: bool = False
    ) -> None:
        self.session = session
        self.debug = debug
        self.max_retries = 3
        self.timeout = Config.OSS_UPLOAD_TIMEOUT

    def _debug_print(self, message: str) -> None:
        """调试输出"""
        if self.debug:
            logger.debug(f"[OSS上传] {message}")

    def _generate_oss_authorization(
        self,
        method: str,
        content_type: str,
        date: str,
        oss_headers: Dict[str, str],
        resource: str,
        access_key_id: str,
        access_key_secret: str,
    ) -> str:
        """生成OSS授权头"""
        canonicalized_oss_headers = ""
        if oss_headers:
            sorted_headers = sorted(oss_headers.items())
            canonicalized_oss_headers = (
                "\n".join(
                    [f"{k}:{v}" for k, v in sorted_headers]
                )
                + "\n"
            )

        string_to_sign = (
            f"{method}\n\n{content_type}\n{date}\n"
            f"{canonicalized_oss_headers}{resource}"
        )

        signature = base64.b64encode(
            hmac.new(
                access_key_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")

        return f"OSS {access_key_id}:{signature}"

    async def upload_file_with_retry(
        self, file_path: str, upload_info: Dict[str, Any]
    ) -> str:
        """带重试的文件上传"""

        async def _upload() -> str:
            try:
                return await self._upload_with_sts_put(
                    file_path, upload_info
                )
            except Exception as e:
                self._debug_print(f"STS PUT上传失败: {e}")
                if "presigned_url" in upload_info:
                    return await self._upload_with_presigned_url(
                        file_path, upload_info
                    )
                raise

        try:
            return await RetryManager.retry_with_backoff(_upload)
        except Exception as e:
            self._debug_print(f"OSS上传最终失败: {e}")
            return upload_info.get("file_url", "")

    async def _upload_with_sts_put(
        self, file_path: str, upload_info: Dict[str, Any]
    ) -> str:
        """使用STS临时凭据PUT上传"""
        filename = os.path.basename(file_path)

        with open(file_path, "rb") as f:
            file_content = f.read()

        content_type = FileUtils.get_mime_type(filename)
        parsed_url = urlparse(upload_info["file_url"])
        bucket_host = parsed_url.netloc
        object_key = upload_info["file_path"]

        gmt_date = datetime.now(timezone.utc).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        oss_headers = {
            "x-oss-security-token": upload_info["security_token"]
        }

        bucket_name = bucket_host.split(".")[0]
        resource = f"/{bucket_name}/{object_key}"

        authorization = self._generate_oss_authorization(
            method="PUT",
            content_type=content_type,
            date=gmt_date,
            oss_headers=oss_headers,
            resource=resource,
            access_key_id=upload_info["access_key_id"],
            access_key_secret=upload_info["access_key_secret"],
        )

        headers = {
            "Host": bucket_host,
            "Date": gmt_date,
            "Content-Type": content_type,
            "Content-Length": str(len(file_content)),
            "Authorization": authorization,
            "x-oss-security-token": upload_info[
                "security_token"
            ],
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Origin": "https://chat.qwen.ai",
            "Referer": "https://chat.qwen.ai/",
        }

        upload_url = f"https://{bucket_host}/{object_key}"

        async with self.session.put(
            upload_url,
            data=file_content,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as response:
            if response.status in [200, 201]:
                self._debug_print(f"文件上传成功: {filename}")
                return upload_info["file_url"]
            else:
                error_text = await response.text()
                raise Exception(
                    f"上传失败: HTTP {response.status}, {error_text}"
                )

    async def _upload_with_presigned_url(
        self, file_path: str, upload_info: Dict[str, Any]
    ) -> str:
        """使用预签名URL上传"""
        presigned_url = upload_info.get("presigned_url")
        if not presigned_url:
            raise Exception("无预签名URL可用")

        filename = os.path.basename(file_path)

        with open(file_path, "rb") as f:
            file_content = f.read()

        content_type = FileUtils.get_mime_type(filename)

        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(file_content)),
        }

        async with self.session.put(
            presigned_url,
            data=file_content,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as response:
            if response.status in [200, 201]:
                self._debug_print(
                    f"预签名URL上传成功: {filename}"
                )
                return upload_info.get(
                    "file_url", presigned_url.split("?")[0]
                )
            else:
                error_text = await response.text()
                raise Exception(
                    f"预签名上传失败: HTTP {response.status}, "
                    f"{error_text}"
                )


# ==================== 图像生成服务 ====================


class ImageGenerationService:
    """图像生成服务"""

    BASE_URL = "https://image.pollinations.ai/prompt"

    def __init__(
        self, session: aiohttp.ClientSession, debug: bool = False
    ) -> None:
        self.session = session
        self.debug = debug
        self.timeout = Config.IMAGE_GENERATION_TIMEOUT

    def _debug_print(self, message: str) -> None:
        """调试输出"""
        if self.debug:
            logger.debug(f"[图像生成] {message}")

    def _build_image_url(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        model: str = "flux",
        nologo: bool = True,
    ) -> str:
        """构建图像URL"""
        encoded_prompt = quote(prompt, safe="")
        url = (
            f"{self.BASE_URL}/{encoded_prompt}"
            f"?width={width}&height={height}&model={model}"
            f"&nologo={str(nologo).lower()}"
        )

        if seed is not None:
            url += f"&seed={seed}"
        else:
            url += f"&seed={secrets.randbelow(1000000)}"

        return url

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        model: str = "flux",
        nologo: bool = True,
        response_format: str = "url",
    ) -> Dict[str, Any]:
        """生成图像"""
        image_url = self._build_image_url(
            prompt=prompt,
            width=width,
            height=height,
            seed=seed,
            model=model,
            nologo=nologo,
        )

        self._debug_print(f"生成图像URL: {image_url}")

        if response_format == "url":
            return {"url": image_url, "revised_prompt": prompt}

        try:
            async with self.session.get(
                image_url,
                timeout=aiohttp.ClientTimeout(
                    total=self.timeout
                ),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(
                        f"图像生成失败: HTTP {response.status}, "
                        f"{error_text}"
                    )

                image_data = await response.read()
                content_type = response.headers.get(
                    "Content-Type", "image/jpeg"
                )
                if ";" in content_type:
                    content_type = content_type.split(";")[
                        0
                    ].strip()

                b64_data = base64.b64encode(image_data).decode(
                    "utf-8"
                )

                if response_format == "b64_json":
                    return {
                        "b64_json": b64_data,
                        "revised_prompt": prompt,
                    }

                Path(Config.GENERATED_IMAGE_DIR).mkdir(
                    parents=True, exist_ok=True
                )
                ext = (
                    ".jpg" if "jpeg" in content_type else ".png"
                )
                timestamp = int(time.time() * 1000)
                unique_id = uuid.uuid4().hex[:8]
                filename = f"gen_{timestamp}_{unique_id}{ext}"
                filepath = (
                    Path(Config.GENERATED_IMAGE_DIR) / filename
                )

                with open(filepath, "wb") as f:
                    f.write(image_data)

                self._debug_print(f"图像已保存: {filepath}")

                return {
                    "url": f"/v1/files/generated/{filename}",
                    "b64_json": b64_data,
                    "revised_prompt": prompt,
                    "local_path": str(filepath),
                }

        except asyncio.TimeoutError:
            raise Exception(
                f"图像生成超时 ({self.timeout}秒)"
            )
        except Exception as e:
            self._debug_print(f"图像生成错误: {e}")
            raise

    async def generate_images(
        self,
        prompt: str,
        n: int = 1,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        model: str = "flux",
        nologo: bool = True,
        response_format: str = "url",
    ) -> List[Dict[str, Any]]:
        """批量生成图像"""
        tasks = []
        for i in range(n):
            current_seed = (
                seed + i if seed is not None else None
            )
            task = self.generate_image(
                prompt=prompt,
                width=width,
                height=height,
                seed=current_seed,
                model=model,
                nologo=nologo,
                response_format=response_format,
            )
            tasks.append(task)

        results = await asyncio.gather(
            *tasks, return_exceptions=True
        )

        valid_results: List[Dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"图像生成失败: {result}")
                continue
            valid_results.append(result)

        return valid_results


# ==================== 视频生成服务 ====================


class VideoGenerationService:
    """视频生成服务"""

    def __init__(
        self, session: aiohttp.ClientSession, debug: bool = False
    ) -> None:
        self.session = session
        self.debug = debug
        self.timeout = Config.VIDEO_GENERATION_TIMEOUT

    def _debug_print(self, message: str) -> None:
        """调试输出"""
        if self.debug:
            logger.debug(f"[视频生成] {message}")

    def build_video_download_url(
        self,
        user_id: str,
        message_id: str,
        task_id: str,
        token: str,
    ) -> str:
        """构建视频下载URL"""
        return (
            f"{Config.VIDEO_CDN_BASE}/{user_id}/t2v/"
            f"{message_id}/{task_id}.mp4?key={token}"
        )

    async def download_video(
        self,
        video_url: str,
        save_path: Optional[str] = None,
    ) -> str:
        """下载视频"""
        try:
            headers = {
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "keep-alive",
                "Origin": "https://chat.qwen.ai",
                "Referer": "https://chat.qwen.ai/",
                "Sec-Fetch-Dest": "video",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
            }

            async with self.session.get(
                video_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=self.timeout
                ),
            ) as response:
                if response.status != 200:
                    raise Exception(
                        f"视频下载失败: HTTP {response.status}"
                    )

                video_data = await response.read()

                if save_path is None:
                    Path(Config.GENERATED_VIDEO_DIR).mkdir(
                        parents=True, exist_ok=True
                    )
                    timestamp = int(time.time() * 1000)
                    unique_id = uuid.uuid4().hex[:8]
                    filename = (
                        f"video_{timestamp}_{unique_id}.mp4"
                    )
                    save_path = str(
                        Path(Config.GENERATED_VIDEO_DIR)
                        / filename
                    )

                with open(save_path, "wb") as f:
                    f.write(video_data)

                self._debug_print(f"视频已下载: {save_path}")
                return save_path

        except Exception as e:
            self._debug_print(f"视频下载错误: {e}")
            raise


# ==================== 异步Qwen客户端 ====================


class AsyncQwenClient:
    """
    异步Qwen客户端

    核心改进:
    - Track-and-Stop 最优账号选择算法
    - 流式断点续传
    - 全量异步性能最大化
    """

    def __init__(
        self,
        max_concurrent_requests: int = 100,
        debug: bool = False,
    ) -> None:
        self.account_pool = AsyncAccountPool(debug=debug)
        self.session_lock = Lock()
        self.semaphore = Semaphore(max_concurrent_requests)
        self.connector: Optional[aiohttp.TCPConnector] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._closing = False
        self._initialized = False
        self.debug = debug
        self.oss_uploader: Optional[UniversalOSSUploader] = None
        self.image_service: Optional[
            ImageGenerationService
        ] = None
        self.video_service: Optional[
            VideoGenerationService
        ] = None
        self.embedding_service: Optional[EmbeddingService] = None
        self._init_lock = Lock()
        self.max_concurrent_requests = max_concurrent_requests

        self._active_requests: Dict[str, asyncio.Task] = {}
        self._request_lock = Lock()

        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """设置信号处理器"""
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(
                        self.graceful_shutdown()
                    ),
                )
        except (RuntimeError, NotImplementedError):
            pass

    def _debug_print(self, message: str) -> None:
        """调试输出"""
        if self.debug:
            logger.debug(f"[客户端] {message}")

    async def ensure_initialized(self) -> None:
        """确保已初始化"""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return
            await self.initialize()

    async def initialize(self) -> None:
        """初始化客户端"""
        if self._initialized:
            return

        try:
            self.connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=30,
                keepalive_timeout=30,
                enable_cleanup_closed=True,
                ssl=False,
            )
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=aiohttp.ClientTimeout(
                    total=Config.HTTP_TIMEOUT
                ),
            )
            self.oss_uploader = UniversalOSSUploader(
                self.session, self.debug
            )
            self.image_service = ImageGenerationService(
                self.session, self.debug
            )
            self.video_service = VideoGenerationService(
                self.session, self.debug
            )
            self.embedding_service = EmbeddingService()

            await self.account_pool.initialize(self.session)

            self._initialized = True
            self._debug_print("客户端初始化完成")

        except Exception as e:
            await self._cleanup_resources()
            raise e

    async def _cleanup_resources(self) -> None:
        """清理资源"""
        try:
            if self.session and not self.session.closed:
                await self.session.close()
        except Exception as e:
            self._debug_print(f"关闭session异常: {e}")

        try:
            if self.connector and not self.connector.closed:
                await self.connector.close()
        except Exception as e:
            self._debug_print(f"关闭connector异常: {e}")

    async def graceful_shutdown(self) -> None:
        """优雅关闭"""
        if self._closing:
            return

        self._closing = True
        self._debug_print("开始优雅关闭...")

        async with self._request_lock:
            for request_id, task in self._active_requests.items():
                if not task.done():
                    task.cancel()
                    self._debug_print(f"取消请求: {request_id}")

        try:
            await self.close()
        except Exception as e:
            self._debug_print(f"关闭异常: {e}")
        finally:
            self._debug_print("优雅关闭完成")

    async def close(self) -> None:
        """关闭客户端"""
        if self._closing:
            return

        self._closing = True
        self._debug_print("关闭客户端...")

        try:
            await self.account_pool.shutdown()
        except Exception as e:
            self._debug_print(f"关闭账号池异常: {e}")

        await self._cleanup_resources()
        await asyncio.sleep(0.1)
        self._initialized = False

    async def _create_new_chat(
        self,
        token: str,
        model: str = "qwen3-coder-plus",
        chat_type: str = "t2t",
    ) -> str:
        """创建新对话"""
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json; charset=UTF-8",
            "source": "web",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "origin": "https://chat.qwen.ai",
            "referer": "https://chat.qwen.ai/",
            "accept": "application/json",
            "accept-language": "zh-CN,zh;q=0.9",
            "x-request-id": str(uuid.uuid4()),
        }

        payload = {
            "title": "新建对话",
            "models": [model],
            "chat_mode": "normal",
            "chat_type": chat_type,
            "timestamp": int(time.time() * 1000),
        }

        async with self.session.post(
            "https://chat.qwen.ai/api/v2/chats/new",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            response.raise_for_status()
            data = await response.json()

            if not data.get("success"):
                raise Exception(f"创建对话失败: {data}")

            chat_id = data.get("data", {}).get("id")
            if not chat_id:
                raise Exception(
                    f"创建对话响应缺少chat_id: {data}"
                )

            return chat_id

    async def _get_or_create_chat_id(
        self,
        account: Account,
        model: str,
        chat_type: str = "t2t",
    ) -> str:
        """获取或创建ChatID (优先使用预创建的)"""
        if chat_type == "t2t":
            cached_chat_id = (
                await self.account_pool.chat_id_pool.get(
                    model, account.email
                )
            )
            if cached_chat_id:
                self._debug_print(
                    f"使用预创建的ChatID: {cached_chat_id[:8]}..."
                )
                return cached_chat_id

        return await self._create_new_chat(
            account.token, model, chat_type
        )

    async def _cancel_chat_request(
        self,
        chat_id: str,
        parent_id: str,
        model: str,
        account: Account,
    ) -> bool:
        """取消聊天请求"""
        try:
            headers = {
                "authorization": f"Bearer {account.token}",
                "content-type": "application/json",
                "source": "web",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "accept": "application/json, text/plain, */*",
                "origin": "https://chat.qwen.ai",
                "referer": f"https://chat.qwen.ai/c/{chat_id}",
                "x-request-id": str(uuid.uuid4()),
                "Timezone": datetime.now().strftime(
                    "%a %b %d %Y %H:%M:%S GMT%z"
                ),
                "bx-v": "2.5.36",
            }

            payload = {
                "stream": False,
                "modelIdx": 0,
                "model": model,
                "chat_id": chat_id,
                "parent_id": parent_id,
                "timestamp": int(time.time()),
            }

            async with self.session.post(
                "https://chat.qwen.ai/api/v2/task/suggestions/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    self._debug_print(
                        f"成功取消请求: chat_id={chat_id}"
                    )
                    return True
                else:
                    self._debug_print(
                        f"取消请求失败: HTTP {response.status}"
                    )
                    return False

        except Exception as e:
            self._debug_print(f"取消请求异常: {e}")
            return False

    async def _get_upload_credentials(
        self, filename: str, filesize: int, token: str
    ) -> Dict[str, Any]:
        """获取上传凭据"""

        async def _get_credentials() -> Dict[str, Any]:
            headers = {
                "authorization": f"Bearer {token}",
                "content-type": "application/json; charset=UTF-8",
                "source": "web",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "origin": "https://chat.qwen.ai",
                "referer": "https://chat.qwen.ai/",
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "x-request-id": str(uuid.uuid4()),
            }

            content_type = FileUtils.get_mime_type(filename)
            file_type, _ = FileUtils.get_file_category(
                content_type
            )

            payload = {
                "filename": filename,
                "filesize": filesize,
                "filetype": file_type,
            }

            api_urls = [
                "https://chat.qwen.ai/api/v2/files/getstsToken",
                "https://chat.qwen.ai/api/v1/files/getstsToken",
            ]

            last_error: Optional[Exception] = None
            for api_url in api_urls:
                try:
                    async with self.session.post(
                        api_url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise Exception(
                                f"获取上传凭据失败, 状态码: "
                                f"{response.status}, "
                                f"响应: {error_text}"
                            )

                        data = await response.json()

                        if "data" in data:
                            return data["data"]
                        elif all(
                            key in data
                            for key in [
                                "access_key_id",
                                "access_key_secret",
                                "security_token",
                            ]
                        ):
                            return data
                        else:
                            raise Exception(
                                f"上传凭据响应格式异常: {data}"
                            )

                except Exception as e:
                    last_error = e
                    continue

            raise last_error or Exception("所有API都失败")

        return await RetryManager.retry_with_backoff(
            _get_credentials
        )

    async def upload_file(
        self, file_path: str, account: Account
    ) -> FileInfo:
        """上传文件"""
        if not os.path.exists(file_path):
            raise Exception(f"文件不存在: {file_path}")

        filename = os.path.basename(file_path)
        filesize = os.path.getsize(file_path)
        content_type = FileUtils.get_mime_type(filename)
        file_type, file_class = FileUtils.get_file_category(
            content_type
        )

        max_size = 100 * 1024 * 1024
        if filesize > max_size:
            raise Exception(
                f"文件过大: {filename} ({filesize} bytes), "
                f"最大支持100MB"
            )

        if filesize == 0:
            raise Exception(f"文件为空: {filename}")

        try:
            upload_info = await self._get_upload_credentials(
                filename, filesize, account.token
            )

            required_fields = [
                "access_key_id",
                "access_key_secret",
                "security_token",
                "file_url",
                "file_path",
            ]
            missing_fields = [
                field_name
                for field_name in required_fields
                if not upload_info.get(field_name)
            ]

            if missing_fields:
                raise Exception(
                    f"上传凭据缺少字段: {missing_fields}"
                )

            file_url = (
                await self.oss_uploader.upload_file_with_retry(
                    file_path, upload_info
                )
            )

            return FileInfo(
                file_id=upload_info.get(
                    "file_id", str(uuid.uuid4())
                ),
                file_url=file_url,
                filename=filename,
                size=filesize,
                content_type=content_type,
                user_id=account.user_id,
                file_type=file_type,
                file_class=file_class,
            )

        except Exception as e:
            raise Exception(
                f"文件上传失败 {filename}: {str(e)}"
            )

    def _build_file_object(
        self, file_info: FileInfo
    ) -> Dict[str, Any]:
        """构建文件对象"""
        current_time = int(time.time() * 1000)
        item_id = str(uuid.uuid4())
        upload_task_id = str(uuid.uuid4())

        if (
            file_info.file_class == "vision"
            and file_info.content_type.startswith("image/")
        ):
            show_type = "image"
        elif (
            file_info.file_class == "vision"
            and file_info.content_type.startswith("video/")
        ):
            show_type = "video"
        elif file_info.file_class == "audio":
            show_type = "audio"
        elif file_info.content_type.startswith("text/"):
            show_type = "file"
        else:
            show_type = "file"

        return {
            "type": file_info.file_type,
            "file": {
                "created_at": current_time,
                "data": {},
                "filename": file_info.filename,
                "hash": None,
                "id": file_info.file_id,
                "user_id": file_info.user_id,
                "meta": {
                    "name": file_info.filename,
                    "size": file_info.size,
                    "content_type": file_info.content_type,
                },
                "update_at": current_time,
            },
            "id": file_info.file_id,
            "url": file_info.file_url,
            "name": file_info.filename,
            "collection_name": "",
            "progress": 0,
            "status": "uploaded",
            "greenNet": "success",
            "size": file_info.size,
            "error": "",
            "itemId": item_id,
            "file_type": file_info.content_type,
            "showType": show_type,
            "file_class": file_info.file_class,
            "uploadTaskId": upload_task_id,
        }

    def _build_payload(
        self,
        message: str,
        chat_id: str,
        model: str,
        files: Optional[List[Dict[str, Any]]] = None,
        large_text_file_path: Optional[str] = None,
        chat_type: str = "t2t",
        sub_chat_type: Optional[str] = None,
        parent_id: Optional[str] = None,
        feature_config: Optional[FeatureConfig] = None,
        video_size: Optional[str] = None,
        stream: bool = True,
    ) -> Dict[str, Any]:
        """构建请求载荷"""
        if files is None:
            files = []

        content = message

        if sub_chat_type is None:
            sub_chat_type = chat_type

        if feature_config is None:
            feature_config = FeatureConfig()

        # 根据聊天类型调整功能配置
        if chat_type == "deep_research":
            feature_config.research_mode = "advance"
        elif chat_type == "learn":
            feature_config.thinking_enabled = True
            feature_config.thinking_format = "summary"

        fid = str(uuid.uuid4())
        child_id = str(uuid.uuid4())

        message_obj = {
            "fid": fid,
            "parentId": parent_id,
            "childrenIds": [child_id],
            "role": "user",
            "content": content,
            "user_action": "chat",
            "files": files,
            "timestamp": int(time.time()),
            "models": [model],
            "chat_type": chat_type,
            "feature_config": feature_config.to_dict(),
            "extra": {
                "meta": {"subChatType": sub_chat_type}
            },
            "sub_chat_type": sub_chat_type,
            "parent_id": parent_id,
        }

        payload = {
            "stream": stream,
            "version": Config.API_VERSION,
            "incremental_output": True,
            "chat_id": chat_id,
            "chat_mode": "normal",
            "model": model,
            "parent_id": parent_id,
            "messages": [message_obj],
            "timestamp": int(time.time() + 1),
        }

        # 视频生成特殊参数
        if chat_type == "t2v" and video_size:
            payload["size"] = video_size
            payload["stream"] = False

        return payload

    async def _replace_content(
        self,
        chat_id: str,
        response_id: str,
        origin_content: str,
        new_content: str,
        account: Account,
    ) -> bool:
        """替换内容"""

        async def _replace() -> bool:
            headers = {
                "authorization": f"Bearer {account.token}",
                "content-type": "application/json; charset=UTF-8",
                "source": "web",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "accept": "application/json",
                "accept-language": "zh-CN,zh;q=0.9",
                "connection": "keep-alive",
                "origin": "https://chat.qwen.ai",
                "referer": f"https://chat.qwen.ai/c/{chat_id}",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "x-request-id": str(uuid.uuid4()),
                "timezone": datetime.now().strftime(
                    "%a %b %d %Y %H:%M:%S GMT%z"
                ),
                "bx-v": "2.5.31",
            }

            payload = {
                "content_list": [
                    {
                        "content": new_content,
                        "phase": "answer",
                        "status": "finished",
                        "extra": None,
                        "role": "assistant",
                        "usage": {
                            "input_tokens": len(origin_content)
                            // 3,
                            "output_tokens": len(new_content)
                            // 3,
                            "total_tokens": (
                                len(origin_content)
                                + len(new_content)
                            )
                            // 3,
                            "prompt_tokens_details": {
                                "cached_tokens": 0
                            },
                        },
                    }
                ]
            }

            request_url = (
                f"https://chat.qwen.ai/api/v2/chats/"
                f"{chat_id}/messages/{response_id}"
            )

            async with self.session.post(
                request_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    self._debug_print(
                        f"内容替换成功: chat_id={chat_id}, "
                        f"message_id={response_id}"
                    )
                    return True
                else:
                    error_text = await response.text()
                    raise Exception(
                        f"HTTP {response.status}: {error_text}"
                    )

        try:
            return await RetryManager.retry_with_backoff(_replace)
        except Exception as e:
            self._debug_print(f"内容替换最终失败: {e}")
            return False

    async def _request_tts(
        self,
        chat_id: str,
        response_id: str,
        account: Account,
    ) -> Optional[str]:
        """请求TTS"""

        async def _tts_request() -> Optional[str]:
            headers = {
                "authorization": f"Bearer {account.token}",
                "content-type": "application/json; charset=UTF-8",
                "source": "web",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "connection": "keep-alive",
                "origin": "https://chat.qwen.ai",
                "referer": f"https://chat.qwen.ai/c/{chat_id}",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "x-request-id": str(uuid.uuid4()),
                "x-accel-buffering": "no",
                "timezone": time.strftime(
                    "%a %b %d %Y %H:%M:%S GMT%z"
                ),
                "bx-v": "2.5.31",
            }

            payload = {
                "chat_id": chat_id,
                "timestamp": int(time.time()),
                "messages": [
                    {
                        "id": response_id,
                        "role": "assistant",
                        "sub_chat_type": "tts",
                    }
                ],
            }

            request_url = (
                f"https://chat.qwen.ai/api/v2/tts/completions"
                f"?chat_id={chat_id}"
            )

            audio_chunks: List[str] = []
            is_finished = False

            async with self.session.post(
                request_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=Config.TTS_TIMEOUT
                ),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(
                        f"HTTP {response.status}: {error_text}"
                    )

                buffer = b""
                async for chunk in response.content.iter_any():
                    if not chunk:
                        continue

                    buffer += chunk
                    lines = buffer.split(b"\n")
                    buffer = lines[-1]

                    for line in lines[:-1]:
                        if not line.strip():
                            continue

                        try:
                            line_str = line.decode(
                                "utf-8", errors="replace"
                            ).strip()
                        except UnicodeDecodeError:
                            continue

                        if line_str.startswith("data: "):
                            data_str = line_str[6:]
                            if (
                                not data_str
                                or data_str == "[DONE]"
                            ):
                                continue

                            try:
                                data = json.loads(data_str)

                                if (
                                    "choices" in data
                                    and data["choices"]
                                ):
                                    choice = data["choices"][0]
                                    delta = choice.get(
                                        "delta", {}
                                    )
                                    tts_data = delta.get(
                                        "tts", ""
                                    )

                                    if (
                                        tts_data
                                        and tts_data.strip()
                                    ):
                                        audio_chunks.append(
                                            tts_data
                                        )

                                    status = delta.get("status")
                                    if status == "finished":
                                        is_finished = True
                                        break

                            except json.JSONDecodeError as e:
                                self._debug_print(
                                    f"JSON解析错误: {e}, "
                                    f"数据: {data_str}"
                                )
                                continue

            if not is_finished and audio_chunks:
                self._debug_print(
                    "TTS流未正常结束, 但已收集到音频数据"
                )

            if not audio_chunks:
                raise Exception("未收集到TTS音频数据")

            combined_base64 = "".join(audio_chunks)

            try:
                audio_data = base64.b64decode(combined_base64)
            except Exception as e:
                raise Exception(f"Base64解码失败: {e}")

            return self._create_wav_file(audio_data)

        try:
            return await RetryManager.retry_with_backoff(
                _tts_request
            )
        except Exception as e:
            self._debug_print(f"TTS请求最终失败: {e}")
            return None

    def _create_wav_file(
        self, pcm_data: bytes
    ) -> Optional[str]:
        """创建WAV文件"""
        try:
            sample_rate = 24000
            channels = 1
            bits_per_sample = 16

            data_size = len(pcm_data)
            file_size = 36 + data_size

            wav_header = bytearray(44)
            wav_header[0:4] = b"RIFF"
            wav_header[4:8] = file_size.to_bytes(4, "little")
            wav_header[8:12] = b"WAVE"
            wav_header[12:16] = b"fmt "
            wav_header[16:20] = (16).to_bytes(4, "little")
            wav_header[20:22] = (1).to_bytes(2, "little")
            wav_header[22:24] = channels.to_bytes(2, "little")
            wav_header[24:28] = sample_rate.to_bytes(4, "little")

            byte_rate = (
                sample_rate * channels * bits_per_sample // 8
            )
            wav_header[28:32] = byte_rate.to_bytes(4, "little")

            block_align = channels * bits_per_sample // 8
            wav_header[32:34] = block_align.to_bytes(2, "little")
            wav_header[34:36] = bits_per_sample.to_bytes(
                2, "little"
            )
            wav_header[36:40] = b"data"
            wav_header[40:44] = data_size.to_bytes(4, "little")

            wav_data = bytes(wav_header) + pcm_data

            tts_dir = Path(Config.TTS_DIR)
            tts_dir.mkdir(parents=True, exist_ok=True)

            filename = (
                f"tts_{int(time.time() * 1000)}_"
                f"{uuid.uuid4().hex[:8]}.wav"
            )
            filepath = tts_dir / filename

            with open(filepath, "wb") as f:
                f.write(wav_data)

            return str(filepath)

        except Exception as e:
            self._debug_print(f"创建WAV文件失败: {e}")
            return None

    async def _preprocess_files_once(
        self,
        file_paths: Union[str, List[str], None],
        extracted_files: Optional[List[ExtractedFile]],
        account: Account,
        message_text: str = "",
    ) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
        """预处理文件 (带缓存)"""
        files: List[Dict[str, Any]] = []
        temp_files_to_cleanup: List[str] = []
        large_text_file_path: Optional[str] = None

        # 处理大文本消息
        if message_text:
            processed_text, file_path = (
                FileUtils.process_large_text(message_text)
            )
            if file_path:
                large_text_file_path = file_path
                temp_files_to_cleanup.append(file_path)
                self._debug_print(
                    f"大文本处理: 保留 {len(processed_text)} 字符, "
                    f"其余保存为文件: {file_path}"
                )

        # 处理文件路径
        if file_paths:
            if isinstance(file_paths, str):
                file_paths = [file_paths]

            for file_path_or_url in file_paths:
                if (
                    not file_path_or_url
                    or not file_path_or_url.strip()
                ):
                    continue

                try:
                    content_hash = FileUtils.compute_content_hash(
                        file_path_or_url
                    )

                    cached = await self.account_pool.file_cache.get(
                        content_hash, account.email
                    )
                    if cached:
                        files.extend(cached)
                        self._debug_print(
                            f"使用缓存的文件: "
                            f"{file_path_or_url[:50]}..."
                        )
                        continue

                    file_objects: List[Dict[str, Any]] = []

                    if FileUtils.is_base64_data_uri(
                        file_path_or_url
                    ):
                        saved_path = (
                            Base64FileHandler.save_base64_file(
                                file_path_or_url
                            )
                        )
                        if saved_path:
                            temp_files_to_cleanup.append(
                                saved_path
                            )
                            file_info = await self.upload_file(
                                saved_path, account
                            )
                            file_obj = self._build_file_object(
                                file_info
                            )
                            file_objects.append(file_obj)

                    elif FileUtils.is_url(file_path_or_url):
                        file_info = (
                            await FileUtils.get_url_file_info(
                                self.session,
                                file_path_or_url,
                                account.user_id,
                            )
                        )
                        file_obj = self._build_file_object(
                            file_info
                        )
                        file_objects.append(file_obj)

                    elif os.path.exists(file_path_or_url):
                        mime_type = FileUtils.get_mime_type(
                            file_path_or_url
                        )

                        if mime_type.startswith("text/"):
                            file_size = os.path.getsize(
                                file_path_or_url
                            )
                            if (
                                file_size
                                > Config.LARGE_TEXT_THRESHOLD
                            ):
                                self._debug_print(
                                    f"检测到大文本文件: "
                                    f"{file_path_or_url} "
                                    f"({file_size} bytes)"
                                )
                                with open(
                                    file_path_or_url,
                                    "r",
                                    encoding="utf-8",
                                ) as f:
                                    file_content = f.read()
                                _, new_file_path = (
                                    FileUtils.process_large_text(
                                        file_content
                                    )
                                )
                                if new_file_path:
                                    temp_files_to_cleanup.append(
                                        new_file_path
                                    )
                                    file_info = (
                                        await self.upload_file(
                                            new_file_path,
                                            account,
                                        )
                                    )
                                    file_obj = (
                                        self._build_file_object(
                                            file_info
                                        )
                                    )
                                    file_objects.append(file_obj)
                            else:
                                temp_path = FileUtils.copy_local_file_to_temp(
                                    file_path_or_url
                                )
                                if temp_path:
                                    temp_files_to_cleanup.append(
                                        temp_path
                                    )
                                    file_info = (
                                        await self.upload_file(
                                            temp_path, account
                                        )
                                    )
                                else:
                                    file_info = (
                                        await self.upload_file(
                                            file_path_or_url,
                                            account,
                                        )
                                    )
                                file_obj = (
                                    self._build_file_object(
                                        file_info
                                    )
                                )
                                file_objects.append(file_obj)
                        else:
                            temp_path = (
                                FileUtils.copy_local_file_to_temp(
                                    file_path_or_url
                                )
                            )
                            if temp_path:
                                temp_files_to_cleanup.append(
                                    temp_path
                                )
                                file_info = (
                                    await self.upload_file(
                                        temp_path, account
                                    )
                                )
                            else:
                                file_info = (
                                    await self.upload_file(
                                        file_path_or_url,
                                        account,
                                    )
                                )
                            file_obj = self._build_file_object(
                                file_info
                            )
                            file_objects.append(file_obj)

                    if file_objects:
                        files.extend(file_objects)
                        await self.account_pool.file_cache.set(
                            content_hash,
                            account.email,
                            file_objects,
                        )

                except Exception as e:
                    logger.warning(
                        f"处理文件失败 "
                        f"{file_path_or_url[:50]}...: {e}"
                    )
                    continue

        # 处理提取的文件
        if extracted_files:
            for extracted_file in extracted_files:
                try:
                    hash_source = (
                        extracted_file.original_data[:10000]
                        if extracted_file.original_data
                        else extracted_file.path_or_url
                    )
                    content_hash = (
                        FileUtils.compute_content_hash(
                            hash_source
                        )
                    )

                    cached = (
                        await self.account_pool.file_cache.get(
                            content_hash, account.email
                        )
                    )
                    if cached:
                        files.extend(cached)
                        continue

                    file_objects = []

                    if extracted_file.source == "base64":
                        saved_path = (
                            Base64FileHandler.save_base64_file(
                                extracted_file.original_data
                            )
                        )
                        if saved_path:
                            temp_files_to_cleanup.append(
                                saved_path
                            )
                            file_info = await self.upload_file(
                                saved_path, account
                            )
                            file_obj = self._build_file_object(
                                file_info
                            )
                            file_objects.append(file_obj)

                    elif extracted_file.source == "url":
                        file_info = (
                            await FileUtils.get_url_file_info(
                                self.session,
                                extracted_file.path_or_url,
                                account.user_id,
                            )
                        )
                        file_obj = self._build_file_object(
                            file_info
                        )
                        file_objects.append(file_obj)

                    elif extracted_file.source == "local":
                        if os.path.exists(
                            extracted_file.path_or_url
                        ):
                            mime_type = (
                                extracted_file.mime_type
                                or FileUtils.get_mime_type(
                                    extracted_file.path_or_url
                                )
                            )

                            if mime_type.startswith("text/"):
                                with open(
                                    extracted_file.path_or_url,
                                    "r",
                                    encoding="utf-8",
                                ) as f:
                                    file_content = f.read()

                                if (
                                    len(file_content)
                                    > Config.LARGE_TEXT_THRESHOLD
                                ):
                                    self._debug_print(
                                        f"检测到大文本提取文件: "
                                        f"{extracted_file.path_or_url}"
                                    )
                                    _, new_file_path = FileUtils.process_large_text(
                                        file_content
                                    )
                                    if new_file_path:
                                        temp_files_to_cleanup.append(
                                            new_file_path
                                        )
                                        file_info = await self.upload_file(
                                            new_file_path,
                                            account,
                                        )
                                        file_obj = self._build_file_object(
                                            file_info
                                        )
                                        file_objects.append(
                                            file_obj
                                        )
                                else:
                                    temp_path = FileUtils.copy_local_file_to_temp(
                                        extracted_file.path_or_url
                                    )
                                    if temp_path:
                                        temp_files_to_cleanup.append(
                                            temp_path
                                        )
                                        file_info = await self.upload_file(
                                            temp_path, account
                                        )
                                    else:
                                        file_info = await self.upload_file(
                                            extracted_file.path_or_url,
                                            account,
                                        )
                                    file_obj = self._build_file_object(
                                        file_info
                                    )
                                    file_objects.append(file_obj)
                            else:
                                temp_path = FileUtils.copy_local_file_to_temp(
                                    extracted_file.path_or_url
                                )
                                if temp_path:
                                    temp_files_to_cleanup.append(
                                        temp_path
                                    )
                                    file_info = (
                                        await self.upload_file(
                                            temp_path, account
                                        )
                                    )
                                else:
                                    file_info = await self.upload_file(
                                        extracted_file.path_or_url,
                                        account,
                                    )
                                file_obj = (
                                    self._build_file_object(
                                        file_info
                                    )
                                )
                                file_objects.append(file_obj)

                    if file_objects:
                        files.extend(file_objects)
                        await self.account_pool.file_cache.set(
                            content_hash,
                            account.email,
                            file_objects,
                        )

                except Exception as e:
                    logger.warning(f"处理提取文件失败: {e}")
                    continue

        self._debug_print(
            f"文件预处理完成: {len(files)} 个文件, "
            f"{len(temp_files_to_cleanup)} 个临时文件, "
            f"大文本处理: "
            f"{'是' if large_text_file_path else '否'}"
        )

        return files, temp_files_to_cleanup, large_text_file_path

    async def _send_chat_request(
        self,
        account: Account,
        chat_id: str,
        message: str,
        model: str,
        files: List[Dict[str, Any]],
        large_text_file_path: Optional[str] = None,
        cancel_event: Optional[Event] = None,
        chat_type: str = "t2t",
        sub_chat_type: Optional[str] = None,
        parent_id: Optional[str] = None,
        feature_config: Optional[FeatureConfig] = None,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """发送聊天请求"""
        headers = {
            "Authorization": f"Bearer {account.token}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Connection": "keep-alive",
            "Accept": "text/event-stream",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
            "Timezone": (
                f"{datetime.now().strftime('%a %b %d %Y %H:%M:%S')}"
                f" GMT+0800"
            ),
            "source": "web",
            "Version": "0.1.13",
            "bx-v": "2.5.31",
            "Origin": "https://chat.qwen.ai",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://chat.qwen.ai/c/" + chat_id,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": (
                f"ssxmod_itna={get_ssxmod_itna()};"
                f"ssxmod_itna2={get_ssxmod_itna2()}"
            ),
        }

        payload = self._build_payload(
            message,
            chat_id,
            model,
            files,
            large_text_file_path,
            chat_type=chat_type,
            sub_chat_type=sub_chat_type,
            parent_id=parent_id,
            feature_config=feature_config,
        )
        request_url = (
            f"https://chat.qwen.ai/api/v2/chat/completions"
            f"?chat_id={chat_id}"
        )

        async with self.session.post(
            request_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(
                total=Config.CHAT_COMPLETION_TIMEOUT
            ),
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(
                    f"HTTP {response.status}: {error_text}"
                )

            response_id: Optional[str] = None
            latest_usage: Optional[Dict[str, Any]] = None

            async for line in response.content:
                # 检查取消事件
                if cancel_event and cancel_event.is_set():
                    self._debug_print("请求被取消")
                    break

                if not line:
                    continue

                try:
                    line_str = line.decode(
                        "utf-8", errors="replace"
                    ).strip()
                except Exception:
                    continue

                if not line_str or not line_str.startswith(
                    "data: "
                ):
                    continue

                data_str = line_str[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)

                    if "response.created" in data_str:
                        created_data = data.get(
                            "response.created", {}
                        )
                        if (
                            created_data
                            and "response_id" in created_data
                        ):
                            response_id = created_data[
                                "response_id"
                            ]

                    if "usage" in data:
                        latest_usage = data.get("usage")

                    if "choices" in data and data["choices"]:
                        delta = data["choices"][0].get(
                            "delta", {}
                        )
                        phase = delta.get("phase", "")
                        status = delta.get("status", "")
                        content = delta.get("content", "")
                        extra = delta.get("extra", {})

                        if phase == "answer" and content:
                            yield content

                        elif phase == "thinking_summary":
                            if extra:
                                summary_title = (
                                    extra.get(
                                        "summary_title", {}
                                    ).get("content", [])
                                )
                                summary_thought = (
                                    extra.get(
                                        "summary_thought", {}
                                    ).get("content", [])
                                )
                                yield {
                                    "thinking_summary": ThinkingSummary(
                                        title=summary_title,
                                        thought=summary_thought,
                                    )
                                }

                        elif (
                            phase == "image_gen" and content
                        ):
                            output_hw = extra.get(
                                "output_image_hw", [[]]
                            )
                            height, width = (
                                output_hw[0]
                                if output_hw and output_hw[0]
                                else (0, 0)
                            )
                            yield {
                                "image_gen": ImageEditResult(
                                    image_url=content,
                                    width=width,
                                    height=height,
                                )
                            }

                        elif phase in [
                            "ResearchNotice",
                            "ResearchPlanning",
                        ]:
                            deep_research = extra.get(
                                "deep_research", {}
                            )
                            yield {
                                "deep_research": DeepResearchResult(
                                    content=content,
                                    stage=deep_research.get(
                                        "stage", phase
                                    ),
                                    lang_code=deep_research.get(
                                        "lang_code", "zh"
                                    ),
                                    status=deep_research.get(
                                        "status", status
                                    ),
                                    version=deep_research.get(
                                        "version", "v4"
                                    ),
                                )
                            }

                        elif phase == "video_gen":
                            yield {"video_gen": content}

                    elif "error" in data:
                        raise Exception(
                            f"服务器错误: {data['error']}"
                        )

                except json.JSONDecodeError:
                    continue
                except Exception:
                    continue

            if response_id:
                yield {"response_id": response_id}

            if latest_usage:
                yield {"usage": latest_usage}

    async def _send_video_request(
        self,
        account: Account,
        chat_id: str,
        message: str,
        model: str,
        video_size: str = "16:9",
    ) -> VideoGenerationResult:
        """发送视频生成请求 (非流式)"""
        headers = {
            "Authorization": f"Bearer {account.token}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Content-Type": "application/json",
            "source": "web",
            "Origin": "https://chat.qwen.ai",
            "Referer": "https://chat.qwen.ai/c/" + chat_id,
            "Accept": "application/json",
            "x-request-id": str(uuid.uuid4()),
        }

        payload = self._build_payload(
            message,
            chat_id,
            model,
            [],
            chat_type="t2v",
            sub_chat_type="t2v",
            video_size=video_size,
            stream=False,
        )

        request_url = (
            f"https://chat.qwen.ai/api/v2/chat/completions"
            f"?chat_id={chat_id}"
        )

        async with self.session.post(
            request_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(
                total=Config.VIDEO_GENERATION_TIMEOUT
            ),
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                return VideoGenerationResult(
                    success=False,
                    request_id="",
                    chat_id=chat_id,
                    message_id="",
                    task_id="",
                    size=video_size,
                    error=f"HTTP {response.status}: {error_text}",
                )

            data = await response.json()

            if not data.get("success"):
                return VideoGenerationResult(
                    success=False,
                    request_id=data.get("request_id", ""),
                    chat_id=chat_id,
                    message_id="",
                    task_id="",
                    size=video_size,
                    error=str(data),
                )

            result_data = data.get("data", {})
            messages = result_data.get("messages", [])

            message_id = result_data.get("message_id", "")
            task_id = ""

            if messages and len(messages) > 0:
                extra = messages[0].get("extra", {})
                wanx = extra.get("wanx", {})
                task_id = wanx.get("task_id", "")

            video_url = (
                self.video_service.build_video_download_url(
                    user_id=account.user_id,
                    message_id=message_id,
                    task_id=task_id,
                    token=account.token,
                )
            )

            return VideoGenerationResult(
                success=True,
                request_id=data.get("request_id", ""),
                chat_id=chat_id,
                message_id=message_id,
                task_id=task_id,
                size=video_size,
                video_url=video_url,
            )

    async def _concurrent_chat_stream(
        self,
        message: str,
        model: str,
        preprocessed_files: List[Dict[str, Any]],
        large_text_file_path: Optional[str],
        voice: bool = False,
        chat_type: str = "t2t",
        sub_chat_type: Optional[str] = None,
        feature_config: Optional[FeatureConfig] = None,
        resume_checkpoint: Optional[StreamCheckpoint] = None,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        并发聊天流 (3并发竞速 + TAS性能反馈 + 断点续传)

        核心改进:
        1. 使用 TAS 选择的账号进行并发请求
        2. 记录每个账号的首Token延迟、吞吐量等指标
        3. 反馈到 TAS 系统用于后续决策
        4. 支持从断点恢复已中断的流
        """
        # 断点续传: 先输出已积累的内容
        if resume_checkpoint and resume_checkpoint.accumulated_content:
            self._debug_print(
                f"从断点恢复: 已有 "
                f"{resume_checkpoint.tokens_received} tokens"
            )
            yield resume_checkpoint.accumulated_content

        concurrent_count = Config.CONCURRENT_GENERATORS
        min_tokens = Config.MIN_TOKENS_FOR_SELECTION

        accounts = await self.account_pool.get_multiple_accounts(
            concurrent_count, wait_timeout=15.0, model=model
        )

        if not accounts:
            raise Exception("没有可用的账号")

        self._debug_print(
            f"获取到 {len(accounts)} 个账号进行并发请求"
        )

        queues: List[asyncio.Queue] = []
        tasks: List[asyncio.Task] = []
        task_info: List[Dict[str, Any]] = []

        async def generator_worker(
            index: int,
            account: Account,
            chat_id: str,
            cancel_event: Event,
            output_queue: asyncio.Queue,
        ) -> None:
            """生成器工作协程"""
            try:
                async for chunk in self._send_chat_request(
                    account,
                    chat_id,
                    message,
                    model,
                    preprocessed_files,
                    large_text_file_path,
                    cancel_event,
                    chat_type=chat_type,
                    sub_chat_type=sub_chat_type,
                    feature_config=feature_config,
                ):
                    if cancel_event.is_set():
                        break
                    await output_queue.put(
                        ("chunk", index, chunk)
                    )
                await output_queue.put(("done", index, None))
            except asyncio.CancelledError:
                await output_queue.put(
                    ("cancelled", index, None)
                )
            except Exception as e:
                self._debug_print(f"生成器 {index} 错误: {e}")
                await output_queue.put(
                    ("error", index, str(e))
                )

        for i, account in enumerate(accounts):
            try:
                chat_id = await self._get_or_create_chat_id(
                    account, model, chat_type
                )
                cancel_event = Event()
                output_queue: asyncio.Queue = asyncio.Queue()

                task = asyncio.create_task(
                    generator_worker(
                        i,
                        account,
                        chat_id,
                        cancel_event,
                        output_queue,
                    )
                )

                queues.append(output_queue)
                tasks.append(task)
                task_info.append(
                    {
                        "index": i,
                        "account": account,
                        "chat_id": chat_id,
                        "cancel_event": cancel_event,
                        "queue": output_queue,
                        "task": task,
                        "tokens": 0,
                        "buffer": [],
                        "response_id": None,
                        "usage_data": None,
                        "is_done": False,
                        "has_error": False,
                        "start_time": time.time(),
                        "first_token_time": None,
                        "accumulated_content": "",
                    }
                )

                self._debug_print(
                    f"创建并发任务 {i + 1}/{len(accounts)}: "
                    f"account={account.email}, "
                    f"chat_id={chat_id[:8]}..."
                )

            except Exception as e:
                self._debug_print(f"创建任务失败: {e}")
                await self.account_pool.release_account(
                    account, False, model=model
                )

        if not task_info:
            raise Exception("无法创建任何并发任务")

        selected_info: Optional[Dict[str, Any]] = None
        checkpoint_id = uuid.uuid4().hex

        try:
            while selected_info is None:
                all_done = all(
                    info["is_done"] or info["has_error"]
                    for info in task_info
                )
                if all_done:
                    break

                for info in task_info:
                    if info["is_done"] or info["has_error"]:
                        continue

                    try:
                        event_type, index, data = (
                            info["queue"].get_nowait()
                        )

                        if event_type == "chunk":
                            if isinstance(data, dict):
                                if "response_id" in data:
                                    info["response_id"] = data[
                                        "response_id"
                                    ]
                                elif "usage" in data:
                                    info["usage_data"] = data[
                                        "usage"
                                    ]
                                else:
                                    info["buffer"].append(data)
                            else:
                                info["buffer"].append(data)
                                info["tokens"] += 1
                                info[
                                    "accumulated_content"
                                ] += str(data)

                                # 记录首Token时间
                                if (
                                    info["first_token_time"]
                                    is None
                                ):
                                    info[
                                        "first_token_time"
                                    ] = time.time()

                                if (
                                    info["tokens"]
                                    >= min_tokens
                                ):
                                    selected_info = info
                                    self._debug_print(
                                        f"选择生成器 "
                                        f"{info['index']}: "
                                        f"已收到 "
                                        f"{info['tokens']} "
                                        f"tokens"
                                    )
                                    break

                        elif event_type == "done":
                            info["is_done"] = True

                        elif event_type in (
                            "error",
                            "cancelled",
                        ):
                            info["has_error"] = True

                    except asyncio.QueueEmpty:
                        continue

                if selected_info is None:
                    await asyncio.sleep(0.05)

            if selected_info is None:
                valid_infos = [
                    info
                    for info in task_info
                    if info["buffer"]
                    and not info["has_error"]
                ]
                if valid_infos:
                    selected_info = max(
                        valid_infos,
                        key=lambda x: x["tokens"],
                    )
                    self._debug_print(
                        f"所有生成器结束, 选择最佳: "
                        f"生成器 {selected_info['index']} "
                        f"({selected_info['tokens']} tokens)"
                    )
                else:
                    # 所有生成器失败 - 记录失败并释放
                    for info in task_info:
                        end_time = time.time()
                        duration = (
                            end_time - info["start_time"]
                        )
                        await self.account_pool.release_account(
                            info["account"],
                            False,
                            duration=duration,
                            model=model,
                        )
                    raise Exception("所有并发生成器都失败了")

            # 取消未选中的生成器并记录TAS指标
            for info in task_info:
                if info != selected_info:
                    info["cancel_event"].set()
                    if not info["task"].done():
                        info["task"].cancel()

                    if info["response_id"]:
                        asyncio.create_task(
                            self._cancel_chat_request(
                                info["chat_id"],
                                info["response_id"],
                                model,
                                info["account"],
                            )
                        )

                    # 记录未选中账号的指标 (视为部分成功)
                    end_time = time.time()
                    duration = end_time - info["start_time"]
                    if info["tokens"] > 0:
                        ftl = (
                            info["first_token_time"]
                            - info["start_time"]
                            if info["first_token_time"]
                            else duration
                        )
                        await self.account_pool.release_account(
                            info["account"],
                            True,
                            latency=ftl,
                            tokens=info["tokens"],
                            duration=duration,
                            model=model,
                        )
                    else:
                        await self.account_pool.release_account(
                            info["account"],
                            False,
                            model=model,
                        )

            # 输出已缓冲的内容
            for chunk in selected_info["buffer"]:
                yield chunk

            # 继续接收选中生成器的剩余内容
            if not selected_info["is_done"]:
                while True:
                    try:
                        event_type, index, data = (
                            await asyncio.wait_for(
                                selected_info["queue"].get(),
                                timeout=Config.CHAT_COMPLETION_TIMEOUT,
                            )
                        )

                        if event_type == "chunk":
                            if isinstance(data, dict):
                                if "response_id" in data:
                                    selected_info[
                                        "response_id"
                                    ] = data["response_id"]
                                elif "usage" in data:
                                    selected_info[
                                        "usage_data"
                                    ] = data["usage"]
                                elif "voice" in data:
                                    yield data
                                else:
                                    yield data
                            else:
                                selected_info["tokens"] += 1
                                selected_info[
                                    "accumulated_content"
                                ] += str(data)
                                yield data

                                # 定期保存断点
                                if (
                                    selected_info["tokens"]
                                    % 50
                                    == 0
                                ):
                                    cp = StreamCheckpoint(
                                        checkpoint_id=checkpoint_id,
                                        message=message,
                                        model=model,
                                        chat_id=selected_info[
                                            "chat_id"
                                        ],
                                        account_email=selected_info[
                                            "account"
                                        ].email,
                                        parent_id=selected_info[
                                            "response_id"
                                        ],
                                        accumulated_content=selected_info[
                                            "accumulated_content"
                                        ],
                                        accumulated_chunks=[],
                                        tokens_received=selected_info[
                                            "tokens"
                                        ],
                                        last_chunk_time=time.time(),
                                        created_at=time.time(),
                                        chat_type=chat_type,
                                        sub_chat_type=sub_chat_type,
                                        feature_config_dict=(
                                            feature_config.to_dict()
                                            if feature_config
                                            else {}
                                        ),
                                        file_objects=preprocessed_files,
                                    )
                                    await self.account_pool.checkpoint_manager.save_checkpoint(
                                        cp
                                    )

                        elif event_type == "done":
                            break

                        elif event_type in (
                            "error",
                            "cancelled",
                        ):
                            self._debug_print(
                                f"选中生成器异常终止: {data}"
                            )
                            break

                    except asyncio.TimeoutError:
                        self._debug_print("接收剩余内容超时")
                        break

            # TTS处理
            if voice and selected_info["response_id"]:
                audio_path = await self._request_tts(
                    selected_info["chat_id"],
                    selected_info["response_id"],
                    selected_info["account"],
                )
                if audio_path:
                    yield {
                        "voice": True,
                        "audio_path": audio_path,
                    }

            if selected_info["response_id"]:
                yield {
                    "response_id": selected_info["response_id"]
                }

            if selected_info["usage_data"]:
                yield {"usage": selected_info["usage_data"]}

            # 记录选中账号的TAS指标 (完整成功)
            end_time = time.time()
            duration = end_time - selected_info["start_time"]
            first_token_latency = (
                selected_info["first_token_time"]
                - selected_info["start_time"]
                if selected_info["first_token_time"]
                else duration
            )

            await self.account_pool.release_account(
                selected_info["account"],
                True,
                latency=first_token_latency,
                tokens=selected_info["tokens"],
                duration=duration,
                model=model,
            )

            # 完成后清理断点
            await self.account_pool.checkpoint_manager.remove_checkpoint(
                checkpoint_id
            )

        except Exception as e:
            # 异常时保存断点
            if selected_info and selected_info["tokens"] > 0:
                cp = StreamCheckpoint(
                    checkpoint_id=checkpoint_id,
                    message=message,
                    model=model,
                    chat_id=selected_info["chat_id"],
                    account_email=selected_info[
                        "account"
                    ].email,
                    parent_id=selected_info["response_id"],
                    accumulated_content=selected_info[
                        "accumulated_content"
                    ],
                    accumulated_chunks=[],
                    tokens_received=selected_info["tokens"],
                    last_chunk_time=time.time(),
                    created_at=time.time(),
                    chat_type=chat_type,
                    sub_chat_type=sub_chat_type,
                    feature_config_dict=(
                        feature_config.to_dict()
                        if feature_config
                        else {}
                    ),
                    file_objects=preprocessed_files,
                )
                await self.account_pool.checkpoint_manager.save_checkpoint(
                    cp
                )
                self._debug_print(
                    f"异常中断, 断点已保存: "
                    f"{selected_info['tokens']} tokens"
                )

            for info in task_info:
                info["cancel_event"].set()
                if not info["task"].done():
                    info["task"].cancel()
                if info.get("account"):
                    end_t = time.time()
                    dur = end_t - info["start_time"]
                    await self.account_pool.release_account(
                        info["account"],
                        False,
                        duration=dur,
                        model=model,
                    )
            raise

    async def chat_stream(
        self,
        message: str,
        model: str = "qwen3-coder-plus",
        file_paths: Union[str, List[str], None] = None,
        extracted_files: Optional[List[ExtractedFile]] = None,
        max_retries: int = 2,
        voice: bool = False,
        chat_type: str = "t2t",
        sub_chat_type: Optional[str] = None,
        feature_config: Optional[FeatureConfig] = None,
        enable_checkpoint: bool = True,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        聊天流 (使用并发竞速 + 断点续传)

        Args:
            message: 消息内容
            model: 模型名称
            file_paths: 文件路径列表
            extracted_files: 提取的文件列表
            max_retries: 最大重试次数
            voice: 是否启用TTS
            chat_type: 聊天类型
            sub_chat_type: 子聊天类型
            feature_config: 功能配置
            enable_checkpoint: 是否启用断点续传
        """
        await self.ensure_initialized()

        # 检查断点
        resume_checkpoint: Optional[StreamCheckpoint] = None
        if enable_checkpoint:
            resume_checkpoint = await self.account_pool.checkpoint_manager.find_checkpoint_for_message(
                message, model
            )
            if resume_checkpoint:
                self._debug_print(
                    f"找到可用断点: "
                    f"{resume_checkpoint.tokens_received} tokens, "
                    f"checkpoint_id="
                    f"{resume_checkpoint.checkpoint_id[:8]}..."
                )

        async with self.semaphore:
            for attempt in range(max_retries + 1):
                temp_files_to_cleanup: List[str] = []
                preprocess_account: Optional[Account] = None

                try:
                    preprocess_account = await self.account_pool.get_available_account(
                        wait_timeout=15.0, model=model
                    )

                    if not preprocess_account:
                        if attempt < max_retries:
                            await asyncio.sleep(2)
                            continue
                        else:
                            raise Exception(
                                "没有可用的账号进行文件预处理"
                            )

                    (
                        preprocessed_files,
                        temp_files_to_cleanup,
                        large_text_file_path,
                    ) = await self._preprocess_files_once(
                        file_paths,
                        extracted_files,
                        preprocess_account,
                        message,
                    )

                    await self.account_pool.release_account(
                        preprocess_account, True
                    )
                    preprocess_account = None

                    async for chunk in self._concurrent_chat_stream(
                        message,
                        model,
                        preprocessed_files,
                        large_text_file_path,
                        voice,
                        chat_type=chat_type,
                        sub_chat_type=sub_chat_type,
                        feature_config=feature_config,
                        resume_checkpoint=resume_checkpoint,
                    ):
                        yield chunk

                    # 成功完成, 清除断点
                    if resume_checkpoint:
                        await self.account_pool.checkpoint_manager.remove_checkpoint(
                            resume_checkpoint.checkpoint_id
                        )

                    return

                except Exception as e:
                    self._debug_print(
                        f"尝试 {attempt + 1}/"
                        f"{max_retries + 1} 失败: {e}"
                    )
                    if preprocess_account:
                        await self.account_pool.release_account(
                            preprocess_account, False
                        )

                    if attempt == max_retries:
                        raise
                    else:
                        await asyncio.sleep(1)

                finally:
                    FileUtils.cleanup_temp_files(
                        temp_files_to_cleanup
                    )

    async def chat_completion(
        self,
        message: str,
        model: str = "qwen3-coder-plus",
        file_paths: Union[str, List[str], None] = None,
        extracted_files: Optional[List[ExtractedFile]] = None,
        voice: bool = False,
        chat_type: str = "t2t",
        sub_chat_type: Optional[str] = None,
        feature_config: Optional[FeatureConfig] = None,
    ) -> Union[str, Dict[str, Any]]:
        """聊天补全 (使用并发竞速 + TAS)"""
        await self.ensure_initialized()

        async def _do_chat_completion() -> Union[
            str, Dict[str, Any]
        ]:
            temp_files_to_cleanup: List[str] = []
            preprocess_account: Optional[Account] = None

            try:
                preprocess_account = await self.account_pool.get_available_account(
                    wait_timeout=15.0, model=model
                )

                if not preprocess_account:
                    raise Exception(
                        "没有可用的账号进行文件预处理"
                    )

                (
                    preprocessed_files,
                    temp_files_to_cleanup,
                    large_text_file_path,
                ) = await self._preprocess_files_once(
                    file_paths,
                    extracted_files,
                    preprocess_account,
                    message,
                )

                await self.account_pool.release_account(
                    preprocess_account, True
                )
                preprocess_account = None

                result_content: List[str] = []
                audio_path: Optional[str] = None
                usage_data: Optional[Dict[str, Any]] = None
                thinking_summary: Optional[
                    ThinkingSummary
                ] = None
                image_results: List[ImageEditResult] = []
                deep_research_results: List[
                    DeepResearchResult
                ] = []

                async for chunk in self._concurrent_chat_stream(
                    message,
                    model,
                    preprocessed_files,
                    large_text_file_path,
                    voice,
                    chat_type=chat_type,
                    sub_chat_type=sub_chat_type,
                    feature_config=feature_config,
                ):
                    if isinstance(chunk, dict):
                        if "voice" in chunk:
                            audio_path = chunk.get(
                                "audio_path"
                            )
                        elif "usage" in chunk:
                            usage_data = chunk.get("usage")
                        elif "thinking_summary" in chunk:
                            thinking_summary = chunk.get(
                                "thinking_summary"
                            )
                        elif "image_gen" in chunk:
                            image_results.append(
                                chunk.get("image_gen")
                            )
                        elif "deep_research" in chunk:
                            deep_research_results.append(
                                chunk.get("deep_research")
                            )
                    else:
                        result_content.append(chunk)

                final_content = "".join(result_content)

                result: Dict[str, Any] = {
                    "text": final_content
                }

                if voice:
                    result["voice"] = (
                        True if audio_path else False
                    )
                    result["audio_path"] = audio_path

                if usage_data:
                    result["usage"] = usage_data

                if thinking_summary:
                    result[
                        "thinking_summary"
                    ] = thinking_summary

                if image_results:
                    result["images"] = image_results

                if deep_research_results:
                    result[
                        "deep_research"
                    ] = deep_research_results

                if (
                    not voice
                    and not thinking_summary
                    and not image_results
                    and not deep_research_results
                ):
                    return final_content

                return result

            except Exception as e:
                if preprocess_account:
                    await self.account_pool.release_account(
                        preprocess_account, False
                    )
                raise
            finally:
                FileUtils.cleanup_temp_files(
                    temp_files_to_cleanup
                )

        return await RetryManager.retry_on_empty_response(
            _do_chat_completion
        )

    async def image_edit(
        self,
        prompt: str,
        image_urls: List[str],
        model: str = "qwen3-max-2026-01-23",
    ) -> Dict[str, Any]:
        """
        图像编辑

        Args:
            prompt: 编辑指令
            image_urls: 图像URL列表
            model: 模型名称

        Returns:
            包含编辑结果的字典
        """
        await self.ensure_initialized()

        account: Optional[Account] = None
        try:
            account = (
                await self.account_pool.get_available_account(
                    wait_timeout=15.0, model=model
                )
            )
            if not account:
                raise Exception("没有可用的账号")

            files: List[Dict[str, Any]] = []
            for url in image_urls:
                file_info = await FileUtils.get_url_file_info(
                    self.session, url, account.user_id
                )
                file_obj = self._build_file_object(file_info)
                files.append(file_obj)

            chat_id = await self._get_or_create_chat_id(
                account, model, "image_edit"
            )

            result_content: List[str] = []
            image_results: List[ImageEditResult] = []

            start_time = time.time()
            async for chunk in self._send_chat_request(
                account,
                chat_id,
                prompt,
                model,
                files,
                chat_type="image_edit",
                sub_chat_type="image_edit",
            ):
                if isinstance(chunk, dict):
                    if "image_gen" in chunk:
                        image_results.append(
                            chunk["image_gen"]
                        )
                else:
                    result_content.append(chunk)

            duration = time.time() - start_time
            await self.account_pool.release_account(
                account,
                True,
                duration=duration,
                model=model,
            )

            return {
                "text": "".join(result_content),
                "images": image_results,
            }

        except Exception as e:
            if account:
                await self.account_pool.release_account(
                    account, False, model=model
                )
            raise

    async def deep_research(
        self,
        topic: str,
        model: str = "qwen3-max-2026-01-23",
        research_mode: str = "advance",
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        深度研究 (流式)

        Args:
            topic: 研究主题
            model: 模型名称
            research_mode: 研究模式 ("normal" 或 "advance")

        Yields:
            研究内容和状态更新
        """
        await self.ensure_initialized()

        fc = FeatureConfig(
            thinking_enabled=False,
            research_mode=research_mode,
            auto_search=True,
        )

        async for chunk in self.chat_stream(
            message=topic,
            model=model,
            chat_type="deep_research",
            sub_chat_type="deep_research",
            feature_config=fc,
        ):
            yield chunk

    async def deep_research_completion(
        self,
        topic: str,
        model: str = "qwen3-max-2026-01-23",
        research_mode: str = "advance",
    ) -> Dict[str, Any]:
        """
        深度研究 (非流式)

        Args:
            topic: 研究主题
            model: 模型名称
            research_mode: 研究模式

        Returns:
            研究结果
        """
        fc = FeatureConfig(
            thinking_enabled=False,
            research_mode=research_mode,
            auto_search=True,
        )

        return await self.chat_completion(
            message=topic,
            model=model,
            chat_type="deep_research",
            sub_chat_type="deep_research",
            feature_config=fc,
        )

    async def learn_chat(
        self,
        question: str,
        model: str = "qwen3-max-2026-01-23",
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        学习辅导对话 (流式)

        Args:
            question: 学习问题
            model: 模型名称

        Yields:
            回答内容和思考摘要
        """
        await self.ensure_initialized()

        fc = FeatureConfig(
            thinking_enabled=True,
            thinking_format="summary",
            auto_search=True,
        )

        async for chunk in self.chat_stream(
            message=question,
            model=model,
            chat_type="learn",
            sub_chat_type="learn",
            feature_config=fc,
        ):
            yield chunk

    async def learn_chat_completion(
        self,
        question: str,
        model: str = "qwen3-max-2026-01-23",
    ) -> Dict[str, Any]:
        """
        学习辅导对话 (非流式)

        Args:
            question: 学习问题
            model: 模型名称

        Returns:
            包含回答和思考摘要的字典
        """
        fc = FeatureConfig(
            thinking_enabled=True,
            thinking_format="summary",
            auto_search=True,
        )

        return await self.chat_completion(
            message=question,
            model=model,
            chat_type="learn",
            sub_chat_type="learn",
            feature_config=fc,
        )

    async def text_to_video(
        self,
        prompt: str,
        size: str = "16:9",
        model: str = "qwen3-max-2026-01-23",
        download: bool = False,
    ) -> VideoGenerationResult:
        """
        文本到视频生成

        Args:
            prompt: 视频描述
            size: 视频尺寸 ("16:9", "9:16", "1:1")
            model: 模型名称
            download: 是否下载视频

        Returns:
            视频生成结果
        """
        await self.ensure_initialized()

        if size not in ClientConfig.VIDEO_SIZES:
            raise ValueError(
                f"不支持的视频尺寸: {size}, "
                f"可选: {list(ClientConfig.VIDEO_SIZES.keys())}"
            )

        account: Optional[Account] = None
        try:
            account = (
                await self.account_pool.get_available_account(
                    wait_timeout=15.0, model=model
                )
            )
            if not account:
                raise Exception("没有可用的账号")

            chat_id = await self._get_or_create_chat_id(
                account, model, "t2v"
            )

            result = await self._send_video_request(
                account, chat_id, prompt, model, size
            )

            if download and result.success and result.video_url:
                try:
                    local_path = (
                        await self.video_service.download_video(
                            result.video_url
                        )
                    )
                    result.video_url = local_path
                except Exception as e:
                    self._debug_print(f"视频下载失败: {e}")

            await self.account_pool.release_account(
                account, result.success, model=model
            )

            return result

        except Exception as e:
            if account:
                await self.account_pool.release_account(
                    account, False, model=model
                )
            return VideoGenerationResult(
                success=False,
                request_id="",
                chat_id="",
                message_id="",
                task_id="",
                size=size,
                error=str(e),
            )

    async def artifacts_web_dev(
        self,
        requirement: str,
        model: str = "qwen3-max-2026-01-23",
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        Web开发/Artifacts (流式)

        Args:
            requirement: 开发需求
            model: 模型名称

        Yields:
            代码和说明内容
        """
        await self.ensure_initialized()

        async for chunk in self.chat_stream(
            message=requirement,
            model=model,
            chat_type="artifacts",
            sub_chat_type="web_dev",
        ):
            yield chunk

    async def count_tokens(
        self, message: str, model: str = "qwen3-coder-plus"
    ) -> TokenCountResult:
        """计算Token数"""
        await self.ensure_initialized()

        account: Optional[Account] = None
        try:
            account = (
                await self.account_pool.get_available_account(
                    wait_timeout=15.0, model=model
                )
            )

            if not account:
                raise Exception(
                    "没有可用的账号进行token计数"
                )

            count_prompt = (
                f"请直接回复OK, 不要有其他任何内容。"
                f"以下是需要计算token的内容:\n{message}"
            )

            chat_id = await self._get_or_create_chat_id(
                account, model
            )

            headers = {
                "Authorization": f"Bearer {account.token}",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "Content-Type": "application/json",
                "source": "web",
                "Origin": "https://chat.qwen.ai",
                "Referer": "https://chat.qwen.ai/",
            }

            payload = self._build_payload(
                count_prompt, chat_id, model, []
            )
            request_url = (
                f"https://chat.qwen.ai/api/v2/chat/completions"
                f"?chat_id={chat_id}"
            )

            input_tokens = 0
            output_tokens = 0

            async with self.session.post(
                request_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(
                        f"HTTP {response.status}: {error_text}"
                    )

                async for line in response.content:
                    if not line:
                        continue

                    try:
                        line_str = line.decode(
                            "utf-8", errors="replace"
                        ).strip()
                    except Exception:
                        continue

                    if (
                        not line_str
                        or not line_str.startswith("data: ")
                    ):
                        continue

                    data_str = line_str[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)

                        if "usage" in data:
                            usage = data["usage"]
                            input_tokens = usage.get(
                                "input_tokens",
                                usage.get("prompt_tokens", 0),
                            )
                            output_tokens = usage.get(
                                "output_tokens",
                                usage.get(
                                    "completion_tokens", 0
                                ),
                            )

                    except json.JSONDecodeError:
                        continue

            await self.account_pool.release_account(
                account, True, model=model
            )

            return TokenCountResult(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except Exception as e:
            if account:
                await self.account_pool.release_account(
                    account, False, model=model
                )
            logger.error(f"Token计数失败: {e}")
            estimated_tokens = len(message) // 3
            return TokenCountResult(
                input_tokens=estimated_tokens
            )

    async def chat_with_tts(
        self, text: str, model: str = "qwen3-coder-plus"
    ) -> Optional[str]:
        """TTS聊天"""
        await self.ensure_initialized()

        account: Optional[Account] = None
        try:
            account = (
                await self.account_pool.get_available_account(
                    wait_timeout=15.0, model=model
                )
            )

            if not account:
                self._debug_print(
                    "没有可用的账号进行TTS转换"
                )
                return None

            chat_id = await self._get_or_create_chat_id(
                account, model
            )

            quick_message = (
                "注意: 啥都不要说, 直接输出\\(\\)即可"
            )
            response_id: Optional[str] = None
            origin_content = ""

            async for chunk in self._send_chat_request(
                account, chat_id, quick_message, model, []
            ):
                if (
                    isinstance(chunk, dict)
                    and "response_id" in chunk
                ):
                    response_id = chunk["response_id"]
                elif isinstance(chunk, str):
                    origin_content += chunk

            if not response_id:
                self._debug_print("未能获取response_id")
                return None

            success = await self._replace_content(
                chat_id=chat_id,
                response_id=response_id,
                origin_content=origin_content.strip(),
                new_content=text,
                account=account,
            )

            if not success:
                self._debug_print("内容替换失败")
                return None

            audio_path = await self._request_tts(
                chat_id, response_id, account
            )

            if audio_path:
                self._debug_print(
                    f"TTS转换成功: {audio_path}"
                )
            else:
                self._debug_print("TTS转换失败")

            return audio_path

        except Exception as e:
            self._debug_print(f"chat_with_tts异常: {e}")
            return None
        finally:
            if account:
                await self.account_pool.release_account(
                    account, True, model=model
                )

    async def generate_image(
        self,
        prompt: str,
        n: int = 1,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        model: str = "flux",
        nologo: bool = True,
        response_format: str = "url",
    ) -> List[Dict[str, Any]]:
        """生成图像"""
        await self.ensure_initialized()

        return await self.image_service.generate_images(
            prompt=prompt,
            n=n,
            width=width,
            height=height,
            seed=seed,
            model=model,
            nologo=nologo,
            response_format=response_format,
        )

    def get_embedding(self, text: str) -> List[float]:
        """
        获取文本的嵌入向量 (同步方法)

        Args:
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        return self.embedding_service.get_embedding(text)

    async def get_embedding_async(
        self, text: str
    ) -> List[float]:
        """
        获取文本的嵌入向量 (异步方法)

        Args:
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        await self.ensure_initialized()
        return await self.embedding_service.get_embedding_async(
            self.session, text
        )

    async def get_embeddings_batch(
        self, texts: List[str]
    ) -> List[List[float]]:
        """
        批量获取文本的嵌入向量

        Args:
            texts: 输入文本列表

        Returns:
            嵌入向量列表的列表
        """
        await self.ensure_initialized()
        return await self.embedding_service.get_embeddings_batch(
            self.session, texts
        )

    async def get_account_status(self) -> Dict[str, Any]:
        """获取账号状态"""
        await self.ensure_initialized()
        return await self.account_pool.get_status()


# ==================== 便捷函数 ====================


def get_embedding(
    text: str,
    model: str = Config.EMBEDDING_MODEL,
    url: str = Config.EMBEDDING_API_URL,
) -> List[float]:
    """
    获取文本的嵌入向量 (独立函数, 兼容原有接口)

    Args:
        text: 输入文本
        model: 模型名称
        url: API地址

    Returns:
        嵌入向量列表
    """
    service = EmbeddingService(url=url, model=model)
    return service.get_embedding(text)


# ==================== 导出模块 ====================


__all__ = [
    # 枚举类
    "ChatType",
    "ResearchMode",
    "VideoSize",
    "ResponsePhase",
    # 配置类
    "Config",
    "ClientConfig",
    # 工具类
    "Base64FileHandler",
    "LargeTextHandler",
    "RetryManager",
    "FingerprintGenerator",
    "CookieManager",
    "FileUtils",
    # Cookie函数
    "get_ssxmod_itna",
    "get_ssxmod_itna2",
    # 数据类
    "Account",
    "AccountStats",
    "AccountPerformanceSample",
    "FileInfo",
    "ExtractedFile",
    "TokenCountResult",
    "LargeTextInfo",
    "PreCreatedChatId",
    "CachedFileUpload",
    "VideoGenerationResult",
    "DeepResearchResult",
    "EmbeddingResult",
    "ThinkingSummary",
    "ImageEditResult",
    "FeatureConfig",
    "StreamCheckpoint",
    # 解析器
    "AccountParser",
    # 缓存管理器
    "FileCacheManager",
    "ChatIdPool",
    # 断点续传管理器
    "CheckpointManager",
    # Track-and-Stop 选择器
    "TrackAndStopSelector",
    # 账号池
    "AsyncAccountPool",
    # 上传器
    "UniversalOSSUploader",
    # 服务类
    "ImageGenerationService",
    "VideoGenerationService",
    "EmbeddingService",
    # 主客户端
    "AsyncQwenClient",
    # 便捷函数
    "get_embedding",
]


# ==================== 测试入口 ====================


async def _test_client() -> None:
    """测试客户端 (含 Track-and-Stop 算法验证)"""
    client = AsyncQwenClient(debug=True)

    try:
        await client.ensure_initialized()

        # 测试获取状态 (含TAS统计)
        status = await client.get_account_status()
        logger.info(
            f"账号状态: {json.dumps(status, ensure_ascii=False, indent=2)}"
        )

        # 测试聊天
        logger.info("测试聊天...")
        response = await client.chat_completion(
            message="你好, 请简单介绍一下你自己。",
            model="qwen3-coder-plus",
        )
        logger.info(f"聊天响应: {response}")

        # 测试流式聊天
        logger.info("测试流式聊天...")
        full_response: List[str] = []
        async for chunk in client.chat_stream(
            message="请用一句话描述今天的天气。",
            model="qwen3-coder-plus",
        ):
            if isinstance(chunk, str):
                full_response.append(chunk)
                print(chunk, end="", flush=True)
        print()
        logger.info(
            f"流式响应完成, 总长度: {len(''.join(full_response))}"
        )

        # 测试嵌入向量
        logger.info("测试嵌入向量...")
        try:
            embedding = await client.get_embedding_async(
                "这是一个测试文本"
            )
            logger.info(f"嵌入向量维度: {len(embedding)}")
        except Exception as e:
            logger.warning(f"嵌入向量测试失败: {e}")

        # 测试学习对话
        logger.info("测试学习对话...")
        learn_result = await client.learn_chat_completion(
            question="什么是机器学习?"
        )
        logger.info(f"学习对话结果: {learn_result}")

        # 再次获取状态, 验证TAS统计更新
        status_after = await client.get_account_status()
        tas_info = status_after.get("tas_algorithm", {})
        logger.info(
            f"TAS算法统计: "
            f"总选择={tas_info.get('total_selections', 0)}, "
            f"探索率={tas_info.get('current_epsilon', 0):.4f}, "
            f"探索/利用比="
            f"{tas_info.get('explore_ratio', 0):.4f}"
        )

    except Exception as e:
        logger.error(f"测试失败: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(_test_client())
