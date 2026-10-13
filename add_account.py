#!/usr/bin/env python3
"""
快速添加账号脚本
"""
import asyncio
import argparse
import json
from src.site.client import ZMineClient, ZMineAPIError
from src.db.session import get_db
from src.db.models import Account

async def add_account(username: str, referral_code: str = None, inviter_id: int = None):
    """快速添加新账号"""
    print(f"🚀 开始注册新账号: {username}")

    # 1. 注册账号
    async with ZMineClient() as client:
        try:
            register_result = await client.register(
                display_name=username,
                referral_code=referral_code or ""
            )

            print("✅ 注册成功！")
            print(f"   用户名: {username}")
            print(f"   用户ID: {register_result['user']['id']}")
            print(f"   登录密钥: {register_result['loginKey']}")
            print(f"   助记词: {register_result['recoveryPhrase']}")

            # 2. 保存到数据库
            async for db in get_db():
                account = Account(
                    username=username,
                    password=register_result['loginKey'],  # 存储loginKey作为密码
                    invite_code=register_result['user']['handle'],
                    inviter_id=inviter_id,
                    token=register_result['loginKey'],
                    cookies=json.dumps(client.get_cookies_dict(), ensure_ascii=False),
                    status=1
                )
                db.add(account)
                await db.commit()
                await db.refresh(account)

                print(f"✅ 账号已保存到数据库，ID: {account.id}")

                # 保存到本地文件
                account_file = f"account_{account.id}_{username}.json"
                with open(account_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "id": account.id,
                        "username": username,
                        "user_info": register_result["user"],
                        "loginKey": register_result["loginKey"],
                        "recoveryPhrase": register_result["recoveryPhrase"],
                        "cookies": client.get_cookies_dict()
                    }, f, indent=2, ensure_ascii=False)

                print(f"ℹ️  账号信息已保存到 {account_file}")
                return account.id

        except ZMineAPIError as e:
            print(f"❌ 注册失败: {e.message}")
            return None

def main():
    parser = argparse.ArgumentParser(description="快速添加ZMINE账号")
    parser.add_argument("username", help="用户名")
    parser.add_argument("--referral", "-r", help="邀请码")
    parser.add_argument("--inviter", "-i", type=int, help="邀请人ID")

    args = parser.parse_args()

    asyncio.run(add_account(args.username, args.referral, args.inviter))

if __name__ == "__main__":
    main()
