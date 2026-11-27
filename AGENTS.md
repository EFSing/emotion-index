# 社交媒体股市情绪指数（Emotion Index）开发与防乱规则

本文件是所有开发者与 AI 代理（Codex / Antigravity）进入本项目的唯一首读入口。

## 1. 开工与恢复流程

- **唯一项目根目录**：`D:\ChatGPT\projects\emotion-index`，默认主分支为 `main`，虚拟环境固定内聚于项目根下 `.venv`（基于 `D:\Tools\Python312\python.exe`）。
- **单一工作树铁律（Single Checkout Rule）**：
  - 日常开发、测试与研究必须在同一工作树内通过 `git switch -c <branch>` 切换分支。
  - **严禁**在 `D:\ChatGPT` 或 `D:\ChatGPT\projects` 下新建并列项目副本、外置虚拟环境或持久化 Git Worktree（如 `emotion-index-prXX`）。若确需临时 Worktree，必须放在系统临时目录 `D:\Temp\worktrees\` 下，并在任务结束时立即执行 `git worktree remove --force` 与 `git worktree prune`。
- **开工必读顺序**：
  1. [AGENTS.md](AGENTS.md)
  2. [HANDOFF.md](HANDOFF.md)
  3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
  4. [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md)
  5. [docs/DECISION_LOG.md](docs/DECISION_LOG.md)
- **实时事实核验**：开工前先核对 `git status --short --branch`、`git remote -v`、当前分支及远端 GitHub Actions CI 状态。若文档记录与实际 Git/产物状态存在影响当前任务的语义矛盾，必须先标记 `PROJECT_GOVERNANCE_STATE_CONFLICT` 并依据可核验证据解决后再推进。

## 2. 架构与实现边界

- **单向分层与依赖约束**：所有业务代码位于 `src/emotion_index/`，严格遵循 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 定义的单向无环依赖表。计算层（`domain`、`sources`、`sentiment`、`research`、`reporting`）仅处理已校验的不可变内存对象，**禁止**直接进行文件读写、网络请求或读取系统当前时间；所有外部 I/O 统一收敛在 `integrations`，由 `app` 负责编排。
- **时点与防未来函数契约（Point-in-Time Discipline）**：
  - 社交媒体帖子必须显式区分发布时间（`published_at`）与采集/可见时间（`available_at`）。
  - 任何情绪指数计算或市场交易时段对齐都以决策截止时刻（`cutoff_at`）校验：`available_at <= cutoff_at` 且 `published_at <= available_at`。晚于截止时刻才可见的帖子、编辑或热度数据（如事后累计点赞/转发量）必须明确拒绝或打上 `POST_HOC_UNVERIFIED` 标记，严禁混入历史点时回放。
- **工程复杂度预算（Complexity Budget）**：
  - 默认选择“最小正确实现”，避免为假设性需求提前引入多余抽象层、状态机或兼容包装。
  - 强 fail-closed 防御重点用于防未来数据泄漏、防原始数据污染、不可逆产物写入与凭证安全。

## 3. 运行产物隔离与清理规范

所有运行期数据、缓存与生成报告**必须**收敛在 `.runtime/` 下，严禁散落到项目根目录或提交入库：

| 路径 | 归属与清理策略 |
| --- | --- |
| `.runtime/artifacts/smoke/` | 离线合成冒烟测试生成的只读 HTML 与摘要，可随时清理并由 `emotion-index smoke` 重建 |
| `.runtime/artifacts/daily/` | 按交易日/观察窗口冻结的情绪指数报告与机器摘要，停止运行后按保留策略归档或清理 |
| `.runtime/data/raw/` | 社交媒体原始采集快照与响应收据（含采集时间戳与 SHA-256），按本地数据策略保留，**严禁提交 Git** |
| `.runtime/tmp/`、`.runtime/cache/` | 临时文件与可重建缓存，测试结束自动清理，无运行进程时可随时清空 |
| `.venv/`、`build/`、`dist/`、`*.egg-info/` | 本机 Python 虚拟环境与构建产物，已加入 `.gitignore`，可随时重建 |

- 提交前必须检查 `git status` 与 `git diff --check`，并运行 `python -m unittest discover -s tests -v`（内含架构依赖方向与 Git 脏产物静态拦截测试）。
- `configs/` 仅存放无凭证的可审阅配置与合成测试样本；所有 API Key / Token / Cookie 必须通过环境变量或未入库的本地 `.env` 注入。
