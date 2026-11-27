# 跨设备恢复与当前状态（HANDOFF）

## 1. Current State (Single Source Of Truth)

- **项目名称**：`emotion-index`（社交媒体股市情绪监控与情绪指数系统）
- **本地唯一路径**：`D:\ChatGPT\projects\emotion-index`
- **远端仓库**：`https://github.com/EFSing/emotion-index.git`
- **默认主分支**：`main`
- **当前语义阶段**：`V2_CODE_COMPLETE_READY_FOR_VPS_DEPLOY`（V2 任务书 [docs/SPEC_V2.md](docs/SPEC_V2.md) 全部 4 个阶段 PR-1 ～ PR-4 已完成开发、审阅与离线端到端测试并合入 `main`；下一步进入实盘/VPS 环境配置与端到端联调验收）
- **动态 Git / GitHub 状态**：`EXTERNAL_VERIFY_REQUIRED`（每次会话启动时通过 `git status -sb`、`git log -n 3` 及 GitHub Actions 页面实时核验）
- **Next Action**：
  1. 在 VPS（或本机）准备未入库的环境变量文件（参考 [deploy/systemd/README.md](deploy/systemd/README.md) 与 [README.md](README.md)）：DeepSeek Key、自托管 RSSHub 路由 URL（雪球/财联社/华尔街见闻/新浪/金十/集思录）、SMTP、Bark 及 rclone Google Drive 远端配置。
  2. 先手工跑一次实盘连通性验证：`emotion-index capture --market US` / `CN` → `emotion-index report --market US --sync --dry-run` / `CN`，核验真实抓包、HTML 日报、JSON 摘要与 Google Drive 同步预览。
  3. 验收无误后按 [deploy/systemd/README.md](deploy/systemd/README.md) 安装并启用 3 个 systemd 定时器，开启每日全自动运行。

## 2. 合并基线与里程碑台账

恢复时核对 [main 提交记录](https://github.com/EFSing/emotion-index/commits/main/)、[main CI 状态](https://github.com/EFSing/emotion-index/actions/workflows/ci.yml?branch=main) 与 [当前状态](docs/CURRENT_STATUS.md)。

| 阶段 / PR | 分支 | 状态 | 说明 |
| --- | --- | --- | --- |
| V1 初始化基线 | `main` | 已建立 | 完成目录防乱治理、7 层单向模块化架构、`emotion-index smoke` 离线冒烟流水线、架构静态门禁测试与跨平台 CI |
| V2 PR-1 | `codex/pr-1-dual-market-emotion-index` | 已合并 `main` | 完成双市场权重与 4 大板块配置、核心不可变领域模型、时点筛选与板块路由、缺源/小红书重归一化聚合、20 日声量 Z-Score 与背离警示纯内存引擎及全离线单测 |
| V2 PR-2 | `codex/pr-2-data-providers` | 已合并 `main` | 完成免费公开源适配器（ApeWisdom/Bluesky/StockTwits/RSSHub/美股与 A 股盘面锚/小红书 Inbox）、原始响应原子冻结与 SHA-256 校验、`capture` 子命令及全仓库禁 `akshare` 与纯计算层禁系统时钟门禁 |
| V2 PR-3 | `codex/pr-3-deepseek-scoring` | 已合并 `main` | 完成 DeepSeek 谷值 5 折窗口门禁、SQLite 持久化哈希缓存与并发配额事务、中英双语词典降级（`DEGRADED_LEXICON`）、冻结快照帖子解析、按平台权重分层 Top-N 精选及 `score` 子命令 |
| V2 PR-4 | `codex/pr-4-report-ledger-deploy` | 已合并 `main` | 完成美股/A 股客观盘面情绪锚解析、JSONL 幂等历史账本、V2 三轨自包含中文 HTML 日报与只读 `emotion_summary` 原子两阶段发布、SMTP/Bark 通知、rclone Google Drive 增量同步与 60 天清理、及 VPS systemd 定时部署模板 |

## 3. 新设备 / 新会话最小恢复路径

```powershell
cd D:\ChatGPT\projects\emotion-index
git status --short --branch
D:\Tools\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e .
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\emotion-index.exe smoke
```
