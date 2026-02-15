"""
qwen_server.py
Qwen API 服务器模块 - 负责处理外界用户请求
版本: 6.0.0

核心特性:
- 集成 Qwen 风格函数调用提示词系统
- 公平请求调度器 (FIFO) - 等待最久的请求优先响应
- 异步并发支持 - 最大化吞吐量
- 生产级性能监控和指标

工作流:
- 向 qwen_client 发送请求时: 使用 Qwen fncall 格式
- 向外部用户响应时: 保持 OpenAI/Anthropic 兼容格式
- 并发控制: 通过 FairRequestScheduler 实现 FIFO 公平调度
"""

import asyncio
import json
import os
import sys
import time
import uuid
from asyncio import Lock, Event
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Deque, Dict, List, Optional, Tuple, Union
import re

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Header, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import logging

from qwen_client import *
from qwen_util import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("qwen_server")


# ====================
# 调度器异常
# ====================


class SchedulerError(Exception):
    """调度器基础异常"""
    pass


class QueueFullError(SchedulerError):
    """队列已满异常 - 应返回 HTTP 429"""

    def __init__(self, scheduler_name: str, queue_depth: int, max_queue: int) -> None:
        self.scheduler_name = scheduler_name
        self.queue_depth = queue_depth
        self.max_queue = max_queue
        super().__init__(
            f"调度器 '{scheduler_name}' 队列已满: "
            f"{queue_depth}/{max_queue}"
        )


class SlotTimeoutError(SchedulerError):
    """槽位等待超时异常 - 应返回 HTTP 504"""

    def __init__(
        self, scheduler_name: str, waited_seconds: float, timeout: float
    ) -> None:
        self.scheduler_name = scheduler_name
        self.waited_seconds = waited_seconds
        self.timeout = timeout
        super().__init__(
            f"调度器 '{scheduler_name}' 等待超时: "
            f"已等待 {waited_seconds:.1f}s, 超时阈值 {timeout:.1f}s"
        )


class SchedulerShutdownError(SchedulerError):
    """调度器已关闭异常 - 应返回 HTTP 503"""

    def __init__(self, scheduler_name: str) -> None:
        self.scheduler_name = scheduler_name
        super().__init__(f"调度器 '{scheduler_name}' 已关闭，不再接受新请求")


# ====================
# 请求槽位
# ====================


@dataclass
class RequestSlot:
    """
    请求处理槽位

    表示一个已获得处理权限的请求，
    持有者负责在完成后调用 release() 释放槽位。
    """

    request_id: str
    scheduler_name: str
    enqueued_at: float
    acquired_at: float
    _released: bool = field(default=False, repr=False)
    _release_callback: Any = field(default=None, repr=False)

    @property
    def wait_duration(self) -> float:
        """等待时长（秒）"""
        return self.acquired_at - self.enqueued_at

    async def release(self) -> None:
        """释放槽位，允许下一个等待的请求获得处理权"""
        if self._released:
            return
        self._released = True
        if self._release_callback is not None:
            await self._release_callback(self)

    def __del__(self) -> None:
        if not self._released:
            logger.warning(
                f"RequestSlot {self.request_id} 未被正确释放 "
                f"(scheduler={self.scheduler_name})"
            )


# ====================
# FIFO 等待条目
# ====================


@dataclass
class _WaiterEntry:
    """队列中等待处理的请求条目"""

    request_id: str
    event: Event
    enqueued_at: float
    cancelled: bool = False


# ====================
# 公平请求调度器
# ====================


class FairRequestScheduler:
    """
    公平请求调度器 (FIFO)

    核心保证:
    - 等待最久的请求优先获得处理槽位
    - 通过并发上限防止后端过载
    - 通过队列上限防止内存耗尽
    - 通过超时机制防止请求无限等待

    使用方式:
        async with scheduler.acquire("req-123") as slot:
            # slot 已获得，可以执行后端调用
            result = await backend_call()

        # 或者手动管理:
        slot = await scheduler.acquire_slot("req-123")
        try:
            result = await backend_call()
        finally:
            await slot.release()
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 20,
        max_queue_size: int = 200,
        default_timeout: float = 120.0,
    ) -> None:
        self._name = name
        self._max_concurrent = max_concurrent
        self._max_queue_size = max_queue_size
        self._default_timeout = default_timeout

        # 状态
        self._active_count: int = 0
        self._waiters: Deque[_WaiterEntry] = deque()
        self._lock = Lock()
        self._shutdown = False

        # 统计指标（原子性由 GIL 保证，读取无需加锁）
        self._total_processed: int = 0
        self._total_rejected: int = 0
        self._total_timed_out: int = 0
        self._total_wait_time: float = 0.0
        self._max_wait_time: float = 0.0
        self._peak_active: int = 0
        self._peak_queue: int = 0

        logger.info(
            f"调度器 '{name}' 初始化: "
            f"max_concurrent={max_concurrent}, "
            f"max_queue={max_queue_size}, "
            f"timeout={default_timeout}s"
        )

    @property
    def name(self) -> str:
        """调度器名称"""
        return self._name

    @property
    def active_count(self) -> int:
        """当前活跃请求数"""
        return self._active_count

    @property
    def queue_depth(self) -> int:
        """当前队列深度（不含已取消的条目）"""
        return sum(1 for w in self._waiters if not w.cancelled)

    async def acquire_slot(
        self,
        request_id: str,
        timeout: Optional[float] = None,
    ) -> RequestSlot:
        """
        获取处理槽位

        如果有空闲槽位，立即返回。
        否则排队等待，直到获得槽位或超时。

        Args:
            request_id: 请求唯一标识
            timeout: 超时时间（秒），None 使用默认值

        Returns:
            RequestSlot: 已获得的处理槽位

        Raises:
            SchedulerShutdownError: 调度器已关闭
            QueueFullError: 队列已满
            SlotTimeoutError: 等待超时
        """
        if timeout is None:
            timeout = self._default_timeout

        enqueued_at = time.monotonic()

        async with self._lock:
            if self._shutdown:
                raise SchedulerShutdownError(self._name)

            # 有空闲槽位，直接获取
            if self._active_count < self._max_concurrent:
                self._active_count += 1
                if self._active_count > self._peak_active:
                    self._peak_active = self._active_count

                return RequestSlot(
                    request_id=request_id,
                    scheduler_name=self._name,
                    enqueued_at=enqueued_at,
                    acquired_at=time.monotonic(),
                    _release_callback=self._on_slot_released,
                )

            # 检查队列是否已满
            current_queue = self.queue_depth
            if current_queue >= self._max_queue_size:
                self._total_rejected += 1
                raise QueueFullError(self._name, current_queue, self._max_queue_size)

            # 入队等待
            waiter = _WaiterEntry(
                request_id=request_id,
                event=Event(),
                enqueued_at=enqueued_at,
            )
            self._waiters.append(waiter)
            queue_size = self.queue_depth
            if queue_size > self._peak_queue:
                self._peak_queue = queue_size

        # 在锁外等待信号
        try:
            await asyncio.wait_for(waiter.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # 超时：标记取消并清理
            waiter.cancelled = True
            async with self._lock:
                self._total_timed_out += 1
                # 从队列中移除（可能已被 dispatch 移除）
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
            waited = time.monotonic() - enqueued_at
            raise SlotTimeoutError(self._name, waited, timeout)
        except asyncio.CancelledError:
            # 客户端断开：标记取消并清理
            waiter.cancelled = True
            async with self._lock:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
            raise

        # 事件被触发 - 检查是否在此期间被取消或关闭
        if waiter.cancelled:
            # 被取消但事件已触发 - 需要释放槽位给下一个等待者
            async with self._lock:
                self._active_count -= 1
                await self._dispatch_next_locked()
            raise asyncio.CancelledError()

        if self._shutdown:
            async with self._lock:
                self._active_count -= 1
            raise SchedulerShutdownError(self._name)

        acquired_at = time.monotonic()
        wait_duration = acquired_at - enqueued_at
        self._total_wait_time += wait_duration
        if wait_duration > self._max_wait_time:
            self._max_wait_time = wait_duration

        return RequestSlot(
            request_id=request_id,
            scheduler_name=self._name,
            enqueued_at=enqueued_at,
            acquired_at=acquired_at,
            _release_callback=self._on_slot_released,
        )

    @asynccontextmanager
    async def acquire(
        self,
        request_id: str,
        timeout: Optional[float] = None,
    ):
        """
        上下文管理器方式获取槽位

        async with scheduler.acquire("req-123") as slot:
            # 执行后端调用
            pass
        # 自动释放
        """
        slot = await self.acquire_slot(request_id, timeout)
        try:
            yield slot
        finally:
            await slot.release()

    async def _on_slot_released(self, slot: RequestSlot) -> None:
        """槽位释放回调"""
        async with self._lock:
            self._active_count -= 1
            self._total_processed += 1
            await self._dispatch_next_locked()

    async def _dispatch_next_locked(self) -> None:
        """
        分派下一个等待的请求（必须在持锁状态下调用）

        从队列头部取出未取消的等待者，激活其事件。
        """
        while self._waiters and self._active_count < self._max_concurrent:
            waiter = self._waiters.popleft()
            if waiter.cancelled:
                continue

            self._active_count += 1
            if self._active_count > self._peak_active:
                self._peak_active = self._active_count

            waiter.event.set()
            break

    async def shutdown(self, drain_timeout: float = 10.0) -> int:
        """
        优雅关闭调度器

        1. 拒绝新请求
        2. 取消所有排队中的请求
        3. 等待活跃请求完成

        Args:
            drain_timeout: 等待活跃请求完成的超时时间

        Returns:
            被取消的排队请求数
        """
        self._shutdown = True
        cancelled_count = 0

        async with self._lock:
            while self._waiters:
                waiter = self._waiters.popleft()
                if not waiter.cancelled:
                    waiter.cancelled = True
                    waiter.event.set()  # 唤醒以让其检测关闭状态
                    cancelled_count += 1

        # 等待活跃请求完成
        deadline = time.monotonic() + drain_timeout
        while self._active_count > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        if self._active_count > 0:
            logger.warning(
                f"调度器 '{self._name}' 关闭时仍有 "
                f"{self._active_count} 个活跃请求"
            )

        logger.info(
            f"调度器 '{self._name}' 已关闭: "
            f"取消 {cancelled_count} 个排队请求"
        )
        return cancelled_count

    def get_metrics(self) -> Dict[str, Any]:
        """获取调度器指标"""
        avg_wait = (
            self._total_wait_time / self._total_processed
            if self._total_processed > 0
            else 0.0
        )

        return {
            "name": self._name,
            "max_concurrent": self._max_concurrent,
            "max_queue_size": self._max_queue_size,
            "default_timeout_seconds": self._default_timeout,
            "current": {
                "active_requests": self._active_count,
                "queue_depth": self.queue_depth,
                "utilization": (
                    self._active_count / self._max_concurrent
                    if self._max_concurrent > 0
                    else 0.0
                ),
            },
            "totals": {
                "processed": self._total_processed,
                "rejected_queue_full": self._total_rejected,
                "timed_out": self._total_timed_out,
            },
            "wait_time": {
                "average_seconds": round(avg_wait, 3),
                "max_seconds": round(self._max_wait_time, 3),
            },
            "peaks": {
                "max_active": self._peak_active,
                "max_queue_depth": self._peak_queue,
            },
            "shutdown": self._shutdown,
        }


# ====================
# 调度器配置
# ====================


class SchedulerConfig:
    """调度器配置 - 通过环境变量可覆盖"""

    # 聊天调度器 - 高并发，核心业务
    CHAT_MAX_CONCURRENT: int = int(os.environ.get("SCHED_CHAT_CONCURRENT", "50"))
    CHAT_MAX_QUEUE: int = int(os.environ.get("SCHED_CHAT_QUEUE", "500"))
    CHAT_TIMEOUT: float = float(os.environ.get("SCHED_CHAT_TIMEOUT", "120"))

    # 媒体调度器 - 资源密集型（图像/视频/TTS）
    MEDIA_MAX_CONCURRENT: int = int(os.environ.get("SCHED_MEDIA_CONCURRENT", "10"))
    MEDIA_MAX_QUEUE: int = int(os.environ.get("SCHED_MEDIA_QUEUE", "100"))
    MEDIA_TIMEOUT: float = float(os.environ.get("SCHED_MEDIA_TIMEOUT", "180"))

    # 辅助调度器 - 嵌入/研究/学习等
    AUX_MAX_CONCURRENT: int = int(os.environ.get("SCHED_AUX_CONCURRENT", "20"))
    AUX_MAX_QUEUE: int = int(os.environ.get("SCHED_AUX_QUEUE", "200"))
    AUX_TIMEOUT: float = float(os.environ.get("SCHED_AUX_TIMEOUT", "300"))


# ====================
# 全局请求指标
# ====================


class GlobalRequestMetrics:
    """全局请求指标追踪"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._total_requests: int = 0
        self._active_requests: int = 0
        self._peak_active: int = 0
        self._total_duration: float = 0.0
        self._max_duration: float = 0.0
        self._status_counts: Dict[int, int] = {}
        self._endpoint_counts: Dict[str, int] = {}
        self._start_time: float = time.monotonic()

    async def record_request_start(self, endpoint: str) -> float:
        """记录请求开始，返回开始时间"""
        async with self._lock:
            self._total_requests += 1
            self._active_requests += 1
            if self._active_requests > self._peak_active:
                self._peak_active = self._active_requests
            self._endpoint_counts[endpoint] = (
                self._endpoint_counts.get(endpoint, 0) + 1
            )
        return time.monotonic()

    async def record_request_end(
        self, start_time: float, status_code: int
    ) -> None:
        """记录请求结束"""
        duration = time.monotonic() - start_time
        async with self._lock:
            self._active_requests -= 1
            self._total_duration += duration
            if duration > self._max_duration:
                self._max_duration = duration
            self._status_counts[status_code] = (
                self._status_counts.get(status_code, 0) + 1
            )

    def get_metrics(self) -> Dict[str, Any]:
        """获取全局指标"""
        uptime = time.monotonic() - self._start_time
        avg_duration = (
            self._total_duration / self._total_requests
            if self._total_requests > 0
            else 0.0
        )
        rps = self._total_requests / uptime if uptime > 0 else 0.0

        return {
            "uptime_seconds": round(uptime, 1),
            "total_requests": self._total_requests,
            "active_requests": self._active_requests,
            "peak_active_requests": self._peak_active,
            "requests_per_second": round(rps, 2),
            "duration": {
                "average_seconds": round(avg_duration, 3),
                "max_seconds": round(self._max_duration, 3),
            },
            "status_codes": dict(self._status_counts),
            "endpoints": dict(self._endpoint_counts),
        }


# ====================
# 错误响应辅助函数
# ====================


def _raise_scheduler_error_openai(exc: SchedulerError) -> None:
    """将调度器异常转换为 OpenAI 风格的 HTTP 异常"""
    if isinstance(exc, QueueFullError):
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        f"Server is at capacity. Queue full: "
                        f"{exc.queue_depth}/{exc.max_queue}. "
                        f"Please retry later."
                    ),
                    type="rate_limit_error",
                    code="queue_full",
                )
            ).model_dump(),
        )
    elif isinstance(exc, SlotTimeoutError):
        raise HTTPException(
            status_code=504,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        f"Request timed out waiting for processing slot "
                        f"after {exc.waited_seconds:.1f}s. "
                        f"Please retry later."
                    ),
                    type="timeout_error",
                    code="slot_timeout",
                )
            ).model_dump(),
        )
    elif isinstance(exc, SchedulerShutdownError):
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message="Server is shutting down. Please retry later.",
                    type="service_unavailable",
                    code="server_shutdown",
                )
            ).model_dump(),
        )
    else:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Scheduler error: {str(exc)}",
                    type="server_error",
                    code="scheduler_error",
                )
            ).model_dump(),
        )


def _raise_scheduler_error_anthropic(exc: SchedulerError) -> None:
    """将调度器异常转换为 Anthropic 风格的 HTTP 异常"""
    if isinstance(exc, QueueFullError):
        raise HTTPException(
            status_code=429,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="rate_limit_error",
                    message=(
                        f"Server is at capacity. Queue full: "
                        f"{exc.queue_depth}/{exc.max_queue}. "
                        f"Please retry later."
                    ),
                )
            ).model_dump(),
        )
    elif isinstance(exc, SlotTimeoutError):
        raise HTTPException(
            status_code=504,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="timeout_error",
                    message=(
                        f"Request timed out waiting for processing slot "
                        f"after {exc.waited_seconds:.1f}s."
                    ),
                )
            ).model_dump(),
        )
    elif isinstance(exc, SchedulerShutdownError):
        raise HTTPException(
            status_code=503,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="overloaded_error",
                    message="Server is shutting down. Please retry later.",
                )
            ).model_dump(),
        )
    else:
        raise HTTPException(
            status_code=500,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="api_error",
                    message=f"Scheduler error: {str(exc)}",
                )
            ).model_dump(),
        )


def _yield_stream_error_openai(exc: SchedulerError) -> str:
    """将调度器异常转换为流式错误块（OpenAI 格式）"""
    if isinstance(exc, QueueFullError):
        code = "queue_full"
        message = (
            f"Server is at capacity. Queue full: "
            f"{exc.queue_depth}/{exc.max_queue}."
        )
    elif isinstance(exc, SlotTimeoutError):
        code = "slot_timeout"
        message = (
            f"Request timed out waiting for processing slot "
            f"after {exc.waited_seconds:.1f}s."
        )
    elif isinstance(exc, SchedulerShutdownError):
        code = "server_shutdown"
        message = "Server is shutting down."
    else:
        code = "scheduler_error"
        message = str(exc)

    error_chunk = {
        "error": {
            "message": message,
            "type": "server_error",
            "code": code,
        }
    }
    return f"data: {json.dumps(error_chunk)}\n\ndata: [DONE]\n\n"


def _yield_stream_error_anthropic(exc: SchedulerError) -> str:
    """将调度器异常转换为流式错误事件（Anthropic 格式）"""
    if isinstance(exc, QueueFullError):
        error_type = "rate_limit_error"
        message = (
            f"Server is at capacity. Queue full: "
            f"{exc.queue_depth}/{exc.max_queue}."
        )
    elif isinstance(exc, SlotTimeoutError):
        error_type = "timeout_error"
        message = (
            f"Request timed out after {exc.waited_seconds:.1f}s."
        )
    elif isinstance(exc, SchedulerShutdownError):
        error_type = "overloaded_error"
        message = "Server is shutting down."
    else:
        error_type = "api_error"
        message = str(exc)

    error_event = {
        "type": "error",
        "error": {"type": error_type, "message": message},
    }
    return f"event: error\ndata: {json.dumps(error_event)}\n\n"


# ====================
# Qwen API 服务器
# ====================


class QwenAPIServer:
    """Qwen API 服务器"""

    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        self.client: Optional[AsyncQwenClient] = None
        self.available_models = ServerConfig.AVAILABLE_MODELS
        self.available_image_models = ServerConfig.AVAILABLE_IMAGE_MODELS
        self._initialized = False

        # 处理器
        self.tool_processor = ToolCallProcessor()
        self.reasoning_processor = ReasoningContentProcessor()
        self.file_extractor = MessageFileExtractor()
        self.message_converter = AnthropicMessageConverter()
        self.fncall_processor = QwenFnCallPromptProcessor()

        # 模型验证器
        self.model_validator = ModelValidator(
            available_models=self.available_models,
            available_image_models=self.available_image_models,
            anthropic_model_mapping=ServerConfig.ANTHROPIC_MODEL_MAPPING,
            default_model=ServerConfig.DEFAULT_MODEL,
            default_image_model=ServerConfig.DEFAULT_IMAGE_MODEL,
        )

        # 存储
        self.batch_store = MessageBatchStore()
        self.file_store = AnthropicFileStore()

        # 调度器
        self.chat_scheduler = FairRequestScheduler(
            name="chat",
            max_concurrent=SchedulerConfig.CHAT_MAX_CONCURRENT,
            max_queue_size=SchedulerConfig.CHAT_MAX_QUEUE,
            default_timeout=SchedulerConfig.CHAT_TIMEOUT,
        )
        self.media_scheduler = FairRequestScheduler(
            name="media",
            max_concurrent=SchedulerConfig.MEDIA_MAX_CONCURRENT,
            max_queue_size=SchedulerConfig.MEDIA_MAX_QUEUE,
            default_timeout=SchedulerConfig.MEDIA_TIMEOUT,
        )
        self.aux_scheduler = FairRequestScheduler(
            name="auxiliary",
            max_concurrent=SchedulerConfig.AUX_MAX_CONCURRENT,
            max_queue_size=SchedulerConfig.AUX_MAX_QUEUE,
            default_timeout=SchedulerConfig.AUX_TIMEOUT,
        )

        # 全局指标
        self.metrics = GlobalRequestMetrics()

    def _log_debug(self, message: str) -> None:
        """调试日志"""
        if self.debug:
            logger.debug(f"[Server] {message}")

    def _format_tool_calls_for_response(
        self,
        tool_calls: Optional[List[Any]],
    ) -> Optional[List[ToolCall]]:
        """格式化工具调用以符合 OpenAI 规范"""
        if not tool_calls:
            return None

        formatted: List[ToolCall] = []
        for tc in tool_calls:
            if isinstance(tc, ToolCall):
                formatted.append(tc)
                continue

            if isinstance(tc, dict):
                tool_call = tc.copy()
            elif hasattr(tc, "model_dump"):
                tool_call = tc.model_dump()
            elif hasattr(tc, "__dict__"):
                tool_call = dict(tc.__dict__)
            else:
                continue

            tc_id = tool_call.get("id") or f"call_{uuid.uuid4().hex[:24]}"

            func = tool_call.get("function", {})
            func_name = func.get("name", "")
            func_args = func.get("arguments")

            if func_args is not None and not isinstance(func_args, str):
                func_args = json.dumps(func_args, ensure_ascii=False)
            elif func_args is None:
                func_args = "{}"

            formatted.append(
                ToolCall(
                    id=tc_id,
                    type="function",
                    function=FunctionCall(name=func_name, arguments=func_args),
                )
            )

        return formatted if formatted else None

    def _format_tool_calls_for_stream(
        self,
        tool_calls: Optional[List[Any]],
    ) -> Optional[List[ToolCallChunk]]:
        """格式化流式工具调用（需要 index 字段）"""
        if not tool_calls:
            return None

        formatted: List[ToolCallChunk] = []
        for i, tc in enumerate(tool_calls):
            if isinstance(tc, ToolCallChunk):
                formatted.append(tc)
                continue

            if isinstance(tc, ToolCall):
                formatted.append(
                    ToolCallChunk(
                        index=i,
                        id=tc.id,
                        type=tc.type,
                        function=tc.function,
                    )
                )
                continue

            if isinstance(tc, dict):
                tool_call = tc.copy()
            elif hasattr(tc, "model_dump"):
                tool_call = tc.model_dump()
            elif hasattr(tc, "__dict__"):
                tool_call = dict(tc.__dict__)
            else:
                continue

            tc_id = tool_call.get("id") or f"call_{uuid.uuid4().hex[:24]}"

            func = tool_call.get("function", {})
            func_name = func.get("name", "")
            func_args = func.get("arguments")

            if func_args is not None and not isinstance(func_args, str):
                func_args = json.dumps(func_args, ensure_ascii=False)
            elif func_args is None:
                func_args = "{}"

            formatted.append(
                ToolCallChunk(
                    index=i,
                    id=tc_id,
                    type="function",
                    function=FunctionCall(name=func_name, arguments=func_args),
                )
            )

        return formatted if formatted else None

    async def initialize(self) -> None:
        """初始化服务器"""
        if self._initialized:
            return

        try:
            self.client = AsyncQwenClient(debug=self.debug)
            await self.client.ensure_initialized()
            self._initialized = True
            logger.info(
                f"Qwen API 服务器初始化完成，监听端口: {ServerConfig.PORT}"
            )
        except Exception as e:
            logger.error(f"服务器初始化失败: {e}")
            raise

    async def shutdown(self) -> None:
        """关闭服务器"""
        try:
            # 关闭所有调度器
            logger.info("正在关闭调度器...")
            shutdown_tasks = [
                self.chat_scheduler.shutdown(drain_timeout=15.0),
                self.media_scheduler.shutdown(drain_timeout=30.0),
                self.aux_scheduler.shutdown(drain_timeout=15.0),
            ]
            results = await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"调度器关闭异常: {result}")

            if self.client:
                await self.client.close()

            self._initialized = False
            logger.info("Qwen API 服务器已关闭")
        except Exception as e:
            logger.error(f"服务关闭异常: {e}")

    def get_scheduler_metrics(self) -> Dict[str, Any]:
        """获取所有调度器的指标"""
        return {
            "schedulers": {
                "chat": self.chat_scheduler.get_metrics(),
                "media": self.media_scheduler.get_metrics(),
                "auxiliary": self.aux_scheduler.get_metrics(),
            },
            "global": self.metrics.get_metrics(),
        }

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """聊天补全 - 使用 Qwen fncall 格式与 qwen_client 通信"""
        completion_id = IDGenerator.generate_completion_id()
        created_time = int(time.time())
        system_fingerprint = IDGenerator.generate_system_fingerprint()
        model = self.model_validator.validate_model(request.model)

        prompt = MessageBuilder.build_prompt_from_messages(
            request.messages, request.tools
        )
        extracted_files = self.file_extractor.extract_files_from_messages(
            request.messages
        )

        self._log_debug(
            f"处理非流式请求: model={model}, files={len(extracted_files)}, "
            f"has_tools={bool(request.tools)}"
        )

        try:
            result = await self.client.chat_completion(
                message=prompt,
                model=model,
                extracted_files=extracted_files if extracted_files else None,
                voice=request.voice,
            )

            if isinstance(result, dict):
                raw_content = result.get("text", "")
                audio_path = result.get("audio_path")
            else:
                raw_content = str(result)
                audio_path = None

            tool_calls: Optional[List[ToolCall]] = None
            reasoning_content: Optional[str] = None
            formal_content: Optional[str] = None

            if request.tools:
                reasoning_content, formal_content, parsed_tool_calls = (
                    QwenFnCallPromptProcessor.postprocess_qwen_response(
                        raw_content
                    )
                )
                if parsed_tool_calls:
                    tool_calls = self._format_tool_calls_for_response(
                        parsed_tool_calls
                    )
            else:
                reasoning_content, formal_content = (
                    self.reasoning_processor.extract_reasoning(raw_content)
                )

            finish_reason = "stop"
            if tool_calls:
                finish_reason = "tool_calls"

            prompt_tokens = len(prompt) // 3
            completion_tokens = len(formal_content or "") // 3

            audio_url = None
            if audio_path and os.path.exists(audio_path):
                audio_url = (
                    f"/v1/audio/files/{os.path.basename(audio_path)}"
                )

            response_message = ChatCompletionMessageResponse(
                role="assistant",
                content=formal_content,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls if tool_calls else None,
            )

            return ChatCompletionResponse(
                id=completion_id,
                created=created_time,
                model=model,
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=response_message,
                        finish_reason=finish_reason,
                    )
                ],
                usage=ChatCompletionUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
                system_fingerprint=system_fingerprint,
                audio_url=audio_url,
            )

        except Exception as e:
            logger.error(f"聊天补全错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="internal_error",
                    )
                ).model_dump(),
            )

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        disconnect_event: Optional[Event] = None,
    ) -> AsyncGenerator[str, None]:
        """
        聊天补全流 - 使用 Qwen fncall 格式

        流式处理策略:
        1. 累积完整的模型输出
        2. 实时检测 fncall 标记
        3. 标记前的文本立即流式输出给用户
        4. 标记后的内容缓冲，直到完整解析后以 tool_calls 格式输出
        """
        completion_id = IDGenerator.generate_completion_id()
        created_time = int(time.time())
        system_fingerprint = IDGenerator.generate_system_fingerprint()
        model = self.model_validator.validate_model(request.model)

        prompt = MessageBuilder.build_prompt_from_messages(
            request.messages, request.tools
        )
        extracted_files = self.file_extractor.extract_files_from_messages(
            request.messages
        )
        self._log_debug(
            f"处理流式请求: model={model}, files={len(extracted_files)}, "
            f"has_tools={bool(request.tools)}"
        )

        try:
            # 发送初始块
            initial_chunk = ChatCompletionChunk(
                id=completion_id,
                created=created_time,
                model=model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionChunkDelta(role="assistant"),
                        finish_reason=None,
                    )
                ],
                system_fingerprint=system_fingerprint,
            )
            yield f"data: {initial_chunk.model_dump_json()}\n\n"

            audio_path = None
            full_content = ""
            in_think_block = False
            think_content = ""
            completion_tokens = 0
            real_usage = None
            has_tools = bool(request.tools)

            # Qwen fncall 流式状态
            fncall_detected = False
            fncall_buffer = ""
            displayable_buffer = ""

            async for chunk in self.client.chat_stream(
                message=prompt,
                model=model,
                extracted_files=(
                    extracted_files if extracted_files else None
                ),
                voice=request.voice,
            ):
                if disconnect_event and disconnect_event.is_set():
                    self._log_debug("检测到客户端断开连接")
                    break

                if isinstance(chunk, dict):
                    if "audio_path" in chunk:
                        audio_path = chunk["audio_path"]
                    elif "usage" in chunk:
                        real_usage = chunk["usage"]
                    continue

                if not chunk:
                    continue

                full_content += chunk

                if has_tools:
                    if not fncall_detected:
                        displayable_buffer += chunk

                        if QwenFnCallPromptProcessor.detect_fncall_in_stream(
                            displayable_buffer
                        ):
                            fncall_detected = True
                            display_text, fncall_part = (
                                QwenFnCallPromptProcessor.split_stream_content(
                                    displayable_buffer
                                )
                            )
                            fncall_buffer = fncall_part

                            if display_text.strip():
                                stream_result = self._process_stream_chunk(
                                    chunk=display_text,
                                    in_think_block=in_think_block,
                                    think_content=think_content,
                                    completion_id=completion_id,
                                    created_time=created_time,
                                    model=model,
                                    system_fingerprint=system_fingerprint,
                                )
                                in_think_block = stream_result[
                                    "in_think_block"
                                ]
                                think_content = stream_result[
                                    "think_content"
                                ]
                                completion_tokens += stream_result[
                                    "tokens_added"
                                ]
                                for output_chunk in stream_result["chunks"]:
                                    yield output_chunk

                            displayable_buffer = ""
                        else:
                            safe_text = ""
                            pending = displayable_buffer

                            has_partial = False
                            for marker in [
                                FN_NAME,
                                FN_ARGS,
                                FN_RESULT,
                                FN_EXIT,
                            ]:
                                for i in range(1, len(marker)):
                                    prefix = marker[:i]
                                    if pending.endswith(prefix):
                                        safe_text = pending[
                                            : -len(prefix)
                                        ]
                                        displayable_buffer = prefix
                                        has_partial = True
                                        break
                                if has_partial:
                                    break

                            if not has_partial:
                                safe_text = displayable_buffer
                                displayable_buffer = ""

                            if safe_text:
                                stream_result = (
                                    self._process_stream_chunk(
                                        chunk=safe_text,
                                        in_think_block=in_think_block,
                                        think_content=think_content,
                                        completion_id=completion_id,
                                        created_time=created_time,
                                        model=model,
                                        system_fingerprint=system_fingerprint,
                                    )
                                )
                                in_think_block = stream_result[
                                    "in_think_block"
                                ]
                                think_content = stream_result[
                                    "think_content"
                                ]
                                completion_tokens += stream_result[
                                    "tokens_added"
                                ]
                                for output_chunk in stream_result[
                                    "chunks"
                                ]:
                                    yield output_chunk
                    else:
                        fncall_buffer += chunk
                else:
                    stream_result = self._process_stream_chunk(
                        chunk=chunk,
                        in_think_block=in_think_block,
                        think_content=think_content,
                        completion_id=completion_id,
                        created_time=created_time,
                        model=model,
                        system_fingerprint=system_fingerprint,
                    )
                    in_think_block = stream_result["in_think_block"]
                    think_content = stream_result["think_content"]
                    completion_tokens += stream_result["tokens_added"]
                    for output_chunk in stream_result["chunks"]:
                        yield output_chunk

            # 流结束后：处理剩余的 displayable_buffer
            if displayable_buffer and not fncall_detected:
                stream_result = self._process_stream_chunk(
                    chunk=displayable_buffer,
                    in_think_block=in_think_block,
                    think_content=think_content,
                    completion_id=completion_id,
                    created_time=created_time,
                    model=model,
                    system_fingerprint=system_fingerprint,
                )
                in_think_block = stream_result["in_think_block"]
                think_content = stream_result["think_content"]
                completion_tokens += stream_result["tokens_added"]
                for output_chunk in stream_result["chunks"]:
                    yield output_chunk

            finish_reason = "stop"

            if has_tools and full_content:
                _, _, parsed_tool_calls = (
                    QwenFnCallPromptProcessor.postprocess_qwen_response(
                        full_content
                    )
                )
                if parsed_tool_calls:
                    finish_reason = "tool_calls"
                    formatted_tool_calls = (
                        self._format_tool_calls_for_stream(
                            parsed_tool_calls
                        )
                    )
                    if formatted_tool_calls:
                        for tc in formatted_tool_calls:
                            tool_chunk_data = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [
                                                tc.model_dump()
                                            ]
                                        },
                                        "finish_reason": None,
                                        "logprobs": None,
                                    }
                                ],
                                "system_fingerprint": system_fingerprint,
                            }
                            yield f"data: {json.dumps(tool_chunk_data)}\n\n"

            if (
                request.max_tokens
                and completion_tokens >= request.max_tokens
            ):
                finish_reason = "length"
                logger.info(
                    f"模型 {model} 因为超过最大 max_token 限制，"
                    f"可能仅输出部分内容，可视情况调整"
                )

            if real_usage:
                usage = ChatCompletionUsage(
                    prompt_tokens=real_usage.get(
                        "input_tokens", len(prompt) // 3
                    ),
                    completion_tokens=real_usage.get(
                        "output_tokens", completion_tokens
                    ),
                    total_tokens=real_usage.get(
                        "total_tokens",
                        len(prompt) // 3 + completion_tokens,
                    ),
                )
            else:
                usage = ChatCompletionUsage(
                    prompt_tokens=len(prompt) // 3,
                    completion_tokens=completion_tokens,
                    total_tokens=len(prompt) // 3 + completion_tokens,
                )

            final_chunk = ChatCompletionChunk(
                id=completion_id,
                created=created_time,
                model=model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionChunkDelta(),
                        finish_reason=finish_reason,
                    )
                ],
                usage=usage,
                system_fingerprint=system_fingerprint,
            )
            yield f"data: {final_chunk.model_dump_json()}\n\n"

            if audio_path and os.path.exists(audio_path):
                audio_info = {
                    "audio_url": (
                        f"/v1/audio/files/{os.path.basename(audio_path)}"
                    )
                }
                yield f"data: {json.dumps(audio_info)}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"流式聊天错误: {e}")
            error_chunk = {
                "error": {
                    "message": str(e),
                    "type": "server_error",
                    "code": "stream_error",
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    def _process_stream_chunk(
        self,
        chunk: str,
        in_think_block: bool,
        think_content: str,
        completion_id: str,
        created_time: int,
        model: str,
        system_fingerprint: str,
    ) -> Dict[str, Any]:
        """处理流式块"""
        result: Dict[str, Any] = {
            "in_think_block": in_think_block,
            "think_content": think_content,
            "tokens_added": 0,
            "chunks": [],
        }

        def create_chunk(
            content: Optional[str] = None,
            reasoning_content: Optional[str] = None,
        ) -> str:
            chunk_obj = ChatCompletionChunk(
                id=completion_id,
                created=created_time,
                model=model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionChunkDelta(
                            content=content,
                            reasoning_content=reasoning_content,
                        ),
                        finish_reason=None,
                    )
                ],
                system_fingerprint=system_fingerprint,
            )
            return f"data: {chunk_obj.model_dump_json()}\n\n"

        # 处理思考块开始
        if "<think>" in chunk and not in_think_block:
            result["in_think_block"] = True
            before_think = chunk.split("<think>")[0]
            if before_think:
                result["chunks"].append(create_chunk(content=before_think))

            after_think = (
                chunk.split("<think>")[1]
                if len(chunk.split("<think>")) > 1
                else ""
            )

            if "</think>" in after_think:
                think_part = after_think.split("</think>")[0]
                after_close = (
                    after_think.split("</think>")[1]
                    if len(after_think.split("</think>")) > 1
                    else ""
                )
                result["in_think_block"] = False

                if think_part:
                    result["chunks"].append(
                        create_chunk(reasoning_content=think_part)
                    )

                if after_close:
                    result["chunks"].append(
                        create_chunk(content=after_close)
                    )
            else:
                result["think_content"] = after_think
            return result

        # 处理思考块中间
        if in_think_block:
            if "</think>" in chunk:
                think_part = chunk.split("</think>")[0]
                after_close = (
                    chunk.split("</think>")[1]
                    if len(chunk.split("</think>")) > 1
                    else ""
                )
                result["in_think_block"] = False

                full_think = think_content + think_part
                if full_think:
                    result["chunks"].append(
                        create_chunk(reasoning_content=full_think)
                    )
                result["think_content"] = ""

                if after_close:
                    result["chunks"].append(
                        create_chunk(content=after_close)
                    )
            else:
                result["think_content"] = think_content + chunk
            return result

        # 普通内容
        result["tokens_added"] = len(chunk) // 3
        result["chunks"].append(create_chunk(content=chunk))
        return result

    async def anthropic_messages(
        self,
        request: AnthropicMessagesRequest,
    ) -> AnthropicMessagesResponse:
        """Anthropic 消息处理 - 使用 Qwen fncall 格式"""
        message_id = IDGenerator.generate_anthropic_message_id()
        model = self.model_validator.validate_anthropic_model(request.model)

        prompt = self.message_converter.anthropic_to_prompt(
            messages=request.messages,
            system=request.system,
            tools=request.tools,
        )

        extracted_files = (
            self.file_extractor.extract_files_from_anthropic_messages(
                request.messages
            )
        )

        self._log_debug(
            f"处理 Anthropic 非流式请求: model={model}, "
            f"files={len(extracted_files)}, has_tools={bool(request.tools)}"
        )

        try:
            result = await self.client.chat_completion(
                message=prompt,
                model=model,
                extracted_files=(
                    extracted_files if extracted_files else None
                ),
                voice=False,
            )

            if isinstance(result, dict):
                raw_content = result.get("text", "")
            else:
                raw_content = str(result)

            if request.tools:
                stop_reason, content_blocks = (
                    QwenFnCallPromptProcessor.postprocess_qwen_response_to_anthropic(
                        raw_content
                    )
                )
            else:
                _, formal_content = (
                    self.reasoning_processor.extract_reasoning(raw_content)
                )
                if formal_content is None:
                    formal_content = raw_content
                content_blocks = [AnthropicTextBlock(text=formal_content)]
                stop_reason = AnthropicStopReason.END_TURN.value

            input_tokens = len(prompt) // 3
            output_tokens = len(raw_content) // 3

            return AnthropicMessagesResponse(
                id=message_id,
                content=content_blocks,
                model=request.model,
                stop_reason=stop_reason,
                stop_sequence=None,
                usage=AnthropicUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
            )

        except Exception as e:
            logger.error(f"Anthropic Messages 错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=AnthropicErrorResponse(
                    error=AnthropicErrorDetail(
                        type="api_error",
                        message=str(e),
                    )
                ).model_dump(),
            )

    async def anthropic_messages_stream(
        self,
        request: AnthropicMessagesRequest,
        disconnect_event: Optional[Event] = None,
    ) -> AsyncGenerator[str, None]:
        """Anthropic 消息流处理 - 使用 Qwen fncall 格式"""
        message_id = IDGenerator.generate_anthropic_message_id()
        model = self.model_validator.validate_anthropic_model(request.model)

        prompt = self.message_converter.anthropic_to_prompt(
            messages=request.messages,
            system=request.system,
            tools=request.tools,
        )

        extracted_files = (
            self.file_extractor.extract_files_from_anthropic_messages(
                request.messages
            )
        )

        self._log_debug(
            f"处理 Anthropic 流式请求: model={model}, "
            f"files={len(extracted_files)}, has_tools={bool(request.tools)}"
        )

        try:
            # 发送 message_start
            message_start = {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": request.model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": len(prompt) // 3,
                        "output_tokens": 0,
                    },
                },
            }
            yield (
                f"event: message_start\n"
                f"data: {json.dumps(message_start)}\n\n"
            )

            # 发送 content_block_start
            content_block_start = {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
            yield (
                f"event: content_block_start\n"
                f"data: {json.dumps(content_block_start)}\n\n"
            )

            full_content = ""
            output_tokens = 0
            has_tools = bool(request.tools)

            fncall_detected = False
            displayable_buffer = ""

            async for chunk in self.client.chat_stream(
                message=prompt,
                model=model,
                extracted_files=(
                    extracted_files if extracted_files else None
                ),
                voice=False,
            ):
                if disconnect_event and disconnect_event.is_set():
                    self._log_debug(
                        "检测到 Anthropic 客户端断开连接"
                    )
                    break

                if isinstance(chunk, dict):
                    continue

                if not chunk:
                    continue

                full_content += chunk

                if has_tools and not fncall_detected:
                    displayable_buffer += chunk

                    if QwenFnCallPromptProcessor.detect_fncall_in_stream(
                        displayable_buffer
                    ):
                        fncall_detected = True
                        display_text, _ = (
                            QwenFnCallPromptProcessor.split_stream_content(
                                displayable_buffer
                            )
                        )

                        filtered_text = self._filter_think_tags(
                            display_text
                        )
                        if filtered_text.strip():
                            output_tokens += len(filtered_text) // 3
                            delta_event = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {
                                    "type": "text_delta",
                                    "text": filtered_text,
                                },
                            }
                            yield (
                                f"event: content_block_delta\n"
                                f"data: {json.dumps(delta_event)}\n\n"
                            )

                        displayable_buffer = ""
                    else:
                        safe_text = ""
                        has_partial = False
                        for marker in [
                            FN_NAME,
                            FN_ARGS,
                            FN_RESULT,
                            FN_EXIT,
                        ]:
                            for i in range(1, len(marker)):
                                prefix = marker[:i]
                                if displayable_buffer.endswith(prefix):
                                    safe_text = displayable_buffer[
                                        : -len(prefix)
                                    ]
                                    displayable_buffer = prefix
                                    has_partial = True
                                    break
                            if has_partial:
                                break

                        if not has_partial:
                            safe_text = displayable_buffer
                            displayable_buffer = ""

                        filtered_text = self._filter_think_tags(safe_text)
                        if filtered_text:
                            output_tokens += len(filtered_text) // 3
                            delta_event = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {
                                    "type": "text_delta",
                                    "text": filtered_text,
                                },
                            }
                            yield (
                                f"event: content_block_delta\n"
                                f"data: {json.dumps(delta_event)}\n\n"
                            )
                elif has_tools and fncall_detected:
                    pass
                else:
                    filtered_text = self._filter_think_tags(chunk)
                    if filtered_text:
                        output_tokens += len(filtered_text) // 3
                        delta_event = {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "text_delta",
                                "text": filtered_text,
                            },
                        }
                        yield (
                            f"event: content_block_delta\n"
                            f"data: {json.dumps(delta_event)}\n\n"
                        )

            # 输出剩余的 displayable_buffer
            if displayable_buffer and not fncall_detected:
                filtered_text = self._filter_think_tags(
                    displayable_buffer
                )
                if filtered_text:
                    output_tokens += len(filtered_text) // 3
                    delta_event = {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": filtered_text,
                        },
                    }
                    yield (
                        f"event: content_block_delta\n"
                        f"data: {json.dumps(delta_event)}\n\n"
                    )

            # 结束文本内容块
            content_block_stop = {
                "type": "content_block_stop",
                "index": 0,
            }
            yield (
                f"event: content_block_stop\n"
                f"data: {json.dumps(content_block_stop)}\n\n"
            )

            stop_reason = "end_turn"

            if has_tools and full_content:
                _, _, parsed_tool_calls = (
                    QwenFnCallPromptProcessor.postprocess_qwen_response(
                        full_content
                    )
                )
                if parsed_tool_calls:
                    stop_reason = "tool_use"

                    for i, tc in enumerate(parsed_tool_calls, start=1):
                        try:
                            args = tc.function.arguments
                            if isinstance(args, str):
                                try:
                                    args_dict = json.loads(args)
                                except json.JSONDecodeError:
                                    args_dict = {"raw": args}
                            else:
                                args_dict = (
                                    args
                                    if isinstance(args, dict)
                                    else {"value": args}
                                )

                            tool_id = (
                                f"toolu_{uuid.uuid4().hex[:24]}"
                            )

                            tool_start = {
                                "type": "content_block_start",
                                "index": i,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": tool_id,
                                    "name": tc.function.name,
                                    "input": {},
                                },
                            }
                            yield (
                                f"event: content_block_start\n"
                                f"data: {json.dumps(tool_start)}\n\n"
                            )

                            input_json = json.dumps(
                                args_dict, ensure_ascii=False
                            )
                            tool_delta = {
                                "type": "content_block_delta",
                                "index": i,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": input_json,
                                },
                            }
                            yield (
                                f"event: content_block_delta\n"
                                f"data: {json.dumps(tool_delta)}\n\n"
                            )

                            tool_stop = {
                                "type": "content_block_stop",
                                "index": i,
                            }
                            yield (
                                f"event: content_block_stop\n"
                                f"data: {json.dumps(tool_stop)}\n\n"
                            )

                        except Exception as e:
                            logger.warning(
                                f"流式 Anthropic 工具调用处理失败: {e}"
                            )
                            continue

            # message_delta
            message_delta = {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": output_tokens},
            }
            yield (
                f"event: message_delta\n"
                f"data: {json.dumps(message_delta)}\n\n"
            )

            # message_stop
            message_stop = {"type": "message_stop"}
            yield (
                f"event: message_stop\n"
                f"data: {json.dumps(message_stop)}\n\n"
            )

        except Exception as e:
            logger.error(f"Anthropic 流式错误: {e}")
            error_event = {
                "type": "error",
                "error": {"type": "api_error", "message": str(e)},
            }
            yield f"event: error\ndata: {json.dumps(error_event)}\n\n"

    def _filter_think_tags(self, text: str) -> str:
        r"""过滤 <think>/<\/think> 标签，保留其他内容"""
        if not text:
            return text
        result = re.sub(
            r"<think>.*?</think>", "", text, flags=re.DOTALL
        )
        result = result.replace("<think>", "").replace("</think>", "")
        return result

    async def anthropic_count_tokens(
        self,
        request: AnthropicCountTokensRequest,
    ) -> AnthropicCountTokensResponse:
        """Anthropic Token 计数"""
        model = self.model_validator.validate_anthropic_model(request.model)

        prompt = self.message_converter.anthropic_to_prompt(
            messages=request.messages,
            system=request.system,
            tools=request.tools,
        )

        self._log_debug(
            f"计算 token 数: model={model}, "
            f"prompt_length={len(prompt)}"
        )

        try:
            token_result = await self.client.count_tokens(
                message=prompt,
                model=model,
            )

            return AnthropicCountTokensResponse(
                input_tokens=token_result.input_tokens
            )

        except Exception as e:
            logger.error(f"Token 计数错误: {e}")
            estimated_tokens = len(prompt) // 3
            return AnthropicCountTokensResponse(
                input_tokens=estimated_tokens
            )

    async def generate_image(
        self,
        request: ImageGenerationRequest,
    ) -> ImageGenerationResponse:
        """生成图像"""
        created_time = int(time.time())
        model = self.model_validator.validate_image_model(request.model)

        width = request.width
        height = request.height

        if request.size:
            try:
                size_parts = request.size.lower().split("x")
                if len(size_parts) == 2:
                    width = int(size_parts[0])
                    height = int(size_parts[1])
            except (ValueError, IndexError):
                self._log_debug(
                    f"无法解析尺寸 {request.size}，使用默认值"
                )

        self._log_debug(
            f"生成图像: prompt={request.prompt[:50]}..., "
            f"model={model}, size={width}x{height}, n={request.n}"
        )

        try:
            results = await self.client.generate_image(
                prompt=request.prompt,
                n=request.n,
                width=width,
                height=height,
                seed=request.seed,
                model=model,
                nologo=request.nologo,
                response_format=request.response_format,
            )

            image_data_list: List[ImageData] = []
            for result in results:
                image_data = ImageData(
                    url=result.get("url"),
                    b64_json=result.get("b64_json"),
                    revised_prompt=result.get("revised_prompt"),
                )
                image_data_list.append(image_data)

            return ImageGenerationResponse(
                created=created_time,
                data=image_data_list,
            )

        except Exception as e:
            logger.error(f"图像生成错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="image_generation_error",
                    )
                ).model_dump(),
            )

    async def generate_video(
        self,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResponse:
        """生成视频"""
        created_time = int(time.time())

        self._log_debug(
            f"生成视频: prompt={request.prompt[:50]}..., "
            f"size={request.size}, seconds={request.seconds}"
        )

        try:
            result: VideoGenerationResult = await self.client.text_to_video(
                prompt=request.prompt,
                size=request.size,
                model="qwen3-max-2026-01-23",
                download=request.download,
            )

            if result.success:
                return VideoGenerationResponse(
                    id=(
                        result.message_id
                        or f"video_{uuid.uuid4().hex[:12]}"
                    ),
                    status="completed",
                    progress=100,
                    created_at=created_time,
                    video_url=result.video_url,
                )
            else:
                return VideoGenerationResponse(
                    id=f"video_{uuid.uuid4().hex[:12]}",
                    status="failed",
                    progress=0,
                    created_at=created_time,
                    error=result.error,
                )

        except Exception as e:
            logger.error(f"视频生成错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="video_generation_error",
                    )
                ).model_dump(),
            )

    async def create_embedding(
        self,
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        """创建嵌入向量"""
        self._log_debug(f"创建嵌入向量: model={request.model}")

        try:
            if isinstance(request.input, str):
                texts = [request.input]
            else:
                texts = request.input

            embeddings = await self.client.get_embeddings_batch(texts)

            data: List[EmbeddingData] = []
            total_tokens = 0
            for i, (text, embedding) in enumerate(
                zip(texts, embeddings)
            ):
                data.append(EmbeddingData(embedding=embedding, index=i))
                total_tokens += len(text) // 3

            return EmbeddingResponse(
                data=data,
                model=request.model,
                usage=EmbeddingUsage(
                    prompt_tokens=total_tokens,
                    total_tokens=total_tokens,
                ),
            )

        except Exception as e:
            logger.error(f"嵌入向量错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="embedding_error",
                    )
                ).model_dump(),
            )

    async def deep_research(
        self,
        request: DeepResearchRequest,
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """深度研究"""
        self._log_debug(
            f"深度研究: topic={request.topic[:50]}..., "
            f"mode={request.research_mode}"
        )

        if request.stream:
            return self._deep_research_stream(request)
        else:
            return await self._deep_research_completion(request)

    async def _deep_research_completion(
        self,
        request: DeepResearchRequest,
    ) -> Dict[str, Any]:
        """深度研究（非流式）"""
        try:
            result = await self.client.deep_research_completion(
                topic=request.topic,
                model=request.model,
                research_mode=request.research_mode,
            )

            if isinstance(result, dict):
                return {
                    "id": f"research_{uuid.uuid4().hex[:12]}",
                    "object": "deep_research",
                    "created_at": int(time.time()),
                    "status": "completed",
                    "content": result.get("text", ""),
                    "deep_research": result.get(
                        "deep_research", []
                    ),
                }
            else:
                return {
                    "id": f"research_{uuid.uuid4().hex[:12]}",
                    "object": "deep_research",
                    "created_at": int(time.time()),
                    "status": "completed",
                    "content": str(result),
                }

        except Exception as e:
            logger.error(f"深度研究错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="deep_research_error",
                    )
                ).model_dump(),
            )

    async def _deep_research_stream(
        self,
        request: DeepResearchRequest,
    ) -> AsyncGenerator[str, None]:
        """深度研究（流式）"""
        try:
            async for chunk in self.client.deep_research(
                topic=request.topic,
                model=request.model,
                research_mode=request.research_mode,
            ):
                if isinstance(chunk, dict):
                    yield f"data: {json.dumps(chunk)}\n\n"
                else:
                    yield (
                        f'data: {json.dumps({"content": chunk})}\n\n'
                    )

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"深度研究流式错误: {e}")
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
            yield "data: [DONE]\n\n"

    async def image_edit(
        self,
        request: ImageEditRequest,
    ) -> Dict[str, Any]:
        """图像编辑"""
        self._log_debug(
            f"图像编辑: prompt={request.prompt[:50]}..., "
            f"images={len(request.image_urls)}"
        )

        try:
            result = await self.client.image_edit(
                prompt=request.prompt,
                image_urls=request.image_urls,
                model=request.model,
            )

            return {
                "id": f"img_edit_{uuid.uuid4().hex[:12]}",
                "object": "image_edit",
                "created_at": int(time.time()),
                "text": result.get("text", ""),
                "images": [
                    {
                        "url": img.image_url,
                        "width": img.width,
                        "height": img.height,
                    }
                    for img in result.get("images", [])
                ],
            }

        except Exception as e:
            logger.error(f"图像编辑错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="image_edit_error",
                    )
                ).model_dump(),
            )

    async def learn_chat(
        self,
        request: LearnChatRequest,
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """学习辅导对话"""
        self._log_debug(
            f"学习辅导: question={request.question[:50]}..."
        )

        if request.stream:
            return self._learn_chat_stream(request)
        else:
            return await self._learn_chat_completion(request)

    async def _learn_chat_completion(
        self,
        request: LearnChatRequest,
    ) -> Dict[str, Any]:
        """学习辅导（非流式）"""
        try:
            result = await self.client.learn_chat_completion(
                question=request.question,
                model=request.model,
            )

            thinking_summary = None
            if (
                isinstance(result, dict)
                and "thinking_summary" in result
            ):
                ts = result["thinking_summary"]
                thinking_summary = {
                    "title": (
                        ts.title if hasattr(ts, "title") else []
                    ),
                    "thought": (
                        ts.thought if hasattr(ts, "thought") else []
                    ),
                }

            return {
                "id": f"learn_{uuid.uuid4().hex[:12]}",
                "object": "learn_chat",
                "created_at": int(time.time()),
                "content": (
                    result.get("text", "")
                    if isinstance(result, dict)
                    else str(result)
                ),
                "thinking_summary": thinking_summary,
            }

        except Exception as e:
            logger.error(f"学习辅导错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        message=str(e),
                        type="server_error",
                        code="learn_chat_error",
                    )
                ).model_dump(),
            )

    async def _learn_chat_stream(
        self,
        request: LearnChatRequest,
    ) -> AsyncGenerator[str, None]:
        """学习辅导（流式）"""
        try:
            async for chunk in self.client.learn_chat(
                question=request.question,
                model=request.model,
            ):
                if isinstance(chunk, dict):
                    yield f"data: {json.dumps(chunk)}\n\n"
                else:
                    yield (
                        f'data: {json.dumps({"content": chunk})}\n\n'
                    )

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"学习辅导流式错误: {e}")
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
            yield "data: [DONE]\n\n"

    async def artifacts_web_dev(
        self,
        request: ArtifactsRequest,
    ) -> AsyncGenerator[str, None]:
        """Artifacts/Web 开发"""
        self._log_debug(
            f"Artifacts: requirement={request.requirement[:50]}..."
        )

        try:
            async for chunk in self.client.artifacts_web_dev(
                requirement=request.requirement,
                model=request.model,
            ):
                if isinstance(chunk, dict):
                    yield f"data: {json.dumps(chunk)}\n\n"
                else:
                    yield (
                        f'data: {json.dumps({"content": chunk})}\n\n'
                    )

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Artifacts 错误: {e}")
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
            yield "data: [DONE]\n\n"

    async def text_to_speech(
        self, request: TTSRequest
    ) -> Optional[str]:
        """文字转语音"""
        self._log_debug(
            f"处理 TTS 请求: input_length={len(request.input)}"
        )

        try:
            audio_path = await self.client.chat_with_tts(
                text=request.input,
                model=ServerConfig.DEFAULT_MODEL,
            )
            return audio_path
        except Exception as e:
            logger.error(f"TTS 错误: {e}")
            return None

    def list_models(self) -> ModelsResponse:
        """列出模型"""
        created_time = int(time.time())

        models: List[ModelInfo] = [
            ModelInfo(
                id=model_id,
                created=created_time,
                owned_by="qwen",
                permission=[
                    {
                        "id": f"modelperm-{uuid.uuid4().hex[:24]}",
                        "object": "model_permission",
                        "created": created_time,
                        "allow_create_engine": False,
                        "allow_sampling": True,
                        "allow_logprobs": False,
                        "allow_search_indices": False,
                        "allow_view": True,
                        "allow_fine_tuning": False,
                        "organization": "*",
                        "group": None,
                        "is_blocking": False,
                    }
                ],
                root=model_id,
                parent=None,
            )
            for model_id in self.available_models
        ]

        for image_model in self.available_image_models:
            models.append(
                ModelInfo(
                    id=image_model,
                    created=created_time,
                    owned_by="pollinations",
                    root=image_model,
                    parent=None,
                )
            )

        special_models = [
            "tts-1",
            "whisper-1",
            "sora-2",
            "qwen3-embedding:0.6b",
        ]
        for special_model in special_models:
            models.append(
                ModelInfo(
                    id=special_model,
                    created=created_time,
                    owned_by="qwen",
                    root=special_model,
                    parent=None,
                )
            )

        return ModelsResponse(data=models)

    def list_anthropic_models(self) -> AnthropicModelsResponse:
        """列出 Anthropic 模型"""
        created_at = datetime.now(timezone.utc).isoformat()

        models: List[AnthropicModelInfo] = []
        for anthropic_model in ServerConfig.ANTHROPIC_MODEL_MAPPING.keys():
            models.append(
                AnthropicModelInfo(
                    id=anthropic_model,
                    display_name=anthropic_model.replace("-", " ").title(),
                    created_at=created_at,
                )
            )

        for qwen_model in self.available_models:
            models.append(
                AnthropicModelInfo(
                    id=qwen_model,
                    display_name=qwen_model,
                    created_at=created_at,
                )
            )

        return AnthropicModelsResponse(
            data=models,
            has_more=False,
            first_id=models[0].id if models else None,
            last_id=models[-1].id if models else None,
        )

    async def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        try:
            account_status = await self.client.get_account_status()
            return {
                "status": "running",
                "initialized": self._initialized,
                "available_models": self.available_models,
                "available_image_models": self.available_image_models,
                "anthropic_models": list(
                    ServerConfig.ANTHROPIC_MODEL_MAPPING.keys()
                ),
                "large_text_threshold": Config.LARGE_TEXT_THRESHOLD,
                "concurrent_generators": Config.CONCURRENT_GENERATORS,
                "fncall_format": "qwen_native",
                "fncall_markers": {
                    "function": FN_NAME,
                    "args": FN_ARGS,
                    "result": FN_RESULT,
                    "return": FN_EXIT,
                },
                "scheduler": self.get_scheduler_metrics(),
                "features": [
                    "chat_completions",
                    "streaming",
                    "tool_calls",
                    "reasoning_content",
                    "tts",
                    "base64_files",
                    "multimodal",
                    "image_generation",
                    "video_generation",
                    "embeddings",
                    "deep_research",
                    "image_edit",
                    "learn_chat",
                    "artifacts",
                    "anthropic_messages",
                    "anthropic_count_tokens",
                    "anthropic_batches",
                    "anthropic_files",
                    "large_text_auto_convert",
                    "concurrent_racing",
                    "chat_id_pool",
                    "file_cache",
                    "qwen_fncall_prompt",
                    "fair_request_scheduling",
                    "fifo_priority_queue",
                    "concurrent_rate_limiting",
                ],
                "accounts": account_status,
            }
        except Exception as e:
            logger.error(f"获取状态错误: {e}")
            return {"status": "error", "error": str(e)}


# ====================
# FastAPI 应用
# ====================


server = QwenAPIServer(debug=ServerConfig.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("正在启动 Qwen API 服务器...")
    await server.initialize()
    yield
    logger.info("正在关闭 Qwen API 服务器...")
    await server.shutdown()


app = FastAPI(
    title="Qwen API Server",
    description=(
        "兼容 OpenAI API 和 Anthropic API 格式的 Qwen AI 服务 "
        "(Qwen FnCall 格式 + FIFO 公平调度)"
    ),
    version="6.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GZip 压缩 - 减少带宽消耗，提高流式传输效率
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ====================
# 请求追踪中间件
# ====================


@app.middleware("http")
async def request_tracking_middleware(
    request: Request, call_next
):
    """请求追踪中间件 - 记录全局指标"""
    endpoint = f"{request.method} {request.url.path}"
    start_time = await server.metrics.record_request_start(endpoint)

    try:
        response = await call_next(request)
        await server.metrics.record_request_end(
            start_time, response.status_code
        )
        # 注入请求 ID 到响应头
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        response.headers["X-Request-Id"] = request_id
        return response
    except Exception as e:
        await server.metrics.record_request_end(start_time, 500)
        raise


# ====================
# 依赖项
# ====================


def get_anthropic_version(
    anthropic_version: Optional[str] = Header(
        None, alias="anthropic-version"
    ),
) -> Optional[str]:
    """获取 Anthropic 版本头"""
    return anthropic_version


def get_anthropic_beta(
    anthropic_beta: Optional[str] = Header(
        None, alias="anthropic-beta"
    ),
) -> Optional[str]:
    """获取 Anthropic Beta 头"""
    return anthropic_beta


# ====================
# 基础路由
# ====================


@app.get("/")
async def root():
    """根路由"""
    return {
        "message": "Qwen API Server",
        "version": "6.0.0",
        "docs": "/docs",
        "openai_compatible": True,
        "anthropic_compatible": True,
        "fncall_format": "qwen_native",
        "scheduling": "fifo_fair_queue",
        "features": [
            "chat_completions",
            "streaming",
            "tool_calls",
            "reasoning_content",
            "tts",
            "base64_files",
            "multimodal",
            "image_generation",
            "video_generation",
            "embeddings",
            "deep_research",
            "image_edit",
            "learn_chat",
            "artifacts",
            "anthropic_messages",
            "anthropic_count_tokens",
            "anthropic_batches",
            "anthropic_files",
            "large_text_auto_convert",
            "concurrent_racing",
            "chat_id_pool",
            "file_cache",
            "qwen_fncall_prompt",
            "fair_request_scheduling",
            "fifo_priority_queue",
            "concurrent_rate_limiting",
        ],
        "large_text_threshold": Config.LARGE_TEXT_THRESHOLD,
        "concurrent_generators": Config.CONCURRENT_GENERATORS,
    }


@app.get("/health")
async def health_check():
    """健康检查 - 包含调度器状态"""
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "schedulers": {
            "chat": {
                "active": server.chat_scheduler.active_count,
                "queued": server.chat_scheduler.queue_depth,
            },
            "media": {
                "active": server.media_scheduler.active_count,
                "queued": server.media_scheduler.queue_depth,
            },
            "auxiliary": {
                "active": server.aux_scheduler.active_count,
                "queued": server.aux_scheduler.queue_depth,
            },
        },
    }

@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    """列出模型"""
    return server.list_models()


@app.get("/v1/models/{model_id}", response_model=ModelInfo)
async def get_model(model_id: str):
    """获取模型信息"""
    all_models = (
        server.available_models
        + server.available_image_models
        + ["tts-1", "whisper-1", "sora-2", "qwen3-embedding:0.6b"]
    )

    if model_id not in all_models:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Model {model_id} not found",
                    type="invalid_request_error",
                    code="model_not_found",
                )
            ).model_dump(),
        )

    owned_by = "qwen"
    if model_id in server.available_image_models:
        owned_by = "pollinations"

    return ModelInfo(
        id=model_id,
        created=int(time.time()),
        owned_by=owned_by,
        root=model_id,
        parent=None,
    )


# ====================
# 聊天补全路由
# ====================


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    raw_request: Request,
    authorization: Optional[str] = Header(None),
):
    """聊天补全 - 通过 FIFO 公平调度器管理并发"""
    if not request.messages:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message="messages is required",
                    type="invalid_request_error",
                    param="messages",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"chat_{uuid.uuid4().hex[:16]}"

    try:
        if request.stream:
            # 流式：先获取槽位，再创建 StreamingResponse
            # 槽位在生成器结束时释放
            try:
                slot = await server.chat_scheduler.acquire_slot(
                    request_id
                )
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)

            if slot.wait_duration > 0.1:
                logger.info(
                    f"请求 {request_id} 等待 "
                    f"{slot.wait_duration:.2f}s 后获得处理槽位"
                )

            disconnect_event = Event()

            async def scheduled_stream():
                """调度包装的流式生成器"""
                try:
                    async for chunk in server.chat_completion_stream(
                        request, disconnect_event
                    ):
                        if await raw_request.is_disconnected():
                            disconnect_event.set()
                            break
                        yield chunk
                except asyncio.CancelledError:
                    disconnect_event.set()
                    raise
                finally:
                    await slot.release()

            return StreamingResponse(
                scheduled_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "Content-Type": (
                        "text/event-stream; charset=utf-8"
                    ),
                    "X-Queue-Wait-Ms": str(
                        int(slot.wait_duration * 1000)
                    ),
                },
            )
        else:
            # 非流式：使用上下文管理器
            try:
                async with server.chat_scheduler.acquire(
                    request_id
                ) as slot:
                    if slot.wait_duration > 0.1:
                        logger.info(
                            f"请求 {request_id} 等待 "
                            f"{slot.wait_duration:.2f}s 后获得处理槽位"
                        )
                    response = await server.chat_completion(request)
                    return JSONResponse(
                        content=response.model_dump(
                            exclude_none=True
                        ),
                        headers={
                            "Content-Type": "application/json",
                            "X-Queue-Wait-Ms": str(
                                int(slot.wait_duration * 1000)
                            ),
                        },
                    )
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"聊天补全异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Internal server error: {str(e)}",
                    type="server_error",
                    code="internal_error",
                )
            ).model_dump(),
        )


# ====================
# Responses API 路由
# ====================


@app.post("/v1/responses")
async def create_response(
    request: ResponsesRequest,
    authorization: Optional[str] = Header(None),
):
    """Responses API - 通过聊天调度器管理"""
    request_id = f"resp_{uuid.uuid4().hex[:16]}"

    try:
        messages: List[ChatMessage] = []

        if request.instructions:
            messages.append(
                ChatMessage(
                    role="system", content=request.instructions
                )
            )

        if isinstance(request.input, str):
            messages.append(
                ChatMessage(role="user", content=request.input)
            )
        elif isinstance(request.input, list):
            for item in request.input:
                if isinstance(item, dict):
                    role = item.get("role", "user")
                    content = item.get("content", "")

                    if isinstance(content, list):
                        content_parts: List[ContentPart] = []
                        for part in content:
                            if isinstance(part, dict):
                                part_type = part.get("type", "text")
                                if part_type == "input_text":
                                    content_parts.append(
                                        ContentPart(
                                            type="text",
                                            text=part.get(
                                                "text", ""
                                            ),
                                        )
                                    )
                                elif part_type == "text":
                                    content_parts.append(
                                        ContentPart(
                                            type="text",
                                            text=part.get(
                                                "text", ""
                                            ),
                                        )
                                    )
                                elif part_type == "input_image":
                                    image_url = part.get(
                                        "image_url",
                                        part.get("url", ""),
                                    )
                                    content_parts.append(
                                        ContentPart(
                                            type="image_url",
                                            image_url=ImageURL(
                                                url=image_url
                                            ),
                                        )
                                    )
                        messages.append(
                            ChatMessage(
                                role=role, content=content_parts
                            )
                        )
                    else:
                        messages.append(
                            ChatMessage(
                                role=role, content=str(content)
                            )
                        )

        tools: Optional[List[ToolDefinition]] = None
        if request.tools:
            tools = []
            for tool in request.tools:
                if tool.get("type") == "function":
                    func = tool.get("function", tool)
                    tools.append(
                        ToolDefinition(
                            type="function",
                            function=FunctionDefinition(
                                name=func.get("name", ""),
                                description=func.get("description"),
                                parameters=func.get("parameters"),
                            ),
                        )
                    )

        chat_request = ChatCompletionRequest(
            model=request.model,
            messages=messages,
            max_tokens=request.max_output_tokens,
            temperature=request.temperature,
            tools=tools,
            stream=request.stream,
        )

        if request.stream:
            try:
                slot = await server.chat_scheduler.acquire_slot(
                    request_id
                )
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)

            async def scheduled_stream():
                try:
                    async for chunk in server.chat_completion_stream(
                        chat_request
                    ):
                        yield chunk
                finally:
                    await slot.release()

            return StreamingResponse(
                scheduled_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            try:
                async with server.chat_scheduler.acquire(
                    request_id
                ) as slot:
                    response = await server.chat_completion(
                        chat_request
                    )
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)

            output_content = ""
            if response.choices and response.choices[0].message:
                output_content = (
                    response.choices[0].message.content or ""
                )

            return JSONResponse(
                content={
                    "id": response.id.replace(
                        "chatcmpl-", "resp_"
                    ),
                    "object": "response",
                    "created_at": response.created,
                    "status": "completed",
                    "model": response.model,
                    "output": [
                        {
                            "type": "message",
                            "id": f"msg_{uuid.uuid4().hex[:24]}",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": output_content,
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Responses API 异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Internal server error: {str(e)}",
                    type="server_error",
                    code="internal_error",
                )
            ).model_dump(),
        )


# ====================
# 图像生成路由
# ====================


@app.post(
    "/v1/images/generations",
    response_model=ImageGenerationResponse,
)
async def create_image(
    request: ImageGenerationRequest,
    authorization: Optional[str] = Header(None),
):
    """图像生成 - 通过媒体调度器管理"""
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "prompt is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="prompt",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"img_{uuid.uuid4().hex[:16]}"

    try:
        async with server.media_scheduler.acquire(
            request_id
        ) as slot:
            if slot.wait_duration > 0.1:
                logger.info(
                    f"图像请求 {request_id} 等待 "
                    f"{slot.wait_duration:.2f}s"
                )
            response = await server.generate_image(request)
            return response
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图像生成异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Image generation error: {str(e)}",
                    type="server_error",
                    code="image_generation_error",
                )
            ).model_dump(),
        )


# ====================
# 视频生成路由
# ====================


@app.post("/v1/videos", response_model=VideoGenerationResponse)
async def create_video(
    request: VideoGenerationRequest,
    authorization: Optional[str] = Header(None),
):
    """视频生成 - 通过媒体调度器管理"""
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "prompt is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="prompt",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"vid_{uuid.uuid4().hex[:16]}"

    try:
        async with server.media_scheduler.acquire(
            request_id
        ) as slot:
            if slot.wait_duration > 0.1:
                logger.info(
                    f"视频请求 {request_id} 等待 "
                    f"{slot.wait_duration:.2f}s"
                )
            response = await server.generate_video(request)
            return response
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"视频生成异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Video generation error: {str(e)}",
                    type="server_error",
                    code="video_generation_error",
                )
            ).model_dump(),
        )


@app.get(
    "/v1/videos/{video_id}",
    response_model=VideoGenerationResponse,
)
async def get_video(video_id: str):
    """获取视频状态"""
    return VideoGenerationResponse(
        id=video_id,
        status="completed",
        progress=100,
        created_at=int(time.time()),
    )


@app.get("/v1/videos/{video_id}/content")
async def get_video_content(video_id: str):
    """获取视频内容"""
    video_dir = Path(Config.GENERATED_VIDEO_DIR)

    for video_file in video_dir.glob(f"*{video_id}*.mp4"):
        if video_file.exists():
            return FileResponse(
                path=str(video_file),
                media_type="video/mp4",
                filename=video_file.name,
            )

    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                message=f"Video {video_id} not found",
                type="invalid_request_error",
                code="video_not_found",
            )
        ).model_dump(),
    )


@app.delete("/v1/videos/{video_id}")
async def delete_video(video_id: str):
    """删除视频"""
    return {"id": video_id, "object": "video", "deleted": True}


# ====================
# 嵌入向量路由
# ====================


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embedding(
    request: EmbeddingRequest,
    authorization: Optional[str] = Header(None),
):
    """创建嵌入向量 - 通过辅助调度器管理"""
    if not request.input:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message="input is required",
                    type="invalid_request_error",
                    param="input",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"emb_{uuid.uuid4().hex[:16]}"

    try:
        async with server.aux_scheduler.acquire(
            request_id
        ) as slot:
            response = await server.create_embedding(request)
            return response
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"嵌入向量异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Embedding error: {str(e)}",
                    type="server_error",
                    code="embedding_error",
                )
            ).model_dump(),
        )


# ====================
# 深度研究路由
# ====================


@app.post("/v1/research")
async def create_deep_research(
    request: DeepResearchRequest,
    authorization: Optional[str] = Header(None),
):
    """深度研究 - 通过辅助调度器管理"""
    if not request.topic or not request.topic.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "topic is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="topic",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"res_{uuid.uuid4().hex[:16]}"

    try:
        if request.stream:
            try:
                slot = await server.aux_scheduler.acquire_slot(
                    request_id
                )
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)

            async def scheduled_stream():
                try:
                    async for chunk in server._deep_research_stream(
                        request
                    ):
                        yield chunk
                finally:
                    await slot.release()

            return StreamingResponse(
                scheduled_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            try:
                async with server.aux_scheduler.acquire(
                    request_id
                ) as slot:
                    response = (
                        await server._deep_research_completion(
                            request
                        )
                    )
                    return JSONResponse(content=response)
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"深度研究异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Deep research error: {str(e)}",
                    type="server_error",
                    code="deep_research_error",
                )
            ).model_dump(),
        )


# ====================
# 图像编辑路由
# ====================


@app.post("/v1/images/edits")
async def create_image_edit(
    request: ImageEditRequest,
    authorization: Optional[str] = Header(None),
):
    """图像编辑 - 通过媒体调度器管理"""
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "prompt is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="prompt",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    if not request.image_urls:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message="image_urls is required",
                    type="invalid_request_error",
                    param="image_urls",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"edit_{uuid.uuid4().hex[:16]}"

    try:
        async with server.media_scheduler.acquire(
            request_id
        ) as slot:
            response = await server.image_edit(request)
            return JSONResponse(content=response)
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图像编辑异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Image edit error: {str(e)}",
                    type="server_error",
                    code="image_edit_error",
                )
            ).model_dump(),
        )


# ====================
# 学习辅导路由
# ====================


@app.post("/v1/learn")
async def create_learn_chat(
    request: LearnChatRequest,
    authorization: Optional[str] = Header(None),
):
    """学习辅导对话 - 通过辅助调度器管理"""
    if not request.question or not request.question.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "question is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="question",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"lrn_{uuid.uuid4().hex[:16]}"

    try:
        if request.stream:
            try:
                slot = await server.aux_scheduler.acquire_slot(
                    request_id
                )
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)

            async def scheduled_stream():
                try:
                    async for chunk in server._learn_chat_stream(
                        request
                    ):
                        yield chunk
                finally:
                    await slot.release()

            return StreamingResponse(
                scheduled_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            try:
                async with server.aux_scheduler.acquire(
                    request_id
                ) as slot:
                    response = (
                        await server._learn_chat_completion(request)
                    )
                    return JSONResponse(content=response)
            except SchedulerError as exc:
                _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"学习辅导异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Learn chat error: {str(e)}",
                    type="server_error",
                    code="learn_chat_error",
                )
            ).model_dump(),
        )


# ====================
# Artifacts 路由
# ====================


@app.post("/v1/artifacts")
async def create_artifacts(
    request: ArtifactsRequest,
    authorization: Optional[str] = Header(None),
):
    """Artifacts/Web 开发 - 通过辅助调度器管理"""
    if not request.requirement or not request.requirement.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "requirement is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="requirement",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"art_{uuid.uuid4().hex[:16]}"

    try:
        try:
            slot = await server.aux_scheduler.acquire_slot(
                request_id
            )
        except SchedulerError as exc:
            _raise_scheduler_error_openai(exc)

        async def scheduled_stream():
            try:
                async for chunk in server.artifacts_web_dev(request):
                    yield chunk
            finally:
                await slot.release()

        return StreamingResponse(
            scheduled_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Artifacts 异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Artifacts error: {str(e)}",
                    type="server_error",
                    code="artifacts_error",
                )
            ).model_dump(),
        )


# ====================
# TTS 路由
# ====================


@app.post("/v1/audio/speech")
async def create_speech(request: TTSRequest):
    """TTS 生成 - 通过媒体调度器管理"""
    if not request.input or not request.input.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        "input is required and cannot be empty"
                    ),
                    type="invalid_request_error",
                    param="input",
                    code="missing_required_parameter",
                )
            ).model_dump(),
        )

    request_id = f"tts_{uuid.uuid4().hex[:16]}"

    try:
        async with server.media_scheduler.acquire(
            request_id
        ) as slot:
            audio_path = await server.text_to_speech(request)

            if audio_path is None or not os.path.exists(audio_path):
                raise HTTPException(
                    status_code=500,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            message="TTS generation failed",
                            type="server_error",
                            code="tts_failed",
                        )
                    ).model_dump(),
                )

            mime_types = {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "opus": "audio/opus",
                "aac": "audio/aac",
                "flac": "audio/flac",
            }
            media_type = mime_types.get(
                request.response_format, "audio/wav"
            )

            return FileResponse(
                path=audio_path,
                media_type=media_type,
                filename=f"speech.{request.response_format}",
            )
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS 异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"TTS error: {str(e)}",
                    type="server_error",
                    code="tts_error",
                )
            ).model_dump(),
        )


# ====================
# 音频转录路由
# ====================


@app.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),
    language: Optional[str] = Form(default=None),
    prompt: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0),
):
    """音频转录 - 通过媒体调度器管理"""
    request_id = f"asr_{uuid.uuid4().hex[:16]}"

    try:
        async with server.media_scheduler.acquire(
            request_id
        ) as slot:
            # 保存上传的音频文件
            audio_dir = Path("data/audio_uploads")
            audio_dir.mkdir(parents=True, exist_ok=True)

            timestamp = int(time.time() * 1000)
            unique_id = uuid.uuid4().hex[:8]
            ext = (
                Path(file.filename).suffix
                if file.filename
                else ".wav"
            )
            audio_path = (
                audio_dir / f"audio_{timestamp}_{unique_id}{ext}"
            )

            content = await file.read()
            with open(audio_path, "wb") as f:
                f.write(content)

            transcription_prompt = (
                "请转录以下音频内容，只输出转录文本，"
                "不要添加任何解释："
            )
            if prompt:
                transcription_prompt = (
                    f"{prompt}\n请转录以下音频内容："
                )

            result = await server.client.chat_completion(
                message=transcription_prompt,
                model="qwen3-omni-flash-2025-12-01",
                file_paths=[str(audio_path)],
            )

            if audio_path.exists():
                os.remove(audio_path)

            text = (
                result.get("text", "")
                if isinstance(result, dict)
                else str(result)
            )

            if response_format == "text":
                return text
            elif response_format in ["srt", "vtt"]:
                return (
                    f"1\n00:00:00,000 --> 00:00:10,000\n{text}\n"
                )
            else:
                return TranscriptionResponse(text=text)
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except Exception as e:
        logger.error(f"音频转录异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Transcription error: {str(e)}",
                    type="server_error",
                    code="transcription_error",
                )
            ).model_dump(),
        )


@app.post("/v1/audio/translations")
async def create_translation(
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),
    prompt: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0),
):
    """音频翻译为英文 - 通过媒体调度器管理"""
    request_id = f"trans_{uuid.uuid4().hex[:16]}"

    try:
        async with server.media_scheduler.acquire(
            request_id
        ) as slot:
            audio_dir = Path("data/audio_uploads")
            audio_dir.mkdir(parents=True, exist_ok=True)

            timestamp = int(time.time() * 1000)
            unique_id = uuid.uuid4().hex[:8]
            ext = (
                Path(file.filename).suffix
                if file.filename
                else ".wav"
            )
            audio_path = (
                audio_dir / f"audio_{timestamp}_{unique_id}{ext}"
            )

            content = await file.read()
            with open(audio_path, "wb") as f:
                f.write(content)

            translation_prompt = (
                "请将以下音频内容转录并翻译成英文，"
                "只输出英文翻译结果："
            )
            if prompt:
                translation_prompt = (
                    f"{prompt}\n请将以下音频内容翻译成英文："
                )

            result = await server.client.chat_completion(
                message=translation_prompt,
                model="qwen3-omni-flash-2025-12-01",
                file_paths=[str(audio_path)],
            )

            if audio_path.exists():
                os.remove(audio_path)

            text = (
                result.get("text", "")
                if isinstance(result, dict)
                else str(result)
            )

            if response_format == "text":
                return text
            else:
                return TranscriptionResponse(text=text)
    except SchedulerError as exc:
        _raise_scheduler_error_openai(exc)
    except Exception as e:
        logger.error(f"音频翻译异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Translation error: {str(e)}",
                    type="server_error",
                    code="translation_error",
                )
            ).model_dump(),
        )


# ====================
# Anthropic API 路由
# ====================


@app.post("/v1/messages")
async def anthropic_messages(
    request: AnthropicMessagesRequest,
    raw_request: Request,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """Anthropic 消息 - 通过聊天调度器管理"""
    if not request.messages:
        raise HTTPException(
            status_code=400,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="invalid_request_error",
                    message="messages is required",
                )
            ).model_dump(),
        )

    request_id = f"anth_{uuid.uuid4().hex[:16]}"

    try:
        if request.stream:
            try:
                slot = await server.chat_scheduler.acquire_slot(
                    request_id
                )
            except SchedulerError as exc:
                _raise_scheduler_error_anthropic(exc)

            if slot.wait_duration > 0.1:
                logger.info(
                    f"Anthropic 请求 {request_id} 等待 "
                    f"{slot.wait_duration:.2f}s"
                )

            disconnect_event = Event()

            async def scheduled_stream():
                try:
                    async for chunk in (
                        server.anthropic_messages_stream(
                            request, disconnect_event
                        )
                    ):
                        if await raw_request.is_disconnected():
                            disconnect_event.set()
                            break
                        yield chunk
                except asyncio.CancelledError:
                    disconnect_event.set()
                    raise
                finally:
                    await slot.release()

            return StreamingResponse(
                scheduled_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "Content-Type": (
                        "text/event-stream; charset=utf-8"
                    ),
                    "X-Queue-Wait-Ms": str(
                        int(slot.wait_duration * 1000)
                    ),
                },
            )
        else:
            try:
                async with server.chat_scheduler.acquire(
                    request_id
                ) as slot:
                    if slot.wait_duration > 0.1:
                        logger.info(
                            f"Anthropic 请求 {request_id} "
                            f"等待 {slot.wait_duration:.2f}s"
                        )
                    response = await server.anthropic_messages(
                        request
                    )
                    return JSONResponse(
                        content=response.model_dump(
                            exclude_none=True
                        ),
                        headers={
                            "Content-Type": "application/json",
                            "X-Queue-Wait-Ms": str(
                                int(slot.wait_duration * 1000)
                            ),
                        },
                    )
            except SchedulerError as exc:
                _raise_scheduler_error_anthropic(exc)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Anthropic Messages 异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="api_error",
                    message=f"Internal server error: {str(e)}",
                )
            ).model_dump(),
        )


@app.post(
    "/v1/messages/count_tokens",
    response_model=AnthropicCountTokensResponse,
)
async def anthropic_count_tokens(
    request: AnthropicCountTokensRequest,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """Anthropic Token 计数 - 通过辅助调度器管理"""
    if not request.messages:
        raise HTTPException(
            status_code=400,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="invalid_request_error",
                    message="messages is required",
                )
            ).model_dump(),
        )

    request_id = f"cnt_{uuid.uuid4().hex[:16]}"

    try:
        async with server.aux_scheduler.acquire(
            request_id
        ) as slot:
            return await server.anthropic_count_tokens(request)
    except SchedulerError as exc:
        _raise_scheduler_error_anthropic(exc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Count Tokens 异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="api_error",
                    message=f"Internal server error: {str(e)}",
                )
            ).model_dump(),
        )


# ====================
# Anthropic 模型路由
# ====================


@app.get(
    "/v1/models/anthropic",
    response_model=AnthropicModelsResponse,
)
async def list_anthropic_models(
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
):
    """列出 Anthropic 兼容模型"""
    return server.list_anthropic_models()


# ====================
# Anthropic 批处理路由
# ====================


@app.post(
    "/v1/messages/batches",
    response_model=AnthropicBatchResponse,
)
async def create_message_batch(
    request: AnthropicCreateBatchRequest,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """创建消息批处理"""
    if not request.requests:
        raise HTTPException(
            status_code=400,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="invalid_request_error",
                    message="requests is required",
                )
            ).model_dump(),
        )

    try:
        batch = await server.batch_store.create_batch(
            [r.model_dump() for r in request.requests]
        )
        asyncio.create_task(
            _process_batch(batch["id"], request.requests)
        )
        return AnthropicBatchResponse(**batch)
    except Exception as e:
        logger.error(f"创建批处理异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="api_error", message=str(e)
                )
            ).model_dump(),
        )


async def _process_batch(
    batch_id: str,
    requests: List[AnthropicBatchRequest],
) -> None:
    """处理批处理 - 每个子请求通过调度器管理"""
    for req in requests:
        request_id = f"batch_{batch_id}_{req.custom_id}"
        try:
            async with server.chat_scheduler.acquire(
                request_id, timeout=300.0
            ) as slot:
                messages_request = AnthropicMessagesRequest(
                    model=req.params.model,
                    messages=req.params.messages,
                    max_tokens=req.params.max_tokens,
                    system=req.params.system,
                    stop_sequences=req.params.stop_sequences,
                    temperature=req.params.temperature,
                    top_p=req.params.top_p,
                    top_k=req.params.top_k,
                    tools=req.params.tools,
                    tool_choice=req.params.tool_choice,
                    metadata=req.params.metadata,
                )

                response = await server.anthropic_messages(
                    messages_request
                )

                await server.batch_store.update_batch_result(
                    batch_id,
                    req.custom_id,
                    {
                        "type": "succeeded",
                        "message": response.model_dump(),
                    },
                )
        except Exception as e:
            await server.batch_store.update_batch_result(
                batch_id,
                req.custom_id,
                {
                    "type": "errored",
                    "error": {
                        "type": "api_error",
                        "message": str(e),
                    },
                },
            )


@app.get(
    "/v1/messages/batches/{batch_id}",
    response_model=AnthropicBatchResponse,
)
async def get_message_batch(
    batch_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """获取消息批处理"""
    batch = await server.batch_store.get_batch(batch_id)
    if not batch:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"Batch {batch_id} not found",
                )
            ).model_dump(),
        )
    return AnthropicBatchResponse(**batch)


@app.get(
    "/v1/messages/batches",
    response_model=AnthropicBatchListResponse,
)
async def list_message_batches(
    limit: int = 20,
    before_id: Optional[str] = None,
    after_id: Optional[str] = None,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """列出消息批处理"""
    batches = await server.batch_store.list_batches(
        limit=limit, before_id=before_id, after_id=after_id
    )
    return AnthropicBatchListResponse(
        data=[AnthropicBatchResponse(**b) for b in batches],
        has_more=len(batches) >= limit,
        first_id=batches[0]["id"] if batches else None,
        last_id=batches[-1]["id"] if batches else None,
    )


@app.post(
    "/v1/messages/batches/{batch_id}/cancel",
    response_model=AnthropicBatchResponse,
)
async def cancel_message_batch(
    batch_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """取消消息批处理"""
    batch = await server.batch_store.cancel_batch(batch_id)
    if not batch:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"Batch {batch_id} not found",
                )
            ).model_dump(),
        )
    return AnthropicBatchResponse(**batch)


@app.delete(
    "/v1/messages/batches/{batch_id}",
    response_model=AnthropicDeletedResponse,
)
async def delete_message_batch(
    batch_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """删除消息批处理"""
    success = await server.batch_store.delete_batch(batch_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"Batch {batch_id} not found",
                )
            ).model_dump(),
        )
    return AnthropicDeletedResponse(
        id=batch_id, type="message_batch"
    )


@app.get("/v1/messages/batches/{batch_id}/results")
async def get_message_batch_results(
    batch_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """获取消息批处理结果"""
    results = await server.batch_store.get_batch_results(batch_id)
    if results is None:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"Batch {batch_id} not found",
                )
            ).model_dump(),
        )

    jsonl_content = "\n".join(json.dumps(r) for r in results)
    return StreamingResponse(
        iter([jsonl_content]),
        media_type="application/x-jsonlines",
    )


# ====================
# Anthropic 文件路由
# ====================


@app.post("/v1/files", response_model=AnthropicFileResponse)
async def upload_file(
    request: Request,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """上传文件"""
    try:
        form = await request.form()
        file = form.get("file")

        if not file:
            raise HTTPException(
                status_code=400,
                detail=AnthropicErrorResponse(
                    error=AnthropicErrorDetail(
                        type="invalid_request_error",
                        message="file is required",
                    )
                ).model_dump(),
            )

        content = await file.read()
        filename = file.filename or "uploaded_file"
        content_type = (
            file.content_type or "application/octet-stream"
        )

        uploaded_file = await server.file_store.upload_file(
            filename=filename,
            content=content,
            content_type=content_type,
        )

        return AnthropicFileResponse(
            id=uploaded_file.id,
            filename=uploaded_file.filename,
            size=uploaded_file.size,
            created_at=uploaded_file.created_at,
            purpose=uploaded_file.purpose,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传异常: {e}")
        raise HTTPException(
            status_code=500,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="api_error", message=str(e)
                )
            ).model_dump(),
        )


@app.get(
    "/v1/files",
    response_model=AnthropicFileListResponse,
)
async def list_files(
    limit: int = 100,
    after_id: Optional[str] = None,
    purpose: Optional[str] = None,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """列出文件"""
    files = await server.file_store.list_files(
        limit=limit, after_id=after_id
    )
    return AnthropicFileListResponse(
        data=[
            AnthropicFileResponse(
                id=f.id,
                filename=f.filename,
                size=f.size,
                created_at=f.created_at,
                purpose=f.purpose,
            )
            for f in files
        ],
        has_more=len(files) >= limit,
        first_id=files[0].id if files else None,
        last_id=files[-1].id if files else None,
    )


@app.get(
    "/v1/files/{file_id}",
    response_model=AnthropicFileResponse,
)
async def get_file_metadata(
    file_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """获取文件元数据"""
    file = await server.file_store.get_file(file_id)
    if not file:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"File {file_id} not found",
                )
            ).model_dump(),
        )

    return AnthropicFileResponse(
        id=file.id,
        filename=file.filename,
        size=file.size,
        created_at=file.created_at,
        purpose=file.purpose,
    )


@app.get("/v1/files/{file_id}/content")
async def get_file_content(
    file_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """获取文件内容"""
    file = await server.file_store.get_file(file_id)
    if not file:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"File {file_id} not found",
                )
            ).model_dump(),
        )

    content = await server.file_store.get_file_content(file_id)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=(
                        f"File content for {file_id} not found"
                    ),
                )
            ).model_dump(),
        )

    return StreamingResponse(
        iter([content]),
        media_type=file.content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename={file.filename}"
            )
        },
    )


@app.delete(
    "/v1/files/{file_id}",
    response_model=AnthropicDeletedResponse,
)
async def delete_file(
    file_id: str,
    anthropic_version: Optional[str] = Depends(
        get_anthropic_version
    ),
    anthropic_beta: Optional[str] = Depends(get_anthropic_beta),
    x_api_key: Optional[str] = Header(
        None, alias="X-Api-Key"
    ),
):
    """删除文件"""
    success = await server.file_store.delete_file(file_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="not_found_error",
                    message=f"File {file_id} not found",
                )
            ).model_dump(),
        )
    return AnthropicDeletedResponse(id=file_id, type="file")


# ====================
# 静态文件路由
# ====================


@app.get("/v1/audio/files/{filename}")
async def get_audio_file(filename: str):
    """获取音频文件"""
    possible_paths = [
        Path("data/tts") / filename,
        Path("tts") / filename,
        Path(filename),
    ]

    for audio_path in possible_paths:
        if audio_path.exists() and audio_path.is_file():
            try:
                audio_path.resolve().relative_to(Path.cwd())
            except ValueError:
                continue

            suffix = audio_path.suffix.lower()
            mime_types = {
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".opus": "audio/opus",
                ".aac": "audio/aac",
                ".flac": "audio/flac",
                ".ogg": "audio/ogg",
            }
            media_type = mime_types.get(suffix, "audio/wav")

            return FileResponse(
                path=str(audio_path),
                media_type=media_type,
                filename=filename,
            )

    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                message=f"Audio file {filename} not found",
                type="invalid_request_error",
                code="file_not_found",
            )
        ).model_dump(),
    )


@app.get("/v1/files/images/{filename}")
async def get_image_file(filename: str):
    """获取图片文件"""
    possible_paths = [
        Path(Config.BASE64_IMAGE_DIR) / filename,
        Path("data/img") / filename,
        Path("img") / filename,
    ]

    for image_path in possible_paths:
        if image_path.exists() and image_path.is_file():
            try:
                image_path.resolve().relative_to(Path.cwd())
            except ValueError:
                continue

            mime_type = FileUtils.get_mime_type(filename)
            return FileResponse(
                path=str(image_path),
                media_type=mime_type,
                filename=filename,
            )

    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                message=f"Image file {filename} not found",
                type="invalid_request_error",
                code="file_not_found",
            )
        ).model_dump(),
    )


@app.get("/v1/files/generated/{filename}")
async def get_generated_image_file(filename: str):
    """获取生成的图片文件"""
    possible_paths = [
        Path(Config.GENERATED_IMAGE_DIR) / filename,
        Path("data/generated_images") / filename,
    ]

    for image_path in possible_paths:
        if image_path.exists() and image_path.is_file():
            try:
                image_path.resolve().relative_to(Path.cwd())
            except ValueError:
                continue

            mime_type = FileUtils.get_mime_type(filename)
            return FileResponse(
                path=str(image_path),
                media_type=mime_type,
                filename=filename,
            )

    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                message=(
                    f"Generated image file {filename} not found"
                ),
                type="invalid_request_error",
                code="file_not_found",
            )
        ).model_dump(),
    )


@app.get("/v1/files/videos/{filename}")
async def get_generated_video_file(filename: str):
    """获取生成的视频文件"""
    possible_paths = [
        Path(Config.GENERATED_VIDEO_DIR) / filename,
        Path("data/generated_videos") / filename,
    ]

    for video_path in possible_paths:
        if video_path.exists() and video_path.is_file():
            try:
                video_path.resolve().relative_to(Path.cwd())
            except ValueError:
                continue

            return FileResponse(
                path=str(video_path),
                media_type="video/mp4",
                filename=filename,
            )

    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                message=(
                    f"Generated video file {filename} not found"
                ),
                type="invalid_request_error",
                code="file_not_found",
            )
        ).model_dump(),
    )


@app.get("/v1/files/large_texts/{filename}")
async def get_large_text_file(filename: str):
    """获取大文本文件"""
    possible_paths = [
        Path(Config.LARGE_TEXT_DIR) / filename,
        Path("data/large_texts") / filename,
        Path("large_texts") / filename,
    ]

    for text_path in possible_paths:
        if text_path.exists() and text_path.is_file():
            try:
                text_path.resolve().relative_to(Path.cwd())
            except ValueError:
                continue

            mime_type = "text/plain"
            if filename.lower().endswith(".txt"):
                mime_type = "text/plain"
            elif filename.lower().endswith(".md"):
                mime_type = "text/markdown"
            elif filename.lower().endswith(".json"):
                mime_type = "application/json"
            elif filename.lower().endswith(".xml"):
                mime_type = "application/xml"
            elif filename.lower().endswith(".html"):
                mime_type = "text/html"

            return FileResponse(
                path=str(text_path),
                media_type=mime_type,
                filename=filename,
            )

    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                message=(
                    f"Large text file {filename} not found"
                ),
                type="invalid_request_error",
                code="file_not_found",
            )
        ).model_dump(),
    )


# ====================
# 状态和监控路由
# ====================


@app.get("/v1/status")
async def get_status():
    """获取服务状态"""
    return await server.get_status()


@app.get("/v1/metrics")
async def get_metrics():
    """获取详细性能指标"""
    return server.get_scheduler_metrics()


@app.get("/v1/metrics/schedulers")
async def get_scheduler_detail():
    """获取调度器详细状态"""
    return {
        "chat": server.chat_scheduler.get_metrics(),
        "media": server.media_scheduler.get_metrics(),
        "auxiliary": server.aux_scheduler.get_metrics(),
    }


# ====================
# 异常处理器
# ====================


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
):
    """HTTP 异常处理器"""
    return JSONResponse(
        status_code=exc.status_code,
        content=(
            exc.detail
            if isinstance(exc.detail, dict)
            else {"error": {"message": str(exc.detail)}}
        ),
    )


@app.exception_handler(Exception)
async def general_exception_handler(
    request: Request, exc: Exception
):
    """通用异常处理器"""
    logger.error(f"未处理的异常: {exc}")

    anthropic_version = request.headers.get("anthropic-version")
    if anthropic_version:
        return JSONResponse(
            status_code=500,
            content=AnthropicErrorResponse(
                error=AnthropicErrorDetail(
                    type="api_error",
                    message="Internal server error",
                )
            ).model_dump(),
        )

    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                message="Internal server error",
                type="server_error",
                code="internal_error",
            )
        ).model_dump(),
    )


# ====================
# 主函数
# ====================


def main() -> None:
    """主函数"""
    logger.info(
        f"启动 Qwen API 服务器: "
        f"http://{ServerConfig.HOST}:{ServerConfig.PORT}"
    )
    logger.info(
        f"API 文档: "
        f"http://{ServerConfig.HOST}:{ServerConfig.PORT}/docs"
    )
    logger.info("支持 OpenAI API 和 Anthropic API 格式")
    logger.info(
        f"FnCall 格式: Qwen 原生 "
        f"({FN_NAME}/{FN_ARGS}/{FN_RESULT}/{FN_EXIT})"
    )
    logger.info(
        f"大文本自动转文件阈值: "
        f"{Config.LARGE_TEXT_THRESHOLD} 字符"
    )
    logger.info(
        f"并发生成器数量: {Config.CONCURRENT_GENERATORS}"
    )
    logger.info(f"Chat ID 池大小: {Config.CHAT_ID_POOL_SIZE}")
    logger.info(
        f"调度器配置: "
        f"chat={SchedulerConfig.CHAT_MAX_CONCURRENT}/"
        f"{SchedulerConfig.CHAT_MAX_QUEUE}, "
        f"media={SchedulerConfig.MEDIA_MAX_CONCURRENT}/"
        f"{SchedulerConfig.MEDIA_MAX_QUEUE}, "
        f"aux={SchedulerConfig.AUX_MAX_CONCURRENT}/"
        f"{SchedulerConfig.AUX_MAX_QUEUE}"
    )
    logger.info(
        "调度策略: FIFO 公平队列 - 等待最久的请求优先响应"
    )

    uvicorn.run(
        app,
        host=ServerConfig.HOST,
        port=ServerConfig.PORT,
        log_level="info",
        access_log=True,
        loop="uvloop" if sys.platform != "win32" else "asyncio",
    )


if __name__ == "__main__":
    main()
