from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from typing import AsyncGenerator
from src.config.settings import settings
from src.db.models import Base

# 创建异步引擎
engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=settings.web_debug,
    future=True
)

# 创建异步会话工厂
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def init_db() -> None:
    """初始化数据库表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all 不会给已存在的表加列，这里做简单的幂等列迁移
        if not await _has_column(conn, "tasks", "updated_at"):
            await conn.execute(text("ALTER TABLE tasks ADD COLUMN updated_at DATETIME"))
        # 历史数据时区迁移：模型曾用 func.now()(UTC)，与代码里的 datetime.now()(本地) 混存
        # 差 8 小时，统一把 created_at 类列修正为本地时间（user_version 幂等标记）
        version = (await conn.exec_driver_sql("PRAGMA user_version")).scalar()
        if version < 1:
            for table in ("tasks", "accounts", "invite_relations", "points_history", "scheduled_tasks"):
                exists = (await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                )).scalar()
                if not exists or not await _has_column(conn, table, "created_at"):
                    continue
                await conn.execute(
                    text(f"UPDATE {table} SET created_at = datetime(created_at, '+8 hours')")
                )
            await conn.exec_driver_sql("PRAGMA user_version = 1")

async def _has_column(conn, table: str, column: str) -> bool:
    result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in result)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()