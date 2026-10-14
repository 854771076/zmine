#!/usr/bin/env python3
"""
ZMINE Automation System
自动化系统入口文件
"""
import asyncio
import argparse
import sys
from src.config.settings import settings
from src.db.session import init_db, engine
from src.scheduler.worker import TaskWorker

def main():
    parser = argparse.ArgumentParser(description="ZMINE Automation System")
    parser.add_argument(
        "command",
        choices=["init-db", "run-worker", "run-web", "run-scheduler", "run-all"],
        help="Command to execute"
    )
    parser.add_argument(
        "--host",
        default=settings.web_host,
        help="Web server host (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.web_port,
        help="Web server port (default: 8000)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.max_concurrent,
        help="Max concurrent tasks (default: 10)"
    )

    args = parser.parse_args()

    if args.command == "init-db":
        print("Initializing database...")
        asyncio.run(init_db())
        print("Database initialized successfully!")

    elif args.command == "run-worker":
        print(f"Starting task worker with concurrency: {args.concurrency}")
        worker = TaskWorker(max_concurrent=args.concurrency)
        try:
            asyncio.run(worker.run())
        except KeyboardInterrupt:
            worker.stop()
            print("Worker stopped.")

    elif args.command == "run-scheduler":
        print("Starting scheduled task manager...")
        from src.scheduler.scheduled import ScheduledTaskManager
        scheduler = ScheduledTaskManager()
        try:
            asyncio.run(scheduler.run())
        except KeyboardInterrupt:
            scheduler.stop()
            print("Scheduler stopped.")

    elif args.command == "run-web":
        print(f"Starting web server on {args.host}:{args.port}")
        import uvicorn
        uvicorn.run(
            "src.web.app:app",
            host=args.host,
            port=args.port,
            reload=settings.web_debug
        )

    elif args.command == "run-all":
        print("Starting full system (web + worker + scheduler)...")

        async def run_all():
            # 初始化数据库
            await init_db()

            # 启动任务工作器
            worker = TaskWorker(max_concurrent=args.concurrency)
            worker_task = asyncio.create_task(worker.run())

            # 启动定时任务管理器
            from src.scheduler.scheduled import ScheduledTaskManager
            scheduler = ScheduledTaskManager()
            scheduler_task = asyncio.create_task(scheduler.run())

            # 启动web服务器
            import uvicorn
            config = uvicorn.Config(
                "src.web.app:app",
                host=args.host,
                port=args.port,
                reload=settings.web_debug
            )
            server = uvicorn.Server(config)
            server_task = asyncio.create_task(server.serve())

            try:
                await asyncio.gather(worker_task, scheduler_task, server_task)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                # 统一收尾：先取消协程，再在事件循环存活时释放数据库连接池。
                # 缺少 engine.dispose() 时，池中 aiosqlite 连接会被拖到解释器退出
                # 阶段强拆，触发 do_terminate 的 CancelledError 崩溃日志
                worker.stop()
                scheduler.stop()
                for t in (worker_task, scheduler_task, server_task):
                    t.cancel()
                await asyncio.gather(worker_task, scheduler_task, server_task, return_exceptions=True)
                await engine.dispose()
                print("System stopped.")

        asyncio.run(run_all())

if __name__ == "__main__":
    main()
