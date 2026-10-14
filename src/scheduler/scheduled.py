"""
定时任务模块
自动执行挖矿、领奖励、做任务等周期性任务
"""
import asyncio
from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.config.settings import settings
from src.db.session import get_db
from src.db.models import Account, Task
from src.scheduler.worker import TaskType, create_task

class ScheduledTaskManager:
    """定时任务管理器"""

    def __init__(self):
        self.running = False
        self.tasks = []

    async def _get_active_accounts(self, db: AsyncSession) -> List[Account]:
        """获取所有活跃且已注册（有站点会话）的账号
        cookies 为空说明注册/登录任务尚未完成，此时排业务任务只会 401→重登→429 空转
        """
        result = await db.execute(
            select(Account).where(
                Account.status == 1,
                Account.cookies.isnot(None)
            )
        )
        return list(result.scalars().all())

    async def _schedule_mining_tasks(self):
        """调度挖矿任务"""
        async for db in get_db():
            accounts = await self._get_active_accounts(db)
            for account in accounts:
                # 检查是否有未完成的挖矿任务
                existing = await db.execute(
                    select(Task).where(
                        Task.account_id == account.id,
                        Task.task_type == TaskType.MINING,
                        Task.status.in_([0, 1])
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                # 创建挖矿任务
                await create_task(
                    account_id=account.id,
                    task_type=TaskType.MINING,
                    task_name=f"自动挖矿: {account.username}",
                    scheduled_at=datetime.now()
                )
            print(f"ℹ️  已为 {len(accounts)} 个账号调度挖矿任务")

    async def _schedule_claim_tasks(self):
        """调度领取奖励任务"""
        async for db in get_db():
            accounts = await self._get_active_accounts(db)
            for account in accounts:
                # 检查是否有未完成的领取任务
                existing = await db.execute(
                    select(Task).where(
                        Task.account_id == account.id,
                        Task.task_type == TaskType.CLAIM_REWARD,
                        Task.status.in_([0, 1])
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                # 创建领取奖励任务
                await create_task(
                    account_id=account.id,
                    task_type=TaskType.CLAIM_REWARD,
                    task_name=f"领取收益: {account.username}",
                    scheduled_at=datetime.now()
                )
            print(f"ℹ️  已为 {len(accounts)} 个账号调度领取奖励任务")

    async def _schedule_sync_tasks(self):
        """调度同步账号信息任务"""
        async for db in get_db():
            accounts = await self._get_active_accounts(db)
            for account in accounts:
                # 检查是否有未完成的同步任务
                existing = await db.execute(
                    select(Task).where(
                        Task.account_id == account.id,
                        Task.task_type == TaskType.UPDATE_PROFILE,
                        Task.status.in_([0, 1])
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                # 创建同步任务
                await create_task(
                    account_id=account.id,
                    task_type=TaskType.UPDATE_PROFILE,
                    task_name=f"同步账号信息: {account.username}",
                    scheduled_at=datetime.now()
                )
            print(f"ℹ️  已为 {len(accounts)} 个账号调度信息同步任务")

    async def _schedule_mind_tasks(self):
        """调度mind答题任务（/app/si 每日12题，答对得算力）"""
        async for db in get_db():
            accounts = await self._get_active_accounts(db)
            for account in accounts:
                # 检查是否有未完成的答题任务
                existing = await db.execute(
                    select(Task).where(
                        Task.account_id == account.id,
                        Task.task_type == TaskType.MIND,
                        Task.status.in_([0, 1])
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                # 创建答题任务
                await create_task(
                    account_id=account.id,
                    task_type=TaskType.MIND,
                    task_name=f"Mind答题: {account.username}",
                    scheduled_at=datetime.now()
                )
            print(f"ℹ️  已为 {len(accounts)} 个账号调度 mind 答题任务")

    async def run(self):
        """运行定时任务管理器"""
        self.running = True
        print("⏰ 定时任务管理器已启动")
        print(f"   挖矿间隔: {settings.mining_interval} 分钟")
        print(f"   领取奖励间隔: {settings.claim_interval} 分钟")
        print(f"   mind答题间隔: {settings.mind_interval} 分钟")
        print(f"   信息同步间隔: {settings.sync_interval} 分钟")

        # 记录上次执行时间
        last_mining = datetime.min
        last_claim = datetime.min
        last_mind = datetime.min
        last_sync = datetime.min

        while self.running:
            now = datetime.now()

            # 挖矿任务
            if now - last_mining > timedelta(minutes=settings.mining_interval):
                try:
                    await self._schedule_mining_tasks()
                    last_mining = now
                except Exception as e:
                    print(f"❌ 调度挖矿任务失败: {str(e)}")

            # 领取奖励任务
            if now - last_claim > timedelta(minutes=settings.claim_interval):
                try:
                    await self._schedule_claim_tasks()
                    last_claim = now
                except Exception as e:
                    print(f"❌ 调度领取任务失败: {str(e)}")

            # 日常任务（站点无任务API，已移除COMPLETE_TASK调度）

            # mind 答题（每日12题，答对得算力，站点每日重置）
            if now - last_mind > timedelta(minutes=settings.mind_interval):
                try:
                    await self._schedule_mind_tasks()
                    last_mind = now
                except Exception as e:
                    print(f"❌ 调度mind答题任务失败: {str(e)}")

            # 信息同步
            if now - last_sync > timedelta(minutes=settings.sync_interval):
                try:
                    await self._schedule_sync_tasks()
                    last_sync = now
                except Exception as e:
                    print(f"❌ 调度同步任务失败: {str(e)}")

            # 等待1分钟
            await asyncio.sleep(60)

    def stop(self):
        """停止定时任务管理器"""
        self.running = False
        print("⏰ 定时任务管理器已停止")
