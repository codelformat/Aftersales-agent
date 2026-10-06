import pytest


@pytest.fixture
def anyio_backend():
    # 只在 asyncio 上运行异步测试。
    return "asyncio"
