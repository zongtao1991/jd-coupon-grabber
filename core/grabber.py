"""抢券核心引擎"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import ntplib

from core.config import SCREENSHOT_DIR, cfg

logger = logging.getLogger("jd.grabber")

# 日志广播回调
_log_callbacks: list[Callable] = []


def on_log(cb: Callable):
    _log_callbacks.append(cb)


async def _broadcast(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level.upper()}] {msg}"
    logger.log(getattr(logging, level.upper(), logging.INFO), msg)
    for cb in _log_callbacks:
        try:
            await cb(line)
        except Exception:
            pass


def get_ntp_offset() -> float:
    """获取本地时钟与 NTP 服务器的偏移量（秒）"""
    try:
        client = ntplib.NTPClient()
        resp = client.request(cfg.ntp.get("server", "ntp.aliyun.com"), version=3)
        offset = resp.offset
        logger.info("NTP offset: %.4f seconds", offset)
        return offset
    except Exception as e:
        logger.warning("NTP sync failed: %s, using 0 offset", e)
        return 0.0


def precise_time(offset: float = 0.0) -> float:
    """返回校正后的精确时间戳"""
    return time.time() + offset


async def precise_wait_until(target_ts: float, offset: float = 0.0):
    """精确等待到目标时间戳"""
    while True:
        now = precise_time(offset)
        diff = target_ts - now
        if diff <= 0:
            break
        if diff > 1:
            await asyncio.sleep(diff - 0.5)
        elif diff > 0.05:
            await asyncio.sleep(0.01)
        else:
            # 自旋等待最后 50ms
            pass


# 京东领券按钮的常见选择器
COUPON_BUTTON_SELECTORS = [
    # 通用领券按钮
    "a.coupon-btn:not(.disabled)",
    "a.btn-getCoupon",
    "a.btn-receive",
    "div.coupon-btn:not(.disabled)",
    "button.coupon-btn:not(.disabled)",
    # 文本匹配
    "a:has-text('立即领取')",
    "a:has-text('领取')",
    "a:has-text('立即领券')",
    "a:has-text('领券')",
    "a:has-text('马上抢')",
    "a:has-text('立即抢购')",
    "button:has-text('立即领取')",
    "button:has-text('领取')",
    "button:has-text('领券')",
    "div:has-text('立即领取'):not(:has(div))",
    "span:has-text('立即领取')",
    # 京东活动页常见
    ".coupon-item .btn",
    ".coupon-wrap .btn-get",
    ".sale-coupon .coupon-get",
    "#coupon-list .btn",
]


async def grab_coupon(url: str, task_id: str = "") -> dict:
    """
    执行抢券操作
    返回 {success: bool, message: str, screenshot: str|None}
    """
    from core.auth import get_context

    ntp_offset = 0.0
    if cfg.ntp.get("sync_before_grab", True):
        await _broadcast("NTP 校时中...")
        ntp_offset = get_ntp_offset()
        await _broadcast(f"NTP 偏移: {ntp_offset:.4f}s")

    ctx = await get_context()
    page = await ctx.new_page()
    screenshot_path = None

    try:
        # 1. 预热：打开目标页面
        await _broadcast(f"正在打开目标页面: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(0.5)

        # 等待页面完全加载
        await _broadcast("页面加载完成，等待领券按钮...")
        await asyncio.sleep(1)

        # 2. 查找领券按钮
        retry_count = cfg.grabber.get("retry_count", 3)
        retry_interval = cfg.grabber.get("retry_interval_ms", 200) / 1000

        for attempt in range(1, retry_count + 1):
            await _broadcast(f"第 {attempt}/{retry_count} 次尝试点击领券按钮...")

            clicked = False
            for selector in COUPON_BUTTON_SELECTORS:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await _broadcast(f"找到按钮: {selector}")
                        await btn.click(force=True)
                        clicked = True
                        await _broadcast("✅ 已点击领券按钮！")
                        break
                except Exception:
                    continue

            if clicked:
                # 等待结果
                await asyncio.sleep(1.5)

                # 检查是否成功（通常会弹出提示或按钮变灰）
                page_text = await page.content()
                success_keywords = ["领取成功", "已领取", "已抢到", "恭喜", "成功"]
                fail_keywords = ["已抢光", "已领完", "已结束", "未开始", "来晚了", "手慢了"]

                for kw in success_keywords:
                    if kw in page_text:
                        msg = f"🎉 领券成功！(关键词: {kw})"
                        await _broadcast(msg)
                        screenshot_path = await _take_screenshot(page, task_id, "success")
                        return {"success": True, "message": msg, "screenshot": screenshot_path}

                for kw in fail_keywords:
                    if kw in page_text:
                        msg = f"❌ 领券失败: {kw}"
                        await _broadcast(msg)
                        screenshot_path = await _take_screenshot(page, task_id, "fail")
                        return {"success": False, "message": msg, "screenshot": screenshot_path}

                # 没有明确结果，可能成功了
                msg = "⚠️ 已点击，但无法确认结果，请检查截图"
                await _broadcast(msg)
                screenshot_path = await _take_screenshot(page, task_id, "uncertain")
                return {"success": True, "message": msg, "screenshot": screenshot_path}

            if attempt < retry_count:
                await _broadcast(f"未找到可点击按钮，{retry_interval}s 后重试...")
                await asyncio.sleep(retry_interval)
                # 刷新页面重试
                await page.reload(wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(0.5)

        # 所有重试用完
        msg = "❌ 未找到领券按钮，所有重试已用完"
        await _broadcast(msg)
        screenshot_path = await _take_screenshot(page, task_id, "no_button")
        return {"success": False, "message": msg, "screenshot": screenshot_path}

    except Exception as e:
        msg = f"❌ 抢券异常: {e}"
        await _broadcast(msg, "error")
        try:
            screenshot_path = await _take_screenshot(page, task_id, "error")
        except Exception:
            pass
        return {"success": False, "message": msg, "screenshot": screenshot_path}
    finally:
        await page.close()


async def grab_coupon_scheduled(url: str, target_time: datetime, task_id: str = "") -> dict:
    """
    定时抢券：等待到目标时间，提前预热，到点抢
    """
    ntp_offset = 0.0
    if cfg.ntp.get("sync_before_grab", True):
        await _broadcast("NTP 校时中...")
        ntp_offset = get_ntp_offset()
        await _broadcast(f"NTP 偏移: {ntp_offset:.4f}s")

    from core.auth import get_context

    ctx = await get_context()
    page = await ctx.new_page()
    screenshot_path = None

    try:
        target_ts = target_time.timestamp()
        preheat = cfg.grabber.get("preheat_seconds", 3)
        preheat_ts = target_ts - preheat

        # 等待到预热时间
        now = precise_time(ntp_offset)
        wait_secs = preheat_ts - now
        if wait_secs > 0:
            await _broadcast(f"距离预热还有 {wait_secs:.1f}s，等待中...")
            await precise_wait_until(preheat_ts, ntp_offset)

        # 预热：打开页面
        await _broadcast(f"预热：打开目标页面 {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await _broadcast("页面已加载，等待目标时间...")

        # 精确等待到目标时间
        await precise_wait_until(target_ts, ntp_offset)
        await _broadcast("⏰ 到达目标时间，开始抢券！")

        # 刷新页面（券可能在整点才出现）
        await page.reload(wait_until="domcontentloaded", timeout=10000)
        await asyncio.sleep(0.3)

        # 抢券逻辑
        retry_count = cfg.grabber.get("retry_count", 3)
        retry_interval = cfg.grabber.get("retry_interval_ms", 200) / 1000

        for attempt in range(1, retry_count + 1):
            await _broadcast(f"第 {attempt}/{retry_count} 次尝试...")

            for selector in COUPON_BUTTON_SELECTORS:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await _broadcast(f"找到按钮: {selector}")
                        await btn.click(force=True)
                        await _broadcast("✅ 已点击！")

                        await asyncio.sleep(1.5)
                        page_text = await page.content()

                        success_keywords = ["领取成功", "已领取", "已抢到", "恭喜", "成功"]
                        fail_keywords = ["已抢光", "已领完", "已结束", "来晚了", "手慢了"]

                        for kw in success_keywords:
                            if kw in page_text:
                                msg = f"🎉 领券成功！({kw})"
                                await _broadcast(msg)
                                screenshot_path = await _take_screenshot(page, task_id, "success")
                                return {"success": True, "message": msg, "screenshot": screenshot_path}

                        for kw in fail_keywords:
                            if kw in page_text:
                                msg = f"❌ 领券失败: {kw}"
                                await _broadcast(msg)
                                screenshot_path = await _take_screenshot(page, task_id, "fail")
                                return {"success": False, "message": msg, "screenshot": screenshot_path}

                        msg = "⚠️ 已点击，结果待确认"
                        await _broadcast(msg)
                        screenshot_path = await _take_screenshot(page, task_id, "uncertain")
                        return {"success": True, "message": msg, "screenshot": screenshot_path}
                except Exception:
                    continue

            if attempt < retry_count:
                await _broadcast(f"未找到按钮，{retry_interval}s 后重试...")
                await asyncio.sleep(retry_interval)
                await page.reload(wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(0.3)

        msg = "❌ 未找到领券按钮"
        await _broadcast(msg)
        screenshot_path = await _take_screenshot(page, task_id, "no_button")
        return {"success": False, "message": msg, "screenshot": screenshot_path}

    except Exception as e:
        msg = f"❌ 定时抢券异常: {e}"
        await _broadcast(msg, "error")
        try:
            screenshot_path = await _take_screenshot(page, task_id, "error")
        except Exception:
            pass
        return {"success": False, "message": msg, "screenshot": screenshot_path}
    finally:
        await page.close()


async def _take_screenshot(page, task_id: str, label: str) -> str | None:
    if not cfg.grabber.get("screenshot", True):
        return None
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{task_id}_{label}_{ts}.png" if task_id else f"{label}_{ts}.png"
        path = SCREENSHOT_DIR / filename
        await page.screenshot(path=str(path), full_page=True)
        await _broadcast(f"截图已保存: {filename}")
        return str(path)
    except Exception as e:
        logger.warning("Screenshot failed: %s", e)
        return None
