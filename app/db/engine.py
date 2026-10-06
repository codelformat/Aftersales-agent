from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """返回全局 sessionmaker。第一次调用时按 DATABASE_URL 创建引擎。"""
    global _sessionmaker
    if _sessionmaker is None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return _sessionmaker


def set_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None:
    """替换全局 sessionmaker。测试用它指向测试库；传 None 恢复为按配置创建。"""
    global _sessionmaker
    _sessionmaker = sm


async def dispose_engine() -> None:
    """关闭全局引擎。脚本在 asyncio.run 结束前调用，避免连接在事件循环关闭后回收。"""
    global _sessionmaker
    if _sessionmaker is not None:
        await _sessionmaker.kw["bind"].dispose()
        _sessionmaker = None
