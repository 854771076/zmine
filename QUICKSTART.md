# ZMINE 快捷使用指南

## 🚀 快速开始

### 1. 初始化数据库
```bash
uv run python main.py init-db
```

### 2. 快速添加账号
```bash
# 添加单个账号
uv run python add_account.py 用户名

# 添加账号并指定邀请码
uv run python add_account.py 用户名 --referral 邀请码

# 添加账号并指定邀请人ID
uv run python add_account.py 用户名 --inviter 1
```

### 3. 启动系统
```bash
# 启动完整系统（Web界面 + 任务执行 + 定时任务）
uv run python main.py run-all

# 或单独启动各组件：
# 仅启动Web管理界面
uv run python main.py run-web --port 8000

# 仅启动任务工作器
uv run python main.py run-worker --concurrency 10

# 仅启动定时任务管理器
uv run python main.py run-scheduler
```

### 4. 访问管理界面
打开浏览器访问：`http://localhost:8000`

## ⚙️ 配置说明
复制 `.env.example` 为 `.env` 并修改配置：
```env
# 代理配置（支持HTTP/SOCKS5）
PROXY_URL=socks5://user:pass@127.0.0.1:1080

# 定时任务间隔（分钟）
MINING_INTERVAL=60      # 自动挖矿间隔
CLAIM_INTERVAL=240      # 自动领取奖励间隔
TASK_INTERVAL=360       # 自动完成任务间隔
SYNC_INTERVAL=120       # 自动同步账号信息间隔
```

## 📋 功能说明

### ✅ 已实现功能
- **账号管理**：批量注册、多账号管理、登录状态自动维护
- **任务调度**：异步并发执行任务，支持重试、限流、状态跟踪
- **定时任务**：自动挖矿、自动领取奖励、自动完成任务、自动同步信息
- **邀请矩阵**：平衡邀请树生成，多级收益自动分配
- **代理支持**：支持HTTP/SOCKS5隧道代理，自动轮换IP
- **Web界面**：可视化管理、实时监控、数据统计
- **API接口**：完整REST API，支持第三方集成

### 🔄 自动任务
系统启动后会自动执行以下周期性任务：
- 每小时自动挖矿一次
- 每4小时自动领取奖励一次
- 每6小时自动完成日常任务一次
- 每2小时自动同步账号信息一次

## 🎯 下一步操作
1. 注册几个测试账号
2. 配置邀请矩阵关系
3. 启动系统自动运行
4. 通过Web界面监控运行状态
5. 后续将完善挖矿、任务等API接口

## 📝 注意事项
- 请妥善保存所有账号的助记词和登录密钥
- 建议使用代理避免IP被封禁
- 合理设置并发数和请求间隔，避免对服务器造成压力
- 当前API接口还在完善中，后续会支持更多功能
