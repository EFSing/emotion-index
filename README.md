# Emotion Index（社交媒体股市情绪指数）

用于监控社交媒体对股市情绪、计算点时（Point-in-Time）情绪指数并生成可审计离线报告的 Python 工程系统。

V1 初始化底座提供严格防乱的单向分层骨架、时点可用性校验（严防未来函数与事后热度污染）、合成样本冒烟测试与架构防回归测试。

## 1. 目录结构

```text
emotion-index/
├── AGENTS.md                   # AI/开发者防乱与架构边界铁律（首读入口）
├── HANDOFF.md                  # 跨设备恢复与当前唯一状态源（Single Source of Truth）
├── README.md                   # 项目说明与快速启动指令
├── pyproject.toml              # Python 3.12+ 包定义与 CLI 入口
├── configs/
│   └── smoke_demo.json         # 无凭证的合成社交媒体情绪样本
├── docs/
│   ├── ARCHITECTURE.md         # 7 层单向依赖契约与时点防泄漏规范
│   ├── CURRENT_STATUS.md       # 当前系统能力与已知边界
│   └── DECISION_LOG.md         # 关键架构决策日志
├── src/emotion_index/
│   ├── domain/                 # 不可变领域契约（帖子、时点、情绪分、指数快照）
│   ├── sources/                # 数据源资格、去重与 cutoff 时点可用性校验
│   ├── sentiment/              # 纯内存情绪打分与多维情绪指数聚合
│   ├── research/               # 市场时段对齐与描述性统计
│   ├── reporting/              # 只读 HTML 与 JSON 摘要投影
│   ├── integrations/           # 外部 I/O 边界（配置读取、.runtime 原子发布）
│   └── app/                    # CLI 入口与端到端编排
├── tests/
│   ├── test_architecture.py    # 模块单向无环依赖、纯计算层禁 I/O 与 Git 防脏检查
│   └── test_smoke.py           # 时点过滤、情绪指数聚合与幂等原子发布测试
└── .runtime/                   # 【Git 忽略】本地运行期产物、原始快照与缓存唯一目录
```

## 2. 快速启动与验证

需要 Git 和 Python 3.12 或更高版本（本机标准解释器为 `D:\Tools\Python312\python.exe`）。

### Windows PowerShell

```powershell
cd D:\ChatGPT\projects\emotion-index
D:\Tools\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e .
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\emotion-index.exe smoke
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -e .
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/emotion-index smoke
```

运行 `emotion-index smoke` 后，将在 `.runtime/artifacts/smoke/EMOTION_US_2026-11-27_pre_open.html` 生成自包含离线报告。相同输入与渲染版本重复执行将幂等复用同一产物。

## 3. 采集公开源

```powershell
.\.venv\Scripts\emotion-index.exe capture --market US
.\.venv\Scripts\emotion-index.exe capture --market CN
```

每轮采集输出一份 JSON 收据路径。收据按子源记录成功、限流、超时、鉴权失败、未配置和缺失等状态。原始响应保存在 `.runtime/data/raw/<market>/<platform>/<UTC 日期>.json`，解码每条 `payload_base64` 即可取得原始字节并核对 `sha256`。每条响应的 `fetched_at` 和 `available_at` 均为实际接收时刻。回放使用观察窗口的截止时刻筛选成功响应，再按帖子发布时间校验。已有平台日文件经校验后复用，响应内容、时间和失败记录随日文件保留。当天没有生成日文件的平台可以重新采集。

### RSS 与盘面锚

为各市场单独设置 `EMOTION_INDEX_RSS_<市场>_<路由>_URL`。自托管 RSSHub URL 可包含市场过滤参数。每条 URL 可选用同前缀的 `_COOKIE` 设置访问 RSSHub 的 Cookie。雪球上游会话由 RSSHub 实例自己的 `XUEQIU_COOKIES` 配置管理。

| 路由变量名 | 市场 | URL 示例路径 |
| --- | --- | --- |
| `XUEQIU` | US、CN | `/xueqiu/hots` |
| `XUEQIU_USER` | US、CN | `/xueqiu/user/<id>` |
| `CLS` | US、CN | `/cls/telegraph` |
| `WALLSTREETCN` | US、CN | `/wallstreetcn/news/global` |
| `JIN10` | US、CN | `/jin10/telegraph` |
| `SINA` | CN | `/sina/news/roll` |
| `JISILU` | CN | 自托管实例中可用的免登集思录 RSS 路由 |

美股盘面锚冻结 CNN 恐慌贪婪指数和 Yahoo Finance 日线，设置 `EMOTION_INDEX_FINNHUB_TOKEN` 后也尝试 Finnhub 新闻情绪。A 股盘面锚冻结腾讯财经的宽基和风格日线。每条行情请求独立记入收据。

### 小红书 Inbox

将合规导出的 UTF-8 JSON 文件放入 `.runtime/inbox/xhs/`。每个文件只包含一个市场，其他市场的采集会跳过该文件。如下为最小样例。

```json
{
  "market": "CN",
  "posts": [
    {
      "post_id": "example-1",
      "published_at": "2026-11-27T06:00:00Z",
      "text": "沪深300收盘观察",
      "tickers": ["000300.SH"]
    }
  ]
}
```

`published_at` 必须含时区。可选字段包括 `author_id`、`tickers`、`available_at` 和非负整数 `likes`、`replies`、`reposts`。实际导入时刻用于回放，文件中声明的 `available_at` 必须处于发布时间与导入时刻之间。缺失小红书样本时，收据标记 `XHS_NOT_PROVIDED`。

## 4. 生成三轨日报

```powershell
.\.venv\Scripts\emotion-index.exe report --market US
.\.venv\Scripts\emotion-index.exe report --market CN
```

`report` 读取冻结数据，复用 DeepSeek 缓存与词典降级，并在 `.runtime/artifacts/daily/` 发布中文 HTML 日报与只读摘要。用 `--capture-date` 指定历史采集日，用 `--cutoff-at` 指定回放截止时刻。定时出报、邮件、Bark、Google Drive 归档与 JSON 字段约定见 [VPS 部署说明](deploy/systemd/README.md)。
