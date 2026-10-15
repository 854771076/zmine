from datetime import datetime
from typing import Optional, List
from sqlalchemy import ForeignKey, String, Float, Integer, DateTime, Text, Boolean
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(AsyncAttrs, DeclarativeBase):
    pass

# 注意：统一用本地时间 datetime.now()。SQLite 的 func.now()/CURRENT_TIMESTAMP 是 UTC，
# 与 worker 代码里的 datetime.now()（本地）混用会导致同一表内时间相差时区偏移
def _now() -> datetime:
    return datetime.now()

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    invite_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    inviter_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"))
    token: Mapped[Optional[str]] = mapped_column(Text)
    cookies: Mapped[Optional[str]] = mapped_column(Text)  # JSON格式存储
    points: Mapped[float] = mapped_column(Float, default=0.0)
    total_points: Mapped[float] = mapped_column(Float, default=0.0)
    mining_status: Mapped[int] = mapped_column(Integer, default=0)  # 0未开始, 1挖矿中, 2可领取
    last_mining_claim: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[int] = mapped_column(Integer, default=1)  # 0禁用, 1正常, 2异常
    proxy_ip: Mapped[Optional[str]] = mapped_column(String(64))  # 注册时的代理出口 IP（风控溯源用）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now(), onupdate=_now())

    # 关系
    inviter: Mapped[Optional["Account"]] = relationship("Account", remote_side=[id], back_populates="invitees")
    invitees: Mapped[List["Account"]] = relationship("Account", back_populates="inviter")
    invite_relations_as_inviter: Mapped[List["InviteRelation"]] = relationship(
        "InviteRelation",
        foreign_keys="InviteRelation.inviter_id",
        back_populates="inviter"
    )
    invite_relations_as_invitee: Mapped[List["InviteRelation"]] = relationship(
        "InviteRelation",
        foreign_keys="InviteRelation.invitee_id",
        back_populates="invitee"
    )
    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="account")

class InviteRelation(Base):
    __tablename__ = "invite_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inviter_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    invitee_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 邀请层级
    reward_rate: Mapped[float] = mapped_column(Float, default=0.1)
    total_reward: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now())

    # 关系
    inviter: Mapped["Account"] = relationship(
        "Account",
        foreign_keys=[inviter_id],
        back_populates="invite_relations_as_inviter"
    )
    invitee: Mapped["Account"] = relationship(
        "Account",
        foreign_keys=[invitee_id],
        back_populates="invite_relations_as_invitee"
    )

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    task_type: Mapped[int] = mapped_column(Integer, nullable=False)  # 1注册, 2登录, 3做任务, 4挖矿, 5领取收益
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_params: Mapped[Optional[str]] = mapped_column(Text)  # JSON格式参数
    status: Mapped[int] = mapped_column(Integer, default=0)  # 0等待, 1运行, 2成功, 3失败, 4已取消
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(Text)  # JSON格式结果
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now())

    # 关系
    account: Mapped["Account"] = relationship("Account", back_populates="tasks")

class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now(), onupdate=_now())