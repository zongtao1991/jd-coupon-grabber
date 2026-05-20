"""京东登录 & Cookie 管理"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from core.config import COOKIES_FILE, cfg

logger = logging.getLogger("jd.auth")

LOGIN_URL = "https://passport.jd.com/new/login.aspx"
HOME_URL = "https://www.jd.com"
CHECK_URL = "https://passport.jd.com/user/petName/getUserInfoForMini498.action"

_pw = None
_browser: Browser | None = None
_context: BrowserContext | None = None

# 回调列表：登录状态变化时通知
_status_callbacks: list[Callable] = []


def on_status_change(cb: Callable):
    _status_callbacks.append(cb)


async def _notify(status: str, msg: str = ""):
    for cb in _status_callbacks:
        try:
            await cb(status, msg)
        except Exception:
            pass


async def _ensure_browser() -> BrowserContext:
    global _pw, _browser, _context
    if _context:
        return _context
    _pw = await async_playwright().start()
    launch_args: dict[str, Any] = {"headless": cfg.browser.get("headless", True)}
    _browser = await _pw.chromium.launch(**launch_args)
    _context = await _browser.new_context(
        user_agent=cfg.browser.get("user_agent") or None,
        viewport={"width": 1280, "height": 800},
    )
    # 加载已有 cookie
    await load_cookies()
    return _context


async def close_browser():
    global _pw, _browser, _context
    if _context:
        await _context.close()
        _context = None
    if _browser:
        await _browser.close()
        _browser = None
    if _pw:
        await _pw.stop()
        _pw = None


async def save_cookies():
    ctx = await _ensure_browser()
    cookies = await ctx.cookies()
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info("Cookies saved (%d items)", len(cookies))


async def load_cookies() -> bool:
    global _context
    if not COOKIES_FILE.exists():
        return False
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if cookies and _context:
            await _context.add_cookies(cookies)
            logger.info("Cookies loaded (%d items)", len(cookies))
            return True
    except Exception as e:
        logger.warning("Failed to load cookies: %s", e)
    return False


async def check_login() -> dict:
    """检查当前登录状态，返回 {logged_in: bool, nickname: str}"""
    try:
        ctx = await _ensure_browser()
        page = await ctx.new_page()
        try:
            resp = await page.goto(CHECK_URL, timeout=10000)
            if resp and resp.ok:
                text = await page.content()
                # 如果返回包含 nickName 说明已登录
                if "nickName" in text and "login" not in text.lower():
                    # 尝试提取昵称
                    import re
                    m = re.search(r'"nickName"\s*:\s*"([^"]*)"', text)
                    nick = m.group(1) if m else "JD用户"
                    return {"logged_in": True, "nickname": nick}
        finally:
            await page.close()
    except Exception as e:
        logger.warning("Check login failed: %s", e)
    return {"logged_in": False, "nickname": ""}


async def start_login() -> dict:
    """触发扫码登录流程，返回 {success, message}"""
    await _notify("logging_in", "正在打开登录页面...")
    try:
        ctx = await _ensure_browser()
        page = await ctx.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

        # 切换到扫码登录 tab（如果不是默认）
        qr_tab = page.locator("div.login-tab-r a, .qrcode-login, #kbCoagent")
        if await qr_tab.count() > 0:
            await qr_tab.first.click()
            await asyncio.sleep(1)

        await _notify("waiting_scan", "请在手机京东 App 扫码登录...")

        # 等待登录成功：检测 URL 变化或 cookie 中出现 pt_key
        for _ in range(120):  # 最多等 120 秒
            await asyncio.sleep(1)
            cookies = await ctx.cookies("https://www.jd.com")
            cookie_names = {c["name"] for c in cookies}
            if "pt_key" in cookie_names and "pt_pin" in cookie_names:
                await save_cookies()
                info = await check_login()
                await _notify("logged_in", f"登录成功: {info.get('nickname', '')}")
                await page.close()
                return {"success": True, "message": f"登录成功: {info.get('nickname', '')}"}
            # 也检查 URL 跳转
            if "passport.jd.com" not in page.url:
                await save_cookies()
                await _notify("logged_in", "登录成功（页面跳转）")
                await page.close()
                return {"success": True, "message": "登录成功"}

        await page.close()
        await _notify("login_timeout", "登录超时，请重试")
        return {"success": False, "message": "登录超时（120秒），请重试"}

    except Exception as e:
        logger.exception("Login error")
        await _notify("login_error", str(e))
        return {"success": False, "message": f"登录失败: {e}"}


async def logout():
    """清除登录态"""
    global _context
    if _context:
        await _context.clear_cookies()
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
    await _notify("logged_out", "已退出登录")


async def get_context() -> BrowserContext:
    """获取浏览器上下文（供 grabber 使用）"""
    return await _ensure_browser()
