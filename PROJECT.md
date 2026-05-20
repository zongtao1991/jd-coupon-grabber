# JD Coupon Grabber — 京东优惠券定时抢购平台

## 概述
单账号京东优惠券定时抢购工具，Web 管理面板 + Playwright 浏览器自动化。

## 技术栈
- **后端**: Python 3 + FastAPI + uvicorn
- **前端**: 单文件 HTML（内联 CSS/JS），深色主题，简洁现代
- **浏览器自动化**: Playwright (Chromium)
- **定时**: APScheduler（精确到毫秒级）
- **校时**: NTP 对时（确保本地时钟与服务器同步）

## 功能模块

### 1. Web 管理面板 (`web/`)
- 登录状态显示（已登录/未登录/Cookie 过期）
- 触发京东扫码登录（Playwright 打开登录页，前端展示二维码状态）
- 券链接管理（添加/删除/编辑）
- 定时任务配置（选择日期+时间，默认 00:00:00）
- 抢券日志实时展示（WebSocket 推送）
- 手动触发测试抢券

### 2. 账号管理 (`core/auth.py`)
- Playwright 启动 Chromium，导航到京东登录页
- 等待用户扫码完成登录
- 提取并持久化 Cookie 到本地文件 (`data/cookies.json`)
- Cookie 有效性检查（定期验证是否过期）
- Cookie 加载恢复会话

### 3. 抢券引擎 (`core/grabber.py`)
- 加载 Cookie → 恢复登录态
- 提前 N 秒（默认 3 秒）打开目标券页面预热
- NTP 校时，精确等待到目标时间
- 到点后快速定位并点击领券按钮
- 支持多种京东券页面结构的按钮识别（CSS 选择器 + 文本匹配）
- 重试机制（失败后快速重试 N 次，默认 3 次）
- 结果截图保存 (`data/screenshots/`)

### 4. 定时调度 (`core/scheduler.py`)
- APScheduler 管理定时任务
- 支持添加/删除/暂停/恢复任务
- 任务持久化（重启后恢复）
- 任务执行状态记录

### 5. 配置管理 (`core/config.py`)
- YAML 配置文件 (`config.yaml`)
- 默认配置 + 用户覆盖

## 目录结构
```
jd-coupon-grabber/
├── main.py              # 入口，启动 FastAPI 服务
├── config.yaml          # 配置文件
├── requirements.txt     # 依赖
├── core/
│   ├── __init__.py
│   ├── auth.py          # 登录 & Cookie 管理
│   ├── grabber.py       # 抢券核心引擎
│   ├── scheduler.py     # 定时任务调度
│   └── config.py        # 配置加载
├── web/
│   ├── __init__.py
│   ├── app.py           # FastAPI 路由 & WebSocket
│   └── index.html       # 前端单页面
├── data/
│   ├── cookies.json     # Cookie 持久化
│   ├── tasks.json       # 任务持久化
│   └── screenshots/     # 抢券截图
└── PROJECT.md
```

## API 设计

### REST API
- `GET /` — 前端页面
- `GET /api/status` — 登录状态 & 系统状态
- `POST /api/login` — 触发扫码登录
- `GET /api/login/status` — 轮询登录状态
- `POST /api/logout` — 退出登录（清除 Cookie）
- `GET /api/tasks` — 任务列表
- `POST /api/tasks` — 创建抢券任务（url, scheduled_time）
- `DELETE /api/tasks/{id}` — 删除任务
- `PUT /api/tasks/{id}` — 编辑任务
- `POST /api/tasks/{id}/run` — 手动执行
- `GET /api/logs` — 历史日志

### WebSocket
- `ws://host:port/ws` — 实时日志推送

## 前端设计
- 深色主题（#1a1a2e 底色）
- 顶部：登录状态卡片 + 扫码登录按钮
- 中部：任务列表（券链接、执行时间、状态、操作）
- 底部：实时日志面板（终端风格，绿色文字）
- 添加任务模态框：URL 输入 + 日期时间选择器
- 响应式但以 PC 为主

## 配置文件 (config.yaml)
```yaml
server:
  host: "0.0.0.0"
  port: 8080

grabber:
  preheat_seconds: 3          # 提前打开页面时间
  retry_count: 3              # 失败重试次数
  retry_interval_ms: 200      # 重试间隔
  screenshot: true            # 是否截图

browser:
  headless: true              # 无头模式
  user_agent: ""              # 自定义 UA（空则用默认）

ntp:
  server: "ntp.aliyun.com"    # NTP 服务器
  sync_before_grab: true      # 抢券前校时
```

## 注意事项
- 仅支持单账号操作
- Cookie 通常 7-30 天有效，过期需重新扫码
- 京东可能有滑块验证，需要手动处理
- 不同活动页面结构不同，按钮选择器需要适配
- 建议 headless: false 首次调试，确认流程正确后再改为 true
