"""进程内飞轮队列。单 worker 串行处理，防止并发查重把同一缺口建成两行。"""

import asyncio
import logging

from app.flywheel import pipeline

logger = logging.getLogger(__name__)


class FlywheelRunner:
    def __init__(self):
        self._queue: asyncio.Queue[int] | None = None
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._loop())

    def submit(self, lcq_id: int) -> bool:
        if self._worker is None or self._worker.done():
            logger.info("flywheel_runner_off lcq=%s", lcq_id)
            return False
        self._queue.put_nowait(lcq_id)
        return True

    async def _loop(self) -> None:
        while True:
            lcq_id = await self._queue.get()
            try:
                await pipeline.process(lcq_id)
            except Exception:
                logger.exception("flywheel_failed lcq=%s step=runner", lcq_id)
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        if self._queue is not None:
            await self._queue.join()

    async def stop(self) -> None:
        # 不等队列清空；未处理的行由 scripts/run_flywheel.py 补跑。
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        self._worker = None


_runner = FlywheelRunner()


def get_runner() -> FlywheelRunner:
    return _runner


def set_runner(runner: FlywheelRunner) -> None:
    global _runner
    _runner = runner
