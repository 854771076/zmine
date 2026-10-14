import asyncio
import json
import random
from datetime import datetime
from typing import Callable, Dict, Any, List, Optional
from enum import IntEnum
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, or_
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.config.settings import settings
from src.db.models import Task, Account
from src.db.session import get_db
from src.site.client import ZMineClient, ZMineAPIError, solve_mind_task

class TaskStatus(IntEnum):
    """任务状态枚举"""
    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    CANCELLED = 4

class TaskType(IntEnum):
    """任务类型枚举"""
    REGISTER = 1
    LOGIN = 2
    COMPLETE_TASK = 3
    MINING = 4
    CLAIM_REWARD = 5
    UPDATE_PROFILE = 6
    MIND = 7

class TaskWorker:
    """任务工作器"""

    def __init__(self, max_concurrent: Optional[int] = None):
        self.max_concurrent = max_concurrent or settings.max_concurrent
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        self.task_handlers: Dict[int, Callable] = {}
        self._register_default_handlers()
        self.running = False

    def _register_default_handlers(self) -> None:
        """注册默认任务处理器"""
        self.task_handlers[TaskType.REGISTER] = self._handle_register_task
        self.task_handlers[TaskType.LOGIN] = self._handle_login_task
        self.task_handlers[TaskType.MINING] = self._handle_mining_task
        self.task_handlers[TaskType.CLAIM_REWARD] = self._handle_claim_reward_task
        self.task_handlers[TaskType.UPDATE_PROFILE] = self._handle_update_profile_task
        self.task_handlers[TaskType.MIND] = self._handle_mind_task

    def register_handler(self, task_type: int, handler: Callable) -> None:
        """注册自定义任务处理器"""
        self.task_handlers[task_type] = handler

    async def _update_task_status(
        self,
        db: AsyncSession,
        task_id: int,
        status: int,
        error_msg: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None
    ) -> None:
        """更新任务状态"""
        update_data = {
            "status": status,
            "updated_at": datetime.now()
        }

        if status == TaskStatus.RUNNING:
            update_data["started_at"] = datetime.now()
        elif status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            update_data["finished_at"] = datetime.now()

        if error_msg:
            update_data["error_msg"] = error_msg
        if result:
            update_data["result"] = json.dumps(result, ensure_ascii=False)

        await db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(**update_data)
        )
        await db.commit()

    async def _get_account_client(self, account: Account) -> ZMineClient:
        """获取账号对应的API客户端"""
        cookies = json.loads(account.cookies) if account.cookies else None
        return ZMineClient(
            cookies=cookies,
            token=account.token
        )

    async def _handle_register_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """处理注册任务：在站点注册新账号并保存 loginKey/助记词"""
        task_params = json.loads(task.task_params) if task.task_params else {}
        display_name = task_params.get("display_name") or account.username
        referral_code = task_params.get("referral_code")

        async with await self._get_account_client(account) as client:
            result = await client.register(display_name, referral_code)

            update_data: Dict[str, Any] = {
                # 注册响应会 Set-Cookie 下发 zmine_session，必须保存，否则后续业务请求全部 401
                "cookies": json.dumps(client.get_cookies_dict(), ensure_ascii=False),
                "updated_at": datetime.now()
            }
            # loginKey 是后续登录凭证，存入 password 字段
            if result.get("loginKey"):
                update_data["password"] = result["loginKey"]
            # 站点返回的 handle 即该账号的邀请码
            user = result.get("user") or {}
            if user.get("handle"):
                update_data["invite_code"] = user["handle"]

            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(**update_data)
            )
            await db.commit()

            return result

    async def _handle_login_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """处理登录任务：站点通过 Set-Cookie 下发 zmine_session，无 token 返回"""
        async with await self._get_account_client(account) as client:
            # password 字段存储的是站点的 loginKey
            result = await client.login(account.password)

            # 更新账号信息（认证凭证在 cookie 中）
            new_cookies = client.get_cookies_dict()
            update_data = {
                "cookies": json.dumps(new_cookies, ensure_ascii=False),
                "updated_at": datetime.now()
            }

            # 站点返回的 handle 即该账号的邀请码
            user = result.get("user") or {}
            if user.get("handle"):
                update_data["invite_code"] = user["handle"]

            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(**update_data)
            )
            await db.commit()

            return result

    async def _handle_mining_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """处理挖矿任务：确保24小时挖矿会话活跃（智能续期）"""
        async with await self._get_account_client(account) as client:
            status = await client.get_mining_status()

            # 会话仍活跃且剩余充足（>2小时）时跳过续期，避免盲目activate产生409
            remaining_ms = status.get("sessionRemainingMs") or 0
            if status.get("sessionActive") and remaining_ms > 2 * 3600 * 1000:
                status["skipped"] = True
                status["skip_reason"] = f"session active, {remaining_ms // 3600000}h remaining"
            else:
                status = await client.start_mining()

            # 更新挖矿状态（sessionActive 为站点真实会话状态）
            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(
                    mining_status=1 if status.get("sessionActive") else 0,
                    updated_at=datetime.now()
                )
            )
            await db.commit()

            return status

    async def _handle_claim_reward_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """处理领取收益任务：站点收益按epoch自动累计，此处同步最新收益与余额"""
        async with await self._get_account_client(account) as client:
            result = await client.sync_account_stats()

            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(
                    points=result.get("points", account.points),
                    total_points=result.get("total_points", account.total_points),
                    mining_status=result.get("mining_status", account.mining_status),
                    last_mining_claim=datetime.now(),
                    updated_at=datetime.now()
                )
            )
            await db.commit()

            return result

    async def _handle_update_profile_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """处理同步账号信息任务：拉取挖矿状态与钱包余额"""
        async with await self._get_account_client(account) as client:
            result = await client.sync_account_stats()

            # 更新账号信息
            update_data = {
                "points": result.get("points", account.points),
                "total_points": result.get("total_points", account.total_points),
                "mining_status": result.get("mining_status", account.mining_status),
                "updated_at": datetime.now()
            }

            user = result.get("user") or {}
            if user.get("handle") and not account.invite_code:
                update_data["invite_code"] = user["handle"]

            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(**update_data)
            )
            await db.commit()

            return result

    async def _handle_mind_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """处理mind答题任务：取题 -> 本地解题 -> 提交（每日上限12题，答对+5000mH算力24h）"""
        answered = 0
        correct = 0
        credited_mh = 0.0
        last_stats: Dict[str, Any] = {}
        detail: List[Dict[str, Any]] = []

        async with await self._get_account_client(account) as client:
            cooldown_budget = 15 * 60  # 单次任务最多累计等待冷却15分钟（答错触发约10分钟冷却）
            for _ in range(15):  # 站点每日上限12题（答对才计入），留少量余量
                data = await client.get_mind_task()
                last_stats = data.get("stats") or last_stats
                mind_task = data.get("task")
                if not mind_task:
                    # 冷却期内取不到题（答错触发的反作弊冷却），等待冷却结束后重试
                    cooldown_s = (last_stats.get("cooldownLeftMs") or 0) / 1000
                    if 0 < cooldown_s < cooldown_budget:
                        cooldown_budget -= cooldown_s
                        await asyncio.sleep(cooldown_s + 2)
                        continue
                    # 今日已完成（remainingToday=0）、冷却过长或站点暂无题目
                    break

                answer = solve_mind_task(mind_task.get("prompt") or "", mind_task.get("options") or [])
                # 模拟阅读耗时，避免站点 tooFast 判定（token 签发15分钟内有效，充裕）
                await asyncio.sleep(random.uniform(4, 9))
                resp = await client.answer_mind_task(mind_task["token"], answer)

                answered += 1
                is_correct = bool(resp.get("correct"))
                correct += 1 if is_correct else 0
                credited_mh += float(resp.get("creditedMh") or 0)
                detail.append({
                    "slug": mind_task.get("slug"),
                    "answer": answer,
                    "correct": is_correct,
                })

                stats = resp.get("stats") or {}
                if stats:
                    last_stats = stats
                if (stats.get("cooldownLeftMs") or 0) > 0:
                    await asyncio.sleep(stats["cooldownLeftMs"] / 1000 + 1)
                if (stats.get("remainingToday") or 0) <= 0:
                    break

        if answered:
            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(updated_at=datetime.now())
            )
            await db.commit()

        return {
            "answered": answered,
            "correct": correct,
            "creditedMh": credited_mh,
            "stats": last_stats,
            "detail": detail[:20],
        }

    @retry(
        stop=stop_after_attempt(settings.task_max_retries),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ZMineAPIError, asyncio.TimeoutError)),
        reraise=True
    )
    async def _execute_task(self, task: Task, account: Account, db: AsyncSession) -> Dict[str, Any]:
        """执行单个任务，带重试机制"""
        if task.task_type not in self.task_handlers:
            raise ValueError(f"No handler registered for task type: {task.task_type}")

        handler = self.task_handlers[task.task_type]
        return await handler(task, account, db)

    async def _process_task(self, task: Task) -> None:
        """处理单个任务（任务在认领阶段已置为RUNNING）"""
        async with self.semaphore:
            async for db in get_db():
                try:
                    # 获取关联账号
                    result = await db.execute(
                        select(Account).where(Account.id == task.account_id)
                    )
                    account = result.scalar_one_or_none()

                    if not account:
                        raise ValueError(f"Account not found: {task.account_id}")

                    if account.status != 1:
                        raise ValueError(f"Account is disabled or abnormal: {task.account_id}")

                    # 执行任务（会话缺失/过期时自动重登并重试一次，不消耗重试次数）
                    try:
                        task_result = await self._execute_task(task, account, db)
                    except ZMineAPIError as e:
                        if e.status_code != 401 or task.task_type == TaskType.LOGIN:
                            raise
                        # 用 loginKey 重新登录刷新 zmine_session，持久化后重试
                        await self._handle_login_task(task, account, db)
                        await db.refresh(account)
                        task_result = await self._execute_task(task, account, db)

                    # 更新任务状态为成功
                    await self._update_task_status(
                        db,
                        task.id,
                        TaskStatus.SUCCESS,
                        result=task_result
                    )

                except Exception as e:
                    # 重试次数检查
                    if task.retry_count < task.max_retries:
                        # 增加重试次数，状态重置为等待
                        await db.execute(
                            update(Task)
                            .where(Task.id == task.id)
                            .values(
                                retry_count=task.retry_count + 1,
                                status=TaskStatus.PENDING,
                                error_msg=str(e),
                                updated_at=datetime.now()
                            )
                        )
                        await db.commit()
                    else:
                        # 超过最大重试次数，标记为失败
                        await self._update_task_status(
                            db,
                            task.id,
                            TaskStatus.FAILED,
                            error_msg=str(e)
                        )

    async def _claim_pending_tasks(self, db: AsyncSession, limit: int = 100) -> List[Task]:
        """原子认领待执行任务
        拾取与置RUNNING在一条 UPDATE 中完成（WHERE status=PENDING 保证只被认领一次），
        防止多轮轮询或多进程重复拾取同一批任务
        """
        now = datetime.now()
        result = await db.execute(
            select(Task)
            .where(
                Task.status == TaskStatus.PENDING,
                # scheduled_at 为空表示立即执行，否则到计划时间才执行
                or_(
                    Task.scheduled_at.is_(None),
                    Task.scheduled_at <= now
                )
            )
            .order_by(Task.created_at.asc())
            .limit(limit)
        )
        candidates = list(result.scalars().all())

        claimed: List[Task] = []
        for task in candidates:
            claim = await db.execute(
                update(Task)
                .where(Task.id == task.id, Task.status == TaskStatus.PENDING)
                .values(status=TaskStatus.RUNNING, started_at=now, updated_at=now)
            )
            if claim.rowcount == 1:
                task.status = TaskStatus.RUNNING
                claimed.append(task)
        await db.commit()
        return claimed

    async def _recover_orphan_running_tasks(self, db: AsyncSession) -> int:
        """回收孤儿任务：服务停止/崩溃时已认领(RUNNING)但未完成的任务重置为PENDING"""
        result = await db.execute(
            update(Task)
            .where(Task.status == TaskStatus.RUNNING)
            .values(status=TaskStatus.PENDING, updated_at=datetime.now())
        )
        await db.commit()
        return result.rowcount or 0

    async def run(self, batch_size: int = 100, poll_interval: int = 5) -> None:
        """启动任务工作器"""
        self.running = True
        print(f"Task worker started, max concurrent: {self.max_concurrent}")

        # 启动时回收上次异常退出遗留的RUNNING任务
        async for db in get_db():
            recovered = await self._recover_orphan_running_tasks(db)
            if recovered:
                print(f"Recovered {recovered} orphan running tasks -> pending")
            break

        while self.running:
            try:
                async for db in get_db():
                    # 原子认领待执行任务（认领即置RUNNING，其他轮询不会重复拾取）
                    pending_tasks = await self._claim_pending_tasks(db, batch_size)

                    if pending_tasks:
                        print(f"Claimed {len(pending_tasks)} tasks, processing...")
                        # 并发处理任务
                        tasks = [asyncio.create_task(self._process_task(task)) for task in pending_tasks]
                        await asyncio.gather(*tasks, return_exceptions=True)

                    # 等待轮询间隔
                    await asyncio.sleep(poll_interval)

            except Exception as e:
                print(f"Worker loop error: {str(e)}")
                await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        """停止任务工作器"""
        self.running = False
        print("Task worker stopping...")

async def create_task(
    account_id: int,
    task_type: int,
    task_name: str,
    task_params: Optional[Dict[str, Any]] = None,
    scheduled_at: Optional[datetime] = None,
    max_retries: int = settings.task_max_retries
) -> Task:
    """创建新任务"""
    async for db in get_db():
        task = Task(
            account_id=account_id,
            task_type=task_type,
            task_name=task_name,
            task_params=json.dumps(task_params, ensure_ascii=False) if task_params else None,
            scheduled_at=scheduled_at,
            max_retries=max_retries
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

async def batch_create_tasks(
    account_ids: List[int],
    task_type: int,
    task_name: str,
    task_params: Optional[Dict[str, Any]] = None,
    scheduled_at: Optional[datetime] = None,
    max_retries: int = settings.task_max_retries
) -> List[Task]:
    """批量创建任务"""
    tasks = []
    async for db in get_db():
        for account_id in account_ids:
            task = Task(
                account_id=account_id,
                task_type=task_type,
                task_name=task_name,
                task_params=json.dumps(task_params, ensure_ascii=False) if task_params else None,
                scheduled_at=scheduled_at,
                max_retries=max_retries
            )
            db.add(task)
            tasks.append(task)

        await db.commit()
        for task in tasks:
            await db.refresh(task)

        return tasks