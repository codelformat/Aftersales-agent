import random
from datetime import date, datetime, time, timedelta

CATALOG = {
    "P001": ("蓝牙耳机", 299),
    "P002": ("羊毛衫", 459),
    "P003": ("扫地机器人", 1999),
    "P004": ("电动牙刷", 199),
    "P005": ("台灯", 129),
    "P006": ("保温杯", 89),
    "P007": ("运动鞋", 599),
    "P008": ("手机壳", 39),
}
ORDER_STATUSES = ("待付款", "待发货", "已取消", "已发货", "已签收")
CARRIERS = ("顺丰", "中通", "圆通", "京东物流")


def product(product_id: str) -> dict:
    """按商品号生成固定的商品信息。"""
    rng = random.Random(f"product:{product_id}")
    if product_id in CATALOG:
        name, price = CATALOG[product_id]
    else:
        name, _ = rng.choice(tuple(CATALOG.values()))
        price = rng.randint(39, 1999)
    return {
        "product_id": product_id,
        "name": name,
        "price": price,
        "stock": rng.randint(0, 200),
        "warranty_days": rng.choice((0, 180, 365)),
        "no_reason_return": rng.choice((True, False)),
    }


def order(order_id: str, today: date) -> dict:
    """按订单号生成固定的订单信息，日期以 today 为基准。"""
    rng = random.Random(f"order:{order_id}")
    status = rng.choice(ORDER_STATUSES)
    if order_id == "1001":
        status = "已发货"
    items = []
    for product_id in rng.sample(tuple(CATALOG), rng.randint(1, 2)):
        name, price = CATALOG[product_id]
        items.append({
            "product_id": product_id,
            "name": name,
            "price": price,
            "quantity": rng.randint(1, 2),
        })
    created_at = datetime.combine(today, time(10, 0)) - timedelta(days=rng.randint(2, 20))
    return {
        "order_id": order_id,
        "status": status,
        "items": items,
        "total": sum(item["price"] * item["quantity"] for item in items),
        "created_at": created_at.strftime("%Y-%m-%d %H:%M"),
    }


def logistics(order_id: str, today: date) -> dict:
    """按订单状态生成物流信息，轨迹按时间先后排列。"""
    order_data = order(order_id, today)
    order_status = order_data["status"]
    rng = random.Random(f"logistics:{order_id}")
    result = {
        "order_id": order_id,
        "status": "未发货",
        "carrier": None,
        "tracking_no": None,
        "traces": [],
        "estimated_delivery": None,
    }
    if order_status in ("待付款", "待发货", "已取消"):
        return result

    result["carrier"] = rng.choice(CARRIERS)
    result["tracking_no"] = str(rng.randint(100000000000, 999999999999))
    signed = order_status == "已签收"
    count = rng.randint(3, 5) if signed else rng.randint(2, 4)
    result["status"] = "已签收" if signed else "运输中"
    if not signed:
        result["estimated_delivery"] = (today + timedelta(days=rng.randint(1, 3))).isoformat()

    origin, destination = rng.sample(("上海", "杭州", "广州", "深圳", "北京", "成都", "武汉", "南京"), 2)
    stages = [
        (f"{origin}仓库", "快件已揽收"),
        (f"{origin}分拨中心", f"快件已到达{origin}分拨中心"),
        (f"{destination}转运中心", f"快件已到达{destination}转运中心"),
        (f"{destination}营业部", "快件正在派送"),
    ]
    stages = stages[:count - 1] + (
        [(destination, "快件已签收")] if signed else [(f"{destination}转运中心", "快件运输中")]
    )
    created_at = datetime.strptime(order_data["created_at"], "%Y-%m-%d %H:%M")
    deadline = datetime.combine(today, time(23, 59))
    offsets = []
    elapsed_minutes = 0
    for _ in stages:
        elapsed_minutes += rng.randint(6, 30) * 60
        offsets.append(elapsed_minutes)
    available_minutes = int((deadline - created_at).total_seconds() // 60)
    if offsets[-1] > available_minutes:
        # 按比例压缩到截止时间，保留分钟精度和严格递增的顺序。
        offsets = [offset * available_minutes // offsets[-1] for offset in offsets]
    result["traces"] = [
        {
            "time": (created_at + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M"),
            "location": location,
            "description": description,
        }
        for offset, (location, description) in zip(offsets, stages)
    ]
    return result
