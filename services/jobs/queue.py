"""
Очередь тяжёлых заданий: одновременно работают не больше MAX_PARALLEL_JOBS,
остальные ждут в порядке поступления и видят свою позицию.

Без очереди несколько видео сразу делят 4 ядра и 6 ГБ памяти: всё идёт медленно,
а при нехватке памяти контейнер перезапускается и теряет все задания разом.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog

from config.settings import settings

logger = structlog.get_logger(__name__)

PositionCallback = Callable[[int], Awaitable[None]]


class JobQueue:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._running: set[str] = set()
        self._waiting: list[str] = []
        self._callbacks: dict[str, PositionCallback] = {}
        self._changed = asyncio.Condition()

    @property
    def running(self) -> int:
        return len(self._running)

    @property
    def waiting(self) -> int:
        return len(self._waiting)

    async def _announce_positions(self) -> None:
        for position, job_id in enumerate(self._waiting, start=1):
            callback = self._callbacks.get(job_id)
            if callback is None:
                continue
            try:
                await callback(position)
            except Exception as exc:
                logger.debug("queue_position_notify_failed", job_id=job_id, error=str(exc))

    @asynccontextmanager
    async def slot(self, job_id: str, on_wait: PositionCallback | None = None) -> AsyncIterator[None]:
        async with self._changed:
            must_wait = len(self._running) >= self._limit or bool(self._waiting)
            if must_wait:
                self._waiting.append(job_id)
                if on_wait is not None:
                    self._callbacks[job_id] = on_wait
                logger.info("job_queued", job_id=job_id, position=len(self._waiting), running=len(self._running))
        if must_wait:
            await self._announce_positions()
        try:
            async with self._changed:
                # FIFO: ждём, пока задание первое в очереди и есть свободное место
                await self._changed.wait_for(
                    lambda: len(self._running) < self._limit
                    and (not self._waiting or self._waiting[0] == job_id)
                )
                if job_id in self._waiting:
                    self._waiting.remove(job_id)
                self._callbacks.pop(job_id, None)
                self._running.add(job_id)
            logger.info("job_started_from_queue", job_id=job_id, running=len(self._running))
            if must_wait:
                await self._announce_positions()
        except BaseException:
            # Отмена (/cancel) во время ожидания — убираем из очереди и сдвигаем остальных
            async with self._changed:
                if job_id in self._waiting:
                    self._waiting.remove(job_id)
                self._callbacks.pop(job_id, None)
                self._changed.notify_all()
            await self._announce_positions()
            raise

        try:
            yield
        finally:
            async with self._changed:
                self._running.discard(job_id)
                self._changed.notify_all()


JOB_QUEUE = JobQueue(settings.max_parallel_jobs)
