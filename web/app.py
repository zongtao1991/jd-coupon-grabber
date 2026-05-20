"""FastAPI 应用 & WebSocket"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from core import auth, scheduler
from core.config import SCREENSHOT_DIR, cfg
from core.grabber import on_log

logger = logging.getLogger("jd.web")

# WebSocket 连接管理
_ws_clients: set[WebSocket] = set()


async def _ws_broadcast(msg: str):
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead


# 注册日志回调
on_log(lambda line: _ws_broadcast(json.dumps({"type": "log", "data": line})))

# 注册登录状态回调
auth.on_status_change(
    lambda status, msg: _ws_broadcast(json.dumps({"type": "auth", "status": status, "message": msg}))
)

# 注册任务结果回调
scheduler.on_result(
    lambda tid, res: _ws_broadcast(json.dumps({"type": "result", "task_id": tid, "data": res}))
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.init_scheduler()
    yield
    scheduler.scheduler.shutdown(wait=False)
    await auth.close_browser()


app = FastAPI(title="JD Coupon Grabber", lifespan=lifespan)

# --- 前端页面 ---
HTML_PATH = Path(__file__).parent / "index.html"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_PATH.read_text(encoding="utf-8"))


# --- 认证 API ---
@app.get("/api/status")
async def get_status():
    info = await auth.check_login()
    tasks = scheduler.get_tasks()
    return {
        "auth": info,
        "tasks_count": len(tasks),
        "pending_count": sum(1 for t in tasks if t["status"] == "pending"),
    }


@app.post("/api/login")
async def login():
    result = await auth.start_login()
    return result


@app.get("/api/login/status")
async def login_status():
    return await auth.check_login()


@app.post("/api/logout")
async def logout():
    await auth.logout()
    return {"success": True}


# --- 任务 API ---
@app.get("/api/tasks")
async def list_tasks():
    return scheduler.get_tasks()


@app.post("/api/tasks")
async def create_task(body: dict):
    url = body.get("url", "").strip()
    scheduled_time = body.get("scheduled_time", "").strip()
    name = body.get("name", "").strip()
    if not url:
        return JSONResponse({"error": "URL 不能为空"}, status_code=400)
    if not scheduled_time:
        return JSONResponse({"error": "执行时间不能为空"}, status_code=400)
    task = scheduler.add_task(url=url, scheduled_time=scheduled_time, name=name)
    await _ws_broadcast(json.dumps({"type": "task_added", "data": task}))
    return task


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    ok = scheduler.remove_task(task_id)
    if not ok:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    await _ws_broadcast(json.dumps({"type": "task_removed", "task_id": task_id}))
    return {"success": True}


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, body: dict):
    task = scheduler.update_task(
        task_id,
        url=body.get("url"),
        scheduled_time=body.get("scheduled_time"),
        name=body.get("name"),
    )
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return task


@app.post("/api/tasks/{task_id}/run")
async def run_task(task_id: str):
    result = await scheduler.run_task_now(task_id)
    if result is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return result


@app.get("/api/logs")
async def get_logs():
    tasks = scheduler.get_tasks()
    logs = []
    for t in tasks:
        if t.get("result"):
            logs.append({
                "task_id": t["id"],
                "name": t["name"],
                "url": t["url"],
                "time": t.get("last_run", ""),
                "status": t["status"],
                "result": t["result"],
                "screenshot": t.get("screenshot"),
            })
    return logs


# --- 截图访问 ---
@app.get("/api/screenshots/{filename}")
async def get_screenshot(filename: str):
    path = SCREENSHOT_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "截图不存在"}, status_code=404)
    return FileResponse(path)


# --- WebSocket ---
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_ws_clients))
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_ws_clients))
