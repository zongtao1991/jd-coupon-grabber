"""JD Coupon Grabber — 入口"""

import logging
import sys

import uvicorn

from core.config import cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)

if __name__ == "__main__":
    host = cfg.server.get("host", "0.0.0.0")
    port = cfg.server.get("port", 8080)
    print(f"\n🎫 JD Coupon Grabber 启动中...")
    print(f"   地址: http://localhost:{port}")
    print(f"   配置: headless={cfg.browser.get('headless')}")
    print()
    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
