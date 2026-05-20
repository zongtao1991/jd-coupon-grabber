"""定时任务调度模块"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from core.config import TASKS_FILE, cfg
from core.grabber import grab_coupon_scheduled, _broadcast

logger = logging.getLogger("jd.scheduler")

scheduler = AsyncIOScheduler()

# 任务列表（内存 + 持久化）
_tasks: dict[str, dict] = {}

# 结果回调
_result_callbacks: list[Callable] = []


def on_result(cb: Callable):
    _result_callbacks.append(cb)


async def _notify_result(task_id: str, result: dict):
    for cb in _result_callbacks:
        try:
            await cb(task_id, result)
        except Exception:
            pass


def _load_tasks():
    global _tasks
    if TASKS_FILE.exists():
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _tasks = {t["id"]: t for t in data}
            logger.info("Loaded %d tasks from disk", len(_tasks))
        except Exception as e:
            logger.warning("Failed to load tasks: %s", e)
            _tasks = {}


def _save_tasks():
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(_tasks.values()), f, ensure_ascii=False, indent=2, default=str)


def get_tasks() -> list[dict]:
    return list(_tasks.values())


def get_task(task_id: str) -> dict | None:
    return _tasks.get(task_id)


async def _execute_task(task_id: str):
    """APScheduler 触发的任务执行函数"""
    task = _tasks.get(task_id)
    if not task:
        logger.warning("Task %s not found", task_id)
        return

    task["status"] = "running"
    task["last_run"] = datetime.now().isoformat()
    _save_tasks()

    await _broadcast(f"🚀 任务开始执行: {task['url']}", "info")

    target_time = datetime.fromisoformat(task["scheduled_time"])
    result = await grab_coupon_scheduled(
        url=task["url"],
        target_time=target_time,
        task_id=task_id,
    )

    task["status"] = "completed" if result.get("success") else "failed"
    task["result"] = result.get("message", "")
    task["screenshot"] = result.get("screenshot")
    _save_tasks()

    await _notify_result(task_id, result)
    await _broadcast(f"任务完成: {result.get('message', '')}", "info")


def add_task(url: str, scheduled_time: str, name: str = "") -> dict:
    """添加抢券任务"""
    task_id = uuid.uuid4().hex[:8]
    target_dt = datetime.fromisoformat(scheduled_time)

    # 提前 preheat 秒触发（在 grabber 内部会做精确等待）
    preheat = cfg.grabber.get("preheat_seconds", 3)
    from datetime import timedelta
    trigger_time = target_dt - timedelta(seconds=preheat + 2)

    now = datetime.now()
    if trigger_time <= now:
        # 如果触发时间已过，立即执行
        trigger_time = now

    task = {
        "id": task_id,
        "url": url,
        "name": name or f"任务-{task_id}",
        "scheduled_time": scheduled_time,
        "created_at": now.isoformat(),
        "status": "pending",
        "result": "",
        "screenshot": None,
        "last_run": None,
    }
    _tasks[task_id] = task
    _save_tasks()

    # 注册到 APScheduler
    scheduler.add_job(
        _execute_task,
        trigger=DateTrigger(run_date=trigger_time),
        args=[task_id],
        id=f"grab_{task_id}",
        replace_existing=True,
    )

    logger.info("Task added: %s -> %s at %s", task_id, url, scheduled_time)
    return task


def remove_task(task_id: str) -> bool:
    if task_id not in _tasks:
        return False
    _tasks.pop(task_id)
    _save_tasks()
    try:
        scheduler.remove_job(f"grab_{task_id}")
    except Exception:
        pass
    return True


def update_task(task_id: str, url: str | None = None, scheduled_time: str | None = None, name: str | None = None) -> dict | None:
    task = _tasks.get(task_id)
    if not task:
        return None
    if url:
        task["url"] = url
    if name:
        task["name"] = name
    if scheduled_time:
        task["scheduled_time"] = scheduled_time
        # 重新调度
        target_dt = datetime.fromisoformat(scheduled_time)
        preheat = cfg.grabber.get("preheat_seconds", 3)
        from datetime import timedelta
        trigger_time = target_dt - timedelta(seconds=preheat + 2)
        now = datetime.now()
        if trigger_time <= now:
            trigger_time = now
        try:
            scheduler.remove_job(f"grab_{task_id}")
        except Exception:
            pass
        scheduler.add_job(
            _execute_task,
            trigger=DateTrigger(run_date=trigger_time),
            args=[task_id],
            id=f"grab_{task_id}",
            replace_existing=True,
        )
    _save_tasks()
    return task


async def run_task_now(task_id: str) -> dict | None:
    """手动立即执行任务"""
    task = _tasks.get(task_id)
    if not task:
        return None
    # 直接抢，不做定时等待
    from core.grabber import grab_coupon

    task["status"] = "running"
    task["last_run"] = datetime.now().isoformat()
    _save_tasks()

    result = await grab_coupon(url=task["url"], task_id=task_id)

    task["status"] = "completed" if result.get("success") else "failed"
    task["result"] = result.get("message", "")
    task["screenshot"] = result.get("screenshot")
    _save_tasks()

    await _notify_result(task_id, result)
    return result


def init_scheduler():
    """初始化调度器，加载持久化任务"""
    _load_tasks()
    # 恢复 pending 状态的任务
    now = datetime.now()
    for task in _tasks.values():
        if task["status"] == "pending":
            try:
                target_dt = datetime.fromisoformat(task["scheduled_time"])
                preheat = cfg.grabber.get("preheat_seconds", 3)
                from datetime import timedelta
                trigger_time = target_dt - timedelta(seconds=preheat + 2)
                if trigger_time > now:
                    scheduler.add_job(
                        _execute_task,
                        trigger=DateTrigger(run_date=trigger_time),
                        args=[task["id"]],
                        id=f"grab_{task['id']}",
                        replace_existing=True,
                    )
                    logger.info("Restored scheduled task: %s at %s", task["id"], task["scheduled_time"])
                else:
                    task["status"] = "expired"
            except Exception as e:
                logger.warning("Failed to restore task %s: %s", task["id"], e)
    _save_tasks()
    scheduler.start()
    logger.info("Scheduler started")
