from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, asc, desc
from src.config.settings import settings
from src.db.session import get_db, init_db
from src.db.models import Account, Task, InviteRelation, Setting
from src.scheduler.worker import TaskStatus, TaskType, create_task, batch_create_tasks
from src.invite.matrix import InviteMatrixManager
from src.site.client import ZMineClient, ZMineAPIError
import asyncio
import json
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

# 创建FastAPI应用
app = FastAPI(
    title="ZMINE Automation System",
    description="Automation system for zmine.si website",
    version="0.1.0",
    debug=settings.web_debug
)

# 静态文件配置
app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

# 读取HTML文件内容
def read_html_file(filename: str) -> str:
    path = Path(__file__).parent / "templates" / filename
    return path.read_text(encoding="utf-8")

# 页面路由
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=read_html_file("index.html"))

@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page():
    return HTMLResponse(content=read_html_file("accounts.html"))

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page():
    return HTMLResponse(content=read_html_file("tasks.html"))

@app.get("/invite", response_class=HTMLResponse)
async def invite_page():
    return HTMLResponse(content=read_html_file("invite.html"))

@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse(content=read_html_file("settings.html"))

# Pydantic模型
class AccountCreate(BaseModel):
    username: str
    password: Optional[str] = ""
    email: Optional[str] = ""
    invite_code: Optional[str] = ""
    inviter_id: Optional[int] = None

class AccountUpdate(BaseModel):
    password: Optional[str] = None
    email: Optional[str] = None
    status: Optional[int] = None

class TaskCreate(BaseModel):
    account_id: int
    task_type: int
    task_name: str
    task_params: Optional[Dict[str, Any]] = None
    scheduled_at: Optional[datetime] = None
    max_retries: Optional[int] = 3

class BatchTaskCreate(BaseModel):
    account_ids: List[int]
    task_type: int
    task_name: str
    task_params: Optional[Dict[str, Any]] = None
    scheduled_at: Optional[datetime] = None
    max_retries: Optional[int] = 3

class MatrixGenerate(BaseModel):
    new_account_ids: List[int]
    root_inviter_id: Optional[int] = None

class BatchAccountCreate(BaseModel):
    count: int
    inviter_id: Optional[int] = None
    auto_register: bool = True  # 创建后是否自动提交注册任务
    max_retries: Optional[int] = 3

class BatchLogin(BaseModel):
    account_ids: List[int]

class ConfigUpdate(BaseModel):
    key: str
    value: str
    description: Optional[str] = None


# 随机用户名生成（fake风格：形容词_名词+数字）
_FAKE_ADJ = ["swift", "silent", "golden", "lucky", "cosmic", "hidden", "rapid", "brave",
             "clever", "steady", "amber", "cobalt", "ivory", "onyx", "delta", "noble"]
_FAKE_NOUN = ["miner", "hash", "forge", "coin", "pick", "vault", "spark", "pulse",
              "block", "epoch", "node", "cliff", "ridge", "storm", "flint", "quartz"]

def _generate_username(used: set) -> str:
    """生成随机用户名，确保与已有集合不重复"""
    while True:
        name = f"{random.choice(_FAKE_ADJ)}_{random.choice(_FAKE_NOUN)}{random.randint(10, 99)}"
        if name not in used:
            used.add(name)
            return name

def _generate_placeholder(used: set) -> str:
    """生成唯一占位邀请码（注册成功后会被站点 handle 覆盖）"""
    while True:
        code = f"PENDING_{secrets.token_hex(4)}"
        if code not in used:
            used.add(code)
            return code

# 初始化数据库
@app.on_event("startup")
async def startup_event():
    await init_db()
    # 这里可以启动任务工作器
    # asyncio.create_task(TaskWorker().run())

# -------------------------- 账号管理接口 --------------------------
@app.get("/api/accounts")
async def get_accounts(
    page: int = 1,
    page_size: int = 20,
    status: Optional[int] = None,
    sort_by: str = "id",
    sort_order: str = "desc",
    db: AsyncSession = Depends(get_db)
):
    """获取账号列表"""
    query = select(Account)
    if status is not None:
        query = query.where(Account.status == status)

    # 排序（白名单防注入）
    sort_columns = {
        "id": Account.id,
        "username": Account.username,
        "points": Account.points,
        "total_points": Account.total_points,
        "mining_status": Account.mining_status,
        "status": Account.status,
        "created_at": Account.created_at,
    }
    sort_col = sort_columns.get(sort_by, Account.id)
    query = query.order_by(desc(sort_col) if sort_order == "desc" else asc(sort_col))

    # 分页
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    accounts = list(result.scalars().all())

    # 计算总数（与列表同条件过滤）
    count_query = select(func.count(Account.id))
    if status is not None:
        count_query = count_query.where(Account.status == status)
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    return {
        "accounts": [
            {
                "id": acc.id,
                "username": acc.username,
                "email": acc.email,
                "invite_code": acc.invite_code,
                "inviter_id": acc.inviter_id,
                "points": acc.points,
                "total_points": acc.total_points,
                "mining_status": acc.mining_status,
                "status": acc.status,
                "created_at": acc.created_at,
                "updated_at": acc.updated_at
            } for acc in accounts
        ],
        "total": total,
        "page": page,
        "page_size": page_size
    }

@app.get("/api/accounts/{account_id}")
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """获取账号详情"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    return {
        "id": account.id,
        "username": account.username,
        "email": account.email,
        "invite_code": account.invite_code,
        "inviter_id": account.inviter_id,
        "points": account.points,
        "total_points": account.total_points,
        "mining_status": account.mining_status,
        "last_mining_claim": account.last_mining_claim,
        "status": account.status,
        "has_session": bool(account.cookies),
        "created_at": account.created_at,
        "updated_at": account.updated_at
    }

@app.post("/api/accounts")
async def create_account(account_data: AccountCreate, db: AsyncSession = Depends(get_db)):
    """创建新账号"""
    # 检查用户名是否存在
    result = await db.execute(select(Account).where(Account.username == account_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    # 注册账号
    async with ZMineClient() as client:
        try:
            register_result = await client.register(
                display_name=account_data.username,
                referral_code=account_data.invite_code
            )
        except ZMineAPIError as e:
            raise HTTPException(status_code=400, detail=f"注册失败: {e.message}")

    account = Account(
        username=account_data.username,
        password=register_result.get("loginKey", ""),  # 存储loginKey作为密码
        email=account_data.email,
        invite_code=register_result["user"].get("handle", ""),
        inviter_id=account_data.inviter_id,
        token=register_result.get("loginKey", ""),
        cookies=json.dumps(client.get_cookies_dict(), ensure_ascii=False),
        status=1
    )

    db.add(account)
    await db.commit()
    await db.refresh(account)

    # 如果指定了邀请人，创建邀请关系
    if account_data.inviter_id:
        matrix_manager = InviteMatrixManager()
        await matrix_manager._create_invite_relation(db, account_data.inviter_id, account.id, level=1)

    return {"id": account.id, "username": account.username, "message": "Account created successfully"}


@app.post("/api/accounts/batch")
async def batch_create_accounts(data: BatchAccountCreate, db: AsyncSession = Depends(get_db)):
    """批量创建账号：随机用户名，可选挂到邀请人下，可选自动提交站点注册任务

    注册异步完成：worker 执行 REGISTER 任务时在站点注册并把 loginKey/handle 回填到账号
    """
    if not 1 <= data.count <= 100:
        raise HTTPException(status_code=400, detail="count 取值范围 1-100")

    # 指定邀请人时校验其存在；未指定则从"已有真实邀请码"的账号池中逐个随机分配
    inviter = None
    if data.inviter_id is not None:
        result = await db.execute(select(Account).where(Account.id == data.inviter_id))
        inviter = result.scalar_one_or_none()
        if not inviter:
            raise HTTPException(status_code=404, detail=f"Inviter not found: {data.inviter_id}")
    else:
        result = await db.execute(
            select(Account.id, Account.invite_code)
            .where(
                Account.status == 1,
                Account.invite_code.isnot(None),
                Account.invite_code.notlike("PENDING_%"),  # 排除未注册的占位码
            )
        )
        valid_referrers = result.all()
        if not valid_referrers:
            raise HTTPException(
                status_code=400,
                detail="没有可用的邀请码：请先完成至少一个账号的注册，或手动指定邀请人ID"
            )

    def pick_referral() -> tuple[Optional[int], Optional[str]]:
        """返回 (inviter_id, 站点推荐码)；指定邀请人用固定值，否则从有效池随机"""
        if data.inviter_id is not None:
            return inviter.id, inviter.invite_code
        if not valid_referrers:
            return None, None
        aid, code = random.choice(valid_referrers)
        return aid, code

    # 载入已有用户名和邀请码，避免唯一约束冲突
    result = await db.execute(select(Account.username))
    used_names = {row[0] for row in result.all()}
    result = await db.execute(select(Account.invite_code))
    used_codes = {row[0] for row in result.all()}

    created = []
    now = datetime.now()
    for _ in range(data.count):
        # 未指定邀请人时，每个新账号独立随机挂到现有有效邀请码下（分散矩阵）
        picked_inviter_id, picked_referral = pick_referral()
        account = Account(
            username=_generate_username(used_names),
            password="PENDING_REGISTER",  # 注册任务成功后回填站点 loginKey
            invite_code=_generate_placeholder(used_codes),
            inviter_id=picked_inviter_id,
            status=1
        )
        db.add(account)
        created.append((account, picked_inviter_id, picked_referral))

    await db.commit()
    for acc, _, _ in created:
        await db.refresh(acc)

    # 建立邀请关系 + 提交注册任务
    matrix_manager = InviteMatrixManager()
    register_tasks = []
    for idx, (acc, picked_inviter_id, picked_referral) in enumerate(created):
        if picked_inviter_id is not None:
            await matrix_manager._create_invite_relation(db, picked_inviter_id, acc.id, level=1)
        if data.auto_register:
            task = await create_task(
                account_id=acc.id,
                task_type=TaskType.REGISTER,
                task_name=f"批量注册: {acc.username}",
                task_params={"display_name": acc.username, "referral_code": picked_referral},
                # 错峰调度：瞬间并发注册会触发站点 429 限流（1h 冷却）；
                # 20s 固定间隔的时序指纹仍过于扎堆（实测 60% 账号同一时段注册后遭风控清理），
                # 改为 60-120s 随机间隔打散
                scheduled_at=now + timedelta(seconds=idx * random.uniform(60, 120)),
                max_retries=data.max_retries
            )
            register_tasks.append(task.id)

    await db.commit()

    return {
        "message": f"Created {len(created)} accounts",
        "accounts": [
            {"id": acc.id, "username": acc.username, "inviter_id": picked_inviter_id}
            for acc, picked_inviter_id, _ in created
        ],
        "register_task_ids": register_tasks
    }


@app.post("/api/accounts/batch/login")
async def batch_login_accounts(data: BatchLogin, db: AsyncSession = Depends(get_db)):
    """批量提交登录任务"""
    if not data.account_ids:
        raise HTTPException(status_code=400, detail="account_ids is empty")
    if len(data.account_ids) > 100:
        raise HTTPException(status_code=400, detail="account_ids max 100")

    # 校验账号存在
    result = await db.execute(select(Account.id).where(Account.id.in_(data.account_ids)))
    valid_ids = {row[0] for row in result.all()}
    missing = set(data.account_ids) - valid_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Accounts not found: {sorted(missing)}")

    now = datetime.now()
    task_ids = []
    for acc_id in data.account_ids:
        task = await create_task(
            account_id=acc_id,
            task_type=TaskType.LOGIN,
            task_name=f"批量登录: account#{acc_id}",
            scheduled_at=now
        )
        task_ids.append(task.id)
    await db.commit()

    return {"message": f"Created {len(task_ids)} login tasks", "task_ids": task_ids}

@app.put("/api/accounts/{account_id}")
async def update_account(
    account_id: int,
    update_data: AccountUpdate,
    db: AsyncSession = Depends(get_db)
):
    """更新账号信息"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    update_dict = update_data.dict(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(account, key, value)

    account.updated_at = datetime.now()
    await db.commit()

    return {"message": "Account updated successfully"}

@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """删除账号"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    account.status = 0  # 标记为禁用
    account.updated_at = datetime.now()
    await db.commit()

    return {"message": "Account deleted successfully"}

@app.post("/api/accounts/{account_id}/login")
async def trigger_account_login(account_id: int, db: AsyncSession = Depends(get_db)):
    """触发账号登录任务"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    task = await create_task(
        account_id=account_id,
        task_type=TaskType.LOGIN,
        task_name=f"登录账号: {account.username}"
    )

    return {"task_id": task.id, "message": "Login task created successfully"}

@app.get("/api/accounts/{account_id}/invite-tree")
async def get_account_invite_tree(account_id: int, max_depth: int = 3):
    """获取账号邀请树"""
    try:
        matrix_manager = InviteMatrixManager()
        tree = await matrix_manager.get_invite_tree(account_id, max_depth)
        return tree
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# -------------------------- 任务管理接口 --------------------------
@app.get("/api/tasks")
async def get_tasks(
    page: int = 1,
    page_size: int = 20,
    status: Optional[int] = None,
    account_id: Optional[int] = None,
    task_type: Optional[int] = None,
    limit: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """获取任务列表"""
    query = select(Task)

    if status is not None:
        query = query.where(Task.status == status)
    if account_id is not None:
        query = query.where(Task.account_id == account_id)
    if task_type is not None:
        query = query.where(Task.task_type == task_type)

    query = query.order_by(Task.created_at.desc())

    # 如果指定了limit，优先使用limit
    if limit is not None:
        query = query.limit(limit)
    else:
        # 分页
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    tasks = list(result.scalars().all())

    # 计算总数
    count_query = select(func.count(Task.id))
    if status is not None:
        count_query = count_query.where(Task.status == status)
    if account_id is not None:
        count_query = count_query.where(Task.account_id == account_id)
    if task_type is not None:
        count_query = count_query.where(Task.task_type == task_type)

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    return {
        "tasks": [
            {
                "id": task.id,
                "account_id": task.account_id,
                "task_type": task.task_type,
                "task_name": task.task_name,
                "status": task.status,
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
                "error_msg": task.error_msg,
                "scheduled_at": task.scheduled_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "created_at": task.created_at
            } for task in tasks
        ],
        "total": total,
        "page": page if limit is None else 1,
        "page_size": page_size if limit is None else limit
    }

# 注意：/api/tasks/statistics 必须定义在 /api/tasks/{task_id} 之前，
# 否则 "statistics" 会先匹配到 {task_id} 路由导致 422 错误
@app.get("/api/tasks/statistics")
async def get_task_statistics(db: AsyncSession = Depends(get_db)):
    """获取任务统计数据"""
    # 按状态统计
    status_result = await db.execute(
        select(
            Task.status,
            func.count(Task.id).label("count")
        )
        .group_by(Task.status)
    )

    status_stats = {row.status: row.count for row in status_result.all()}

    # 按类型统计
    type_result = await db.execute(
        select(
            Task.task_type,
            func.count(Task.id).label("count")
        )
        .group_by(Task.task_type)
    )

    type_stats = {row.task_type: row.count for row in type_result.all()}

    return {
        "status": status_stats,
        "type": type_stats,
        "total": sum(status_stats.values()) if status_stats else 0
    }

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """获取任务详情"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "id": task.id,
        "account_id": task.account_id,
        "task_type": task.task_type,
        "task_name": task.task_name,
        "task_params": json.loads(task.task_params) if task.task_params else None,
        "status": task.status,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "error_msg": task.error_msg,
        "result": json.loads(task.result) if task.result else None,
        "scheduled_at": task.scheduled_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_at": task.created_at
    }

@app.post("/api/tasks")
async def create_new_task(task_data: TaskCreate):
    """创建新任务"""
    task = await create_task(
        account_id=task_data.account_id,
        task_type=task_data.task_type,
        task_name=task_data.task_name,
        task_params=task_data.task_params,
        scheduled_at=task_data.scheduled_at,
        max_retries=task_data.max_retries
    )

    return {"task_id": task.id, "message": "Task created successfully"}

@app.post("/api/tasks/batch")
async def create_batch_tasks(batch_data: BatchTaskCreate):
    """批量创建任务"""
    tasks = await batch_create_tasks(
        account_ids=batch_data.account_ids,
        task_type=batch_data.task_type,
        task_name=batch_data.task_name,
        task_params=batch_data.task_params,
        scheduled_at=batch_data.scheduled_at,
        max_retries=batch_data.max_retries
    )

    return {
        "task_ids": [task.id for task in tasks],
        "count": len(tasks),
        "message": "Batch tasks created successfully"
    }

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """取消任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")

    task.status = TaskStatus.CANCELLED
    task.updated_at = datetime.now()
    await db.commit()

    return {"message": "Task cancelled successfully"}

# -------------------------- 邀请矩阵接口 --------------------------
@app.post("/api/invite/generate-matrix")
async def generate_invite_matrix(matrix_data: MatrixGenerate):
    """生成平衡邀请矩阵"""
    try:
        matrix_manager = InviteMatrixManager()
        relations = await matrix_manager.generate_balanced_matrix(
            new_account_ids=matrix_data.new_account_ids,
            root_inviter_id=matrix_data.root_inviter_id
        )

        return {
            "relations_count": len(relations),
            "message": "Invite matrix generated successfully"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/invite/relations")
async def get_invite_relations(db: AsyncSession = Depends(get_db)):
    """获取全部邀请关系列表 + 邀请树，供邀请矩阵页面渲染"""
    result = await db.execute(
        select(
            InviteRelation,
            Account.username.label("inviter_name"),
            Account.invite_code.label("inviter_code"),
        )
        .join(Account, Account.id == InviteRelation.inviter_id)
        .order_by(InviteRelation.level.asc(), InviteRelation.id.asc())
    )
    rows = result.all()

    # 被邀请人用户名（一次查出避免 N+1）
    invitee_ids = [r.InviteRelation.invitee_id for r in rows]
    name_map: dict = {}
    if invitee_ids:
        res = await db.execute(
            select(Account.id, Account.username).where(Account.id.in_(invitee_ids))
        )
        name_map = {aid: uname for aid, uname in res.all()}

    relations = []
    children: dict = {}
    for r in rows:
        rel = r.InviteRelation
        inviter_label = f"{r.inviter_name} (#{rel.inviter_id})"
        invitee_name = name_map.get(rel.invitee_id, f"#{rel.invitee_id}")
        item = {
            "id": rel.id,
            "inviter_id": rel.inviter_id,
            "inviter_name": r.inviter_name,
            "invitee_id": rel.invitee_id,
            "invitee_name": invitee_name,
            "level": rel.level,
            "reward_rate": rel.reward_rate,
            "total_reward": rel.total_reward,
            "created_at": rel.created_at,
        }
        relations.append(item)
        children.setdefault(rel.inviter_id, []).append(item)

    # 构建邀请树：节点 = 邀请人，子节点为其直接邀请的账号
    def build_node(account_id: int, name: str, depth: int = 0) -> dict:
        return {
            "id": account_id,
            "name": name,
            "children": [
                build_node(c["invitee_id"], c["invitee_name"], depth + 1)
                for c in children.get(account_id, [])
            ],
        }

    # 根节点：没有任何人邀请它的邀请人
    all_inviters = {r.InviteRelation.inviter_id: r.inviter_name for r in rows}
    invited_set = {r.InviteRelation.invitee_id for r in rows}
    roots = [aid for aid in all_inviters if aid not in invited_set]
    tree = [
        build_node(aid, all_inviters[aid])
        for aid in sorted(roots, key=lambda x: (children.get(x, [{}])[0].get("level", 0), x))
    ] if roots else []

    avg_level = sum(r["level"] for r in relations) / len(relations) if relations else 0

    return {"relations": relations, "tree": tree, "avg_level": round(avg_level, 2)}


@app.get("/api/invite/statistics")
async def get_invite_statistics(account_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    """获取邀请统计"""
    if account_id is None:
        # 全局统计
        total_result = await db.execute(select(func.count(InviteRelation.id)))
        total_invites = total_result.scalar_one()

        reward_result = await db.execute(select(func.sum(InviteRelation.total_reward)))
        total_reward = reward_result.scalar_one() or 0

        return {
            "total_invites": total_invites,
            "total_reward": total_reward
        }
    else:
        # 单个账号统计
        try:
            matrix_manager = InviteMatrixManager()
            stats = await matrix_manager.get_invite_statistics(account_id)
            return stats
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/invite/bind")
async def bind_invite_relation(
    inviter_id: int,
    invitee_id: int,
    level: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """手动绑定邀请关系"""
    try:
        matrix_manager = InviteMatrixManager()
        relation = await matrix_manager._create_invite_relation(db, inviter_id, invitee_id, level)
        return {"relation_id": relation.id, "message": "Invite relation bound successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# -------------------------- 系统配置接口 --------------------------
@app.get("/api/config")
async def get_config(db: AsyncSession = Depends(get_db)):
    """获取系统配置"""
    result = await db.execute(select(Setting).order_by(Setting.key.asc()))
    settings = list(result.scalars().all())

    return {
        "settings": [
            {
                "key": setting.key,
                "value": setting.value,
                "description": setting.description,
                "updated_at": setting.updated_at
            } for setting in settings
        ]
    }

@app.put("/api/config")
async def update_config(config_data: ConfigUpdate, db: AsyncSession = Depends(get_db)):
    """更新系统配置"""
    result = await db.execute(select(Setting).where(Setting.key == config_data.key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = config_data.value
        if config_data.description is not None:
            setting.description = config_data.description
        setting.updated_at = datetime.now()
    else:
        setting = Setting(
            key=config_data.key,
            value=config_data.value,
            description=config_data.description
        )
        db.add(setting)

    await db.commit()
    return {"message": "Config updated successfully"}

@app.get("/api/config/health")
async def health_check():
    """系统健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# -------------------------- 统计接口 --------------------------
@app.get("/api/statistics/overview")
async def get_overview_statistics(db: AsyncSession = Depends(get_db)):
    """获取总览统计数据"""
    # 总账号数
    account_result = await db.execute(select(func.count(Account.id)))
    total_accounts = account_result.scalar_one()

    # 账号状态分布
    account_status_result = await db.execute(
        select(Account.status, func.count(Account.id).label("count"))
        .group_by(Account.status)
    )
    account_status = {row.status: row.count for row in account_status_result.all()}

    # 挖矿状态分布
    mining_result = await db.execute(
        select(Account.mining_status, func.count(Account.id).label("count"))
        .group_by(Account.mining_status)
    )
    mining_stats = {row.mining_status: row.count for row in mining_result.all()}

    # 任务状态分布
    task_status_result = await db.execute(
        select(Task.status, func.count(Task.id).label("count"))
        .group_by(Task.status)
    )
    task_status = {row.status: row.count for row in task_status_result.all()}

    # 24小时任务成功率
    day_ago = datetime.now() - timedelta(hours=24)
    success_result = await db.execute(
        select(func.count(Task.id)).where(Task.status == TaskStatus.SUCCESS, Task.finished_at >= day_ago)
    )
    success_24h = success_result.scalar_one()

    failed_result = await db.execute(
        select(func.count(Task.id)).where(Task.status == TaskStatus.FAILED, Task.finished_at >= day_ago)
    )
    failed_24h = failed_result.scalar_one()

    finished_24h = success_24h + failed_24h
    success_rate_24h = round(success_24h / finished_24h * 100, 1) if finished_24h > 0 else None

    # 总积分
    points_result = await db.execute(select(func.sum(Account.total_points)))
    total_points = points_result.scalar_one() or 0

    # 总邀请数
    invite_result = await db.execute(select(func.count(InviteRelation.id)).where(InviteRelation.level == 1))
    total_invites = invite_result.scalar_one()

    return {
        "total_accounts": total_accounts,
        "active_accounts": account_status.get(1, 0),
        "abnormal_accounts": account_status.get(2, 0),
        "disabled_accounts": account_status.get(0, 0),
        "mining_active": mining_stats.get(1, 0),
        "mining_claimable": mining_stats.get(2, 0),
        "pending_tasks": task_status.get(TaskStatus.PENDING, 0),
        "running_tasks": task_status.get(TaskStatus.RUNNING, 0),
        "total_points": total_points,
        "total_invites": total_invites,
        "success_24h": success_24h,
        "failed_24h": failed_24h,
        "success_rate_24h": success_rate_24h
    }

@app.get("/api/statistics/points-trend")
async def get_points_trend(days: int = 7, db: AsyncSession = Depends(get_db)):
    """获取积分趋势数据（按账号创建日期累计 total_points）"""
    days = max(1, min(days, 90))
    today = datetime.now().date()
    start_day = today - timedelta(days=days - 1)

    result = await db.execute(select(Account.created_at, Account.total_points))
    rows = result.all()

    # 窗口前的存量积分作为基线，窗口内按账号创建日期累加
    baseline = 0.0
    points_by_day: dict = {}
    for created_at, total_points in rows:
        if created_at is None:
            continue
        day = created_at.date()
        if day < start_day:
            baseline += total_points or 0
        else:
            day = min(day, today)
            points_by_day[day] = points_by_day.get(day, 0) + (total_points or 0)

    labels, trend = [], []
    running = baseline
    for i in range(days):
        day = start_day + timedelta(days=i)
        running += points_by_day.get(day, 0)
        labels.append(day.strftime("%m-%d"))
        trend.append(round(running, 2))

    return {"days": days, "labels": labels, "trend": trend}

@app.get("/api/statistics/task-trend")
async def get_task_trend(days: int = 7, db: AsyncSession = Depends(get_db)):
    """获取近 N 天任务创建/成功数量趋势"""
    days = max(1, min(days, 90))
    today = datetime.now().date()
    start_day = today - timedelta(days=days - 1)

    result = await db.execute(
        select(Task.created_at, Task.status).where(Task.created_at >= datetime.combine(start_day, datetime.min.time()))
    )
    rows = result.all()

    created_by_day: dict = {}
    success_by_day: dict = {}
    for created_at, status in rows:
        day = created_at.date()
        created_by_day[day] = created_by_day.get(day, 0) + 1
        if status == TaskStatus.SUCCESS:
            success_by_day[day] = success_by_day.get(day, 0) + 1

    labels, created, success = [], [], []
    for i in range(days):
        day = start_day + timedelta(days=i)
        labels.append(day.strftime("%m-%d"))
        created.append(created_by_day.get(day, 0))
        success.append(success_by_day.get(day, 0))

    return {"days": days, "labels": labels, "created": created, "success": success}

@app.get("/api/statistics/mining-rewards")
async def get_mining_rewards(days: int = 7, db: AsyncSession = Depends(get_db)):
    """获取挖矿收益统计"""
    # TODO: 实现挖矿收益统计
    return {"days": days, "rewards": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=settings.web_debug
    )
