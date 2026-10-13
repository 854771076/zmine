# ZMINE 自动化系统

一个针对 `https://zmine.si/` 网站的全流程自动化系统，支持多账号管理、并发任务执行、平衡邀请矩阵等高级功能。

## 功能特性

### 核心功能
- ✅ **账号管理**：多账号注册、登录、状态管理
- ✅ **任务自动化**：自动完成日常任务、挖矿、领取收益
- ✅ **邀请矩阵**：平衡的邀请树生成、多级收益分成
- ✅ **并发执行**：基于 asyncio 的异步并发任务调度
- ✅ **可视化管理**：Web UI 界面，支持账号监控、任务管理、数据统计
- ✅ **REST API**：完整的 API 接口，支持第三方系统集成
- ✅ **数据持久化**：SQLite 数据库存储，支持多账号数据管理

### 技术栈
- **包管理**：uv
- **Web 框架**：FastAPI (pyweb 兼容)
- **数据验证**：pydantic, pydantic-settings
- **数据库**：SQLite + SQLAlchemy ORM
- **HTTP 客户端**：aiohttp
- **任务调度**：asyncio + tenacity 重试机制
- **反检测**：随机 User-Agent、请求间隔控制、指纹模拟

## 项目结构

```
zmine-automation/
├── src/
│   ├── config/              # 配置模块
│   │   └── settings.py      # 全局配置定义
│   ├── db/                  # 数据库模块
│   │   ├── models.py        # ORM 模型定义
│   │   └── session.py       # 数据库会话管理
│   ├── site/                # 网站交互模块
│   │   └── client.py        # ZMINE API 客户端封装
│   ├── scheduler/           # 任务调度模块
│   │   └── worker.py        # 任务工作器实现
│   ├── invite/              # 邀请矩阵模块
│   │   └── matrix.py        # 邀请关系管理
│   └── web/                 # Web UI 和 API 模块
│       └── app.py           # FastAPI 应用主入口
├── tests/                   # 测试用例
├── js_reverse_cache/        # 逆向分析缓存目录
├── main.py                  # 系统入口文件
├── pyproject.toml           # 项目配置文件
└── README.md                # 项目说明文档
```

## 快速开始

### 1. 安装依赖
```bash
# 使用 uv 安装依赖
uv sync
```

### 2. 初始化数据库
```bash
python main.py init-db
```

### 3. 运行系统
```bash
# 运行完整系统（Web UI + 任务工作器）
python main.py run-all

# 或单独运行各组件：
# 仅运行 Web UI
python main.py run-web --port 8000

# 仅运行任务工作器
python main.py run-worker --concurrency 10
```

### 4. 访问管理界面
打开浏览器访问：`http://localhost:8000`

## 功能模块说明

### 账号管理
- 支持多账号批量创建和管理
- 自动保存登录状态（Cookie/Token）
- 账号状态监控（正常、禁用、异常）
- 积分和挖矿状态实时同步

### 任务系统
- 支持的任务类型：登录、完成任务、挖矿、领取收益、更新资料
- 异步并发执行，支持并发数控制
- 指数退避重试机制，提高任务成功率
- 任务状态实时跟踪，失败自动重试
- 支持定时任务调度

### 邀请矩阵
- 自动生成平衡的邀请树结构
- 支持多级邀请关系管理（默认2级）
- 邀请收益自动计算和发放
- 反作弊检测机制
- 邀请关系可视化展示

### 反检测机制
- 随机 User-Agent 轮换
- 请求间隔随机化，避免请求过快
- 支持代理池配置
- 浏览器指纹模拟

## API 接口

### 账号管理
- `GET /api/accounts` - 获取账号列表
- `POST /api/accounts` - 创建新账号
- `GET /api/accounts/{id}` - 获取账号详情
- `PUT /api/accounts/{id}` - 更新账号信息
- `DELETE /api/accounts/{id}` - 删除账号
- `POST /api/accounts/{id}/login` - 触发账号登录任务

### 任务管理
- `GET /api/tasks` - 获取任务列表
- `POST /api/tasks` - 创建新任务
- `POST /api/tasks/batch` - 批量创建任务
- `GET /api/tasks/{id}` - 获取任务详情
- `POST /api/tasks/{id}/cancel` - 取消任务

### 邀请管理
- `POST /api/invite/generate-matrix` - 生成平衡邀请矩阵
- `GET /api/invite/statistics` - 获取邀请统计
- `POST /api/invite/bind` - 手动绑定邀请关系

### 系统配置
- `GET /api/config` - 获取系统配置
- `PUT /api/config` - 更新系统配置
- `GET /api/config/health` - 健康检查

## 配置说明

可以通过环境变量或 `.env` 文件修改配置：

```env
# 网站配置
SITE_URL=https://zmine.si
REQUEST_TIMEOUT=30
MAX_RETRIES=3

# 并发配置
MAX_CONCURRENT=10
REQUEST_DELAY_MIN=1.0
REQUEST_DELAY_MAX=3.0

# 数据库配置
DB_PATH=zmine.db

# Web UI配置
WEB_HOST=0.0.0.0
WEB_PORT=8000
WEB_DEBUG=False

# 邀请配置
INVITE_MAX_LEVEL=2
INVITE_REWARD_RATE=0.1
```

## 注意事项

1. **网站接口完善**：当前 API 客户端中的业务方法为占位实现，需要登录网站后捕获真实 API 接口信息后完善。
2. **法律合规**：使用本系统请遵守相关法律法规和网站服务条款，请勿用于非法用途。
3. **风险提示**：自动化操作可能存在账号被封禁的风险，请谨慎使用。
4. **逆向分析**：如需完整的 API 接口信息，可以提供测试账号以便进一步分析。

## 下一步操作

1. 访问网站注册测试账号
2. 使用浏览器开发者工具捕获 API 接口信息
3. 完善 `src/site/client.py` 中的实际 API 路径和参数
4. 测试单账号流程是否正常运行
5. 批量导入账号并配置邀请矩阵

## 许可证

MIT License
