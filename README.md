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
