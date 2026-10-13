import json
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from src.config.settings import settings
from src.db.models import Account, InviteRelation
from src.db.session import get_db

class InviteMatrixManager:
    """邀请矩阵管理器"""

    def __init__(self, max_level: int = 2, reward_rate: float = 0.1):
        self.max_level = max_level or settings.invite_max_level
        self.reward_rate = reward_rate or settings.invite_reward_rate

    async def _get_account_by_id(self, db: AsyncSession, account_id: int) -> Optional[Account]:
        """根据ID获取账号"""
        result = await db.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()

    async def _get_available_inviter(self, db: AsyncSession) -> Optional[Account]:
        """获取可用的邀请人（优先选择邀请人数最少的账号）"""
        # 查询每个账号的直接邀请人数
        subquery = (
            select(
                InviteRelation.inviter_id,
                func.count(InviteRelation.id).label("invitee_count")
            )
            .where(InviteRelation.level == 1)
            .group_by(InviteRelation.inviter_id)
            .subquery()
        )

        # 优先选择邀请人数最少的账号
        result = await db.execute(
            select(Account)
            .outerjoin(subquery, Account.id == subquery.c.inviter_id)
            .where(Account.status == 1)  # 只选择正常状态的账号
            .order_by(func.coalesce(subquery.c.invitee_count, 0).asc(), Account.id.asc())
            .limit(1)
        )

        inviter = result.scalar_one_or_none()

        # 如果没有账号，返回None
        if not inviter:
            return None

        return inviter

    async def _create_invite_relation(
        self,
        db: AsyncSession,
        inviter_id: int,
        invitee_id: int,
        level: int = 1
    ) -> InviteRelation:
        """创建邀请关系"""
        relation = InviteRelation(
            inviter_id=inviter_id,
            invitee_id=invitee_id,
            level=level,
            reward_rate=self.reward_rate
        )
        db.add(relation)

        # 同时更新账号的inviter_id
        await db.execute(
            Account.__table__.update()
            .where(Account.id == invitee_id)
            .values(inviter_id=inviter_id)
        )

        await db.commit()
        await db.refresh(relation)
        return relation

    async def generate_balanced_matrix(
        self,
        new_account_ids: List[int],
        root_inviter_id: Optional[int] = None
    ) -> List[InviteRelation]:
        """
        生成平衡的邀请矩阵
        采用广度优先策略，确保邀请树结构平衡
        """
        relations = []

        async for db in get_db():
            # 如果指定了根邀请人，使用根邀请人
            if root_inviter_id:
                root_inviter = await self._get_account_by_id(db, root_inviter_id)
                if not root_inviter:
                    raise ValueError(f"Root inviter not found: {root_inviter_id}")
            else:
                # 否则自动选择可用的邀请人
                root_inviter = await self._get_available_inviter(db)
                if not root_inviter:
                    # 如果没有任何账号，说明是第一批账号，不需要邀请关系
                    return relations

            # 为每个新账号分配邀请人
            for account_id in new_account_ids:
                # 检查账号是否存在
                account = await self._get_account_by_id(db, account_id)
                if not account:
                    raise ValueError(f"Account not found: {account_id}")

                # 检查账号是否已有邀请人
                if account.inviter_id:
                    continue

                # 获取当前可用的邀请人
                inviter = await self._get_available_inviter(db)
                if not inviter:
                    inviter = root_inviter

                # 创建一级邀请关系
                relation = await self._create_invite_relation(db, inviter.id, account_id, level=1)
                relations.append(relation)

                # 为上级层级创建关系（如果max_level > 1）
                current_inviter = inviter
                for level in range(2, self.max_level + 1):
                    if not current_inviter.inviter_id:
                        break

                    # 创建上级层级的邀请关系
                    await self._create_invite_relation(
                        db,
                        current_inviter.inviter_id,
                        account_id,
                        level=level
                    )

                    # 继续向上追溯
                    current_inviter = await self._get_account_by_id(db, current_inviter.inviter_id)
                    if not current_inviter:
                        break

        return relations

    async def get_invite_tree(self, account_id: int, max_depth: int = 3) -> Dict[str, Any]:
        """获取账号的邀请树"""
        async for db in get_db():
            account = await self._get_account_by_id(db, account_id)
            if not account:
                raise ValueError(f"Account not found: {account_id}")

            # 递归构建邀请树
            async def build_tree(current_id: int, depth: int = 0) -> Dict[str, Any]:
                if depth >= max_depth:
                    return {}

                # 获取直接邀请的账号
                result = await db.execute(
                    select(InviteRelation)
                    .where(
                        InviteRelation.inviter_id == current_id,
                        InviteRelation.level == 1
                    )
                    .order_by(InviteRelation.created_at.asc())
                )
                relations = list(result.scalars().all())

                tree = {
                    "account_id": current_id,
                    "invitee_count": len(relations),
                    "invitees": []
                }

                for rel in relations:
                    invitee_tree = await build_tree(rel.invitee_id, depth + 1)
                    if invitee_tree:
                        tree["invitees"].append(invitee_tree)

                return tree

            return await build_tree(account_id)

    async def calculate_invite_rewards(self, account_id: int) -> Dict[int, float]:
        """计算邀请奖励（按层级）"""
        async for db in get_db():
            result = await db.execute(
                select(
                    InviteRelation.level,
                    func.sum(InviteRelation.total_reward).label("total")
                )
                .where(InviteRelation.inviter_id == account_id)
                .group_by(InviteRelation.level)
                .order_by(InviteRelation.level.asc())
            )

            rewards = {}
            for row in result.all():
                rewards[row.level] = row.total or 0.0

            return rewards

    async def add_invite_reward(
        self,
        invitee_id: int,
        reward_amount: float,
        distribute: bool = True
    ) -> Dict[int, float]:
        """
        添加邀请奖励
        当被邀请人获得收益时，向上级发放邀请奖励
        """
        distributed_rewards = {}

        if reward_amount <= 0:
            return distributed_rewards

        async for db in get_db():
            # 获取被邀请人
            invitee = await self._get_account_by_id(db, invitee_id)
            if not invitee:
                raise ValueError(f"Invitee not found: {invitee_id}")

            # 向上级层级发放奖励
            current_inviter_id = invitee.inviter_id
            current_level = 1

            while current_inviter_id and current_level <= self.max_level:
                # 计算当前层级的奖励
                level_reward = reward_amount * (self.reward_rate / current_level)

                if level_reward > 0:
                    # 更新邀请关系的总奖励
                    await db.execute(
                        InviteRelation.__table__.update()
                        .where(
                            InviteRelation.inviter_id == current_inviter_id,
                            InviteRelation.invitee_id == invitee_id,
                            InviteRelation.level == current_level
                        )
                        .values(total_reward = InviteRelation.total_reward + level_reward)
                    )

                    # 更新邀请人的积分
                    await db.execute(
                        Account.__table__.update()
                        .where(Account.id == current_inviter_id)
                        .values(
                            points = Account.points + level_reward,
                            total_points = Account.total_points + level_reward
                        )
                    )

                    distributed_rewards[current_level] = distributed_rewards.get(current_level, 0) + level_reward

                # 继续向上一级
                inviter = await self._get_account_by_id(db, current_inviter_id)
                if not inviter or not inviter.inviter_id:
                    break

                current_inviter_id = inviter.inviter_id
                current_level += 1

            await db.commit()

        return distributed_rewards

    async def get_invite_statistics(self, account_id: int) -> Dict[str, Any]:
        """获取邀请统计信息"""
        async for db in get_db():
            # 各层级邀请人数
            level_result = await db.execute(
                select(
                    InviteRelation.level,
                    func.count(InviteRelation.id).label("count")
                )
                .where(InviteRelation.inviter_id == account_id)
                .group_by(InviteRelation.level)
                .order_by(InviteRelation.level.asc())
            )

            level_counts = {}
            total_invitees = 0
            for row in level_result.all():
                level_counts[row.level] = row.count
                total_invitees += row.count

            # 各层级奖励
            rewards = await self.calculate_invite_rewards(account_id)
            total_reward = sum(rewards.values())

            return {
                "total_invitees": total_invitees,
                "level_counts": level_counts,
                "total_reward": total_reward,
                "level_rewards": rewards
            }
