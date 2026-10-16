"""
定时任务模块
自动执行挖矿、领奖励、做任务等周期性任务
"""
import asyncio
from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
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

    async def _schedule_periodic(self, task_type: int, interval_min: int,
                                 name_fmt: str) -> int:
        """通用周期排期：防重复 + 已完成窗口去重。

        - PENDING/RUNNING 存在 → 跳过（防重复）
        - 上次成功时间 + interval 仍在未来 → 任务排到未来时刻（重启不丢时间账）
        - 到期或从未成功 → scheduled_at=now 立即执行

        这样重启后调度器第一轮不会把 interval 窗口内刚成功过的账号再全量跑一遍，
        只为真正到期的账号创建任务。
        """
        interval = timedelta(minutes=interval_min)
        async for db in get_db():
            accounts = await self._get_active_accounts(db)
            if not accounts:
                return 0
            ids = [a.id for a in accounts]

            # 在途任务集合（一条 SQL）
            busy_rows = await db.execute(
                select(Task.account_id).where(
                    Task.account_id.in_(ids),
                    Task.task_type == task_type,
                    Task.status.in_([0, 1]),
                )
            )
            busy = set(busy_rows.scalars().all())

            # 每账号最近一次成功时间（聚合一条 SQL，避免 N+1）
            ok_rows = await db.execute(
                select(Task.account_id, func.max(Task.updated_at)).where(
                    Task.account_id.in_(ids),
                    Task.task_type == task_type,
                    Task.status == 2,
                ).group_by(Task.account_id)
            )
            last_success = dict(ok_rows.all())

            now = datetime.now()
            created = 0
            for account in accounts:
                if account.id in busy:
                    continue
                last = last_success.get(account.id)
                if last is None:
                    scheduled_at = now  # 从未成功过：立即执行
                else:
                    due = last + interval
                    scheduled_at = due if due > now else now  # 窗口内：排到到期时刻
                await create_task(
                    account_id=account.id,
                    task_type=task_type,
                    task_name=name_fmt.format(username=account.username),
                    scheduled_at=scheduled_at,
                )
                created += 1
            return created

    async def _schedule_mining_tasks(self):
        """调度挖矿任务"""
        n = await self._schedule_periodic(
            TaskType.MINING, settings.mining_interval, "自动挖矿: {username}")
        print(f"ℹ️  挖矿任务排期完成，新建 {n} 个")

    async def _schedule_claim_tasks(self):
        """调度领取奖励任务"""
        n = await self._schedule_periodic(
            TaskType.CLAIM_REWARD, settings.claim_interval, "领取收益: {username}")
        print(f"ℹ️  领取奖励任务排期完成，新建 {n} 个")

    async def _schedule_sync_tasks(self):
        """调度同步账号信息任务"""
        n = await self._schedule_periodic(
            TaskType.UPDATE_PROFILE, settings.sync_interval, "同步账号信息: {username}")
        print(f"ℹ️  信息同步任务排期完成，新建 {n} 个")

    async def _schedule_mind_tasks(self):
        """调度mind答题任务（/app/si 每日12题，答对得算力）"""
        n = await self._schedule_periodic(
            TaskType.MIND, settings.mind_interval, "Mind答题: {username}")
        print(f"ℹ️  Mind 答题任务排期完成，新建 {n} 个")

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
