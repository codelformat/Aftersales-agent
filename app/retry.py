import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


def backoff_delay(
    retry_number: int, base_delay: float, max_delay: float, rand_value: float
) -> float:
    """计算有上限的指数回退延迟，并加入抖动。"""
    return min(base_delay * 2 ** (retry_number - 1), max_delay) * (0.5 + rand_value / 2)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float,
    max_delay: float,
    retry_on: tuple[type[BaseException], ...],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """重试指定异常。达到尝试次数上限时，抛出最后一次异常。"""
    if attempts < 1:
        raise ValueError("尝试次数必须至少为 1")

    retry_number = 0
    while True:
        try:
            return await fn()
        except retry_on:
            retry_number += 1
            if retry_number >= attempts:
                raise
            await sleep(backoff_delay(retry_number, base_delay, max_delay, rand()))
