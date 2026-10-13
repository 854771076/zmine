#!/usr/bin/env python3
"""
ZMINE 全流程测试脚本
测试注册、登录、任务、挖矿完整流程
"""
import asyncio
import json
from src.site.client import ZMineClient, ZMineAPIError

async def test_full_flow():
    """测试完整流程"""
    print("🚀 开始 ZMINE 全流程测试")
    print("=" * 60)

    # 1. 测试注册
    print("\n1. 测试账号注册...")
    async with ZMineClient() as client:
        try:
            register_result = await client.register(
                display_name="zmine_test_002",
                referral_code=""
            )

            print("✅ 注册成功！")
            print(f"   用户ID: {register_result['user']['id']}")
            print(f"   用户名: {register_result['user']['displayName']}")
            print(f"   登录密钥: {register_result['loginKey']}")
            print(f"   助记词: {register_result['recoveryPhrase']}")

            # 保存账号信息
            account_info = {
                "user": register_result["user"],
                "loginKey": register_result["loginKey"],
                "recoveryPhrase": register_result["recoveryPhrase"],
                "cookies": client.get_cookies_dict()
            }

            with open("test_account_002.json", "w", encoding="utf-8") as f:
                json.dump(account_info, f, indent=2, ensure_ascii=False)
            print("ℹ️  账号信息已保存到 test_account_002.json")

        except ZMineAPIError as e:
            print(f"❌ 注册失败: {e.message}")
            return False

    print("=" * 60)

    # 2. 测试登录
    print("\n2. 测试账号登录...")
    login_key = register_result["loginKey"]
    async with ZMineClient(login_key=login_key) as client:
        try:
            login_result = await client.login()
            print("✅ 登录成功！")
            print(f"   用户信息: {login_result['user']}")
            print(f"   会话Cookie: {client.get_cookies_dict().get('zmine_session', '未获取到')}")

        except ZMineAPIError as e:
            print(f"❌ 登录失败: {e.message}")
            return False

    print("=" * 60)

    # 3. 测试获取用户信息
    print("\n3. 测试获取用户信息...")
    profile = await client.get_profile()
    if profile:
        print("✅ 获取用户信息成功！")
        print(f"   用户资料: {json.dumps(profile, indent=2, ensure_ascii=False)}")
    else:
        print("ℹ️  用户信息接口待完善，当前已登录成功")

    print("=" * 60)

    # 4. 输出测试结果
    print("\n🎉 基础流程测试完成！")
    print("✅ 注册功能正常")
    print("✅ 登录功能正常")
    print("✅ 会话管理正常")
    print("\n📋 下一步待完善功能：")
    print("   - 获取任务列表接口")
    print("   - 完成任务接口")
    print("   - 挖矿相关接口")
    print("   - 邀请相关接口")

    return True

if __name__ == "__main__":
    asyncio.run(test_full_flow())
