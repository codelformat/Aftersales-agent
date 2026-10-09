import asyncio
import os
import random
from typing import Annotated

from pydantic import Field

OrderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="订单号")]

PRODUCTS = (
    "蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳",
)


def delay() -> float:
    return float(os.getenv("MOCK_DELAY_SECONDS", "0"))


async def maybe_delay() -> None:
    seconds = delay()
    if seconds > 0:
        await asyncio.sleep(seconds)


def rng(kind: str, order_id: str) -> random.Random:
    return random.Random(f"{kind}:{order_id}")


def not_found(order_id: str) -> dict | None:
    if order_id.startswith("9"):
        return {"found": False, "order_id": order_id, "reason": "订单不存在"}
    return None
