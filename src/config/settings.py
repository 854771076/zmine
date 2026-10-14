from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # 网站配置
    site_url: str = "https://zmine.si"
    request_timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 2

    # 并发配置
    max_concurrent: int = 10
    request_delay_min: float = 1.0
    request_delay_max: float = 3.0

    # 任务队列分离（按类型独立并发槽，互不抢占）
    queue_main: int = 25   # 挖矿/领取/同步（快任务）
    queue_mind: int = 5    # Mind答题（慢任务：答题延迟+冷却等待）
    queue_auth: int = 5    # 注册/登录（限流敏感端点）

    # 数据库配置
    db_path: str = "zmine.db"

    # 任务配置
    task_default_priority: int = 5
    task_max_retries: int = 3

    # Web UI配置
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    web_debug: bool = False

    # 邀请配置
    invite_max_level: int = 2
    invite_reward_rate: float = 0.1

    # 反检测配置
    use_random_ua: bool = True
    auto_rotate_proxy: bool = False
    proxy_url: Optional[str] = None

    # 定时任务配置
    mining_interval: int = 60  # 自动挖矿间隔（分钟）
    claim_interval: int = 240  # 自动领取奖励间隔（分钟）
    task_interval: int = 360  # 自动完成任务间隔（分钟）
    sync_interval: int = 120  # 自动同步账号信息间隔（分钟）
    mind_interval: int = 120  # mind答题间隔（分钟，冷却中断后靠下一轮继续完成当日额度）

settings = Settings()