# 当前状态（CURRENT STATUS）

恢复基线为远端 `main` 分支。合并历史与当前下一步行动见 [HANDOFF.md](../HANDOFF.md)。

## V1 工程初始化与防乱底座

- **目录与命名规范化**：已将本地项目路径统一为 `D:\ChatGPT\projects\emotion-index`（清理了原文件夹名中的零宽不可见字符与空格），并与 GitHub 远端仓库 `EFSing/emotion-index` 建立同步。
- **7 层单向模块化单体**：已建立 `src/emotion_index/` 下的 `domain`、`sources`、`sentiment`、`research`、`reporting`、`integrations`、`app` 七层结构，纯计算层与外部 I/O 严格隔离。
- **时点防泄漏与合成冒烟流水线**：
  - `emotion-index smoke` 读取 `configs/smoke_demo.json` 中的合成社交媒体样本，校验 `published_at` 与 `available_at` 相对窗口 `cutoff_at` 的可用性，自动剔除未来可见（late capture）记录并统计诊断缺口。
  - 计算 `[-1.0, +1.0]` 归一化净情绪值与 `[0.0, 100.0]` 情绪指数刻度（`EXTREME_FEAR`、`FEAR`、`NEUTRAL`、`GREED`、`EXTREME_GREED`），并在 `.runtime/artifacts/smoke/` 下原子发布自包含中文 HTML 报告。
- **自动化防乱测试与跨平台 CI**：
  - `tests/test_architecture.py` 静态校验 AST import 方向、无环拓扑、纯计算层禁用文件/网络 I/O 以及 Git 仓库零运行垃圾与零密钥泄漏。
  - `.github/workflows/ci.yml` 覆盖 Ubuntu（Python 3.12 / 3.13）与 Windows（Python 3.12）自动化测试及 `git diff --check`。

## V2 PR-1：双市场核心契约与三轨纯内存引擎（已合入 `main`）

- **配置与领域契约**：新增 `configs/weights.json`、`configs/sectors.json` 与 `src/emotion_index/domain/market.py`，严格隔离 `US` / `CN` 双市场，固化 4 大对称风格板块、`EmotionState`、三轨输出与背离枚举。
- **时点筛选与文本加权聚合**：扩展 `src/emotion_index/sources/filter.py`（板块路由、按市场与平台配置交集筛选）与 `src/emotion_index/sentiment/market.py`（缺源自动重归一化、小红书高权重 30%/40% 自动缩放与 `XHS_NOT_PROVIDED` / `XHS_NOT_SCORED` 标记）。
- **三轨聚合与背离引擎**：新增 `src/emotion_index/research/three_track.py` 与 `tests/test_market_index.py`，实现近 20 日声量 Z-Score（含 `WARMUP` / `ZERO_VARIANCE`）、客观盘面锚时点接入与 4 条背离/极端警示规则。

## V2 PR-2：免费公开数据源适配器与原始快照冻结（已合入 `main`）

- **免费公开源与小红书插件位**：新增 `src/emotion_index/integrations/providers/`（`apewisdom.py`、`bluesky.py`、`stocktwits.py`、`rss_feeds.py`、`us_anchor.py`、`cn_anchor.py`、`xhs_inbox.py`、`http.py`）与 `integrations/capture.py`。
- **原子冻结与故障隔离**：`emotion-index capture --market US|CN` 仅采集与冻结原始字节（零 DeepSeek 调用），按 `.runtime/data/raw/<market>/<platform>/<UTC日期>.json` 原子落盘并校验 SHA-256 与 `fetched_at`/`available_at`；单源失败不拖垮整轮，全失败平台不锁死当日重试机会。
- **架构门禁强化**：`tests/test_architecture.py` 新增全仓库禁 `akshare` 导入与依赖声明门禁，并加强纯计算层禁系统时钟调用校验。

## V2 PR-3：DeepSeek 谷值门禁打分、缓存与双语降级（已合入 `main`）

- **谷值门禁与批量客户端**：新增 `src/emotion_index/integrations/deepseek_client.py`，强制北京时间工作日高峰拦截（支持 `--allow-peak` 与 `EMOTION_INDEX_DEEPSEEK_HOLIDAYS`），固定 `deepseek-chat` JSON 模式、20–30 条打包、严格 Schema 校验与最多 2 次重试。
- **缓存、配额与降级**：新增 `integrations/score_cache.py`（SQLite 内容哈希持久化缓存 + 并发每日每市场 Top-100 配额预占）与纯内存 `sentiment/bilingual_lexicon.py`（`DEGRADED_LEXICON` 透明降级）。
- **冻结快照解析与 `score` 子命令**：新增 `integrations/frozen_posts.py` 与 `app/scoring.py`，按平台有效权重分层精选 Top-N 帖子防止 RSS 源被高点赞源饿死，提供 `emotion-index score --market US|CN`。

## V2 PR-4：三轨中文日报、历史账本与 VPS 交付部署（已合入 `main`）

- **客观盘面情绪锚与端到端出报**：新增 `domain/anchor.py`、`integrations/frozen_anchors.py`、`research/market_anchor.py` 与 `app/daily_report.py`，提供 `emotion-index report --market US|CN` 端到端链路，支持两阶段原子发布与中断恢复，并接入 DeepSeek 板块分类结果。
- **历史账本与自包含日报**：新增 `domain/ledger.py`、`integrations/ledger.py`（跨进程文件锁 + 按交易日幂等 JSONL 账本）与 `reporting/html_report.py` V2 三轨中文离线 HTML 日报（零外部资源、内联 SVG 趋势图、四大板块温度表、背离警示、JSON 审计区）及只读 `emotion_summary_<market>_<YYYY-MM-DD>.json` 契约。
- **通知、归档与定时模板**：新增 `integrations/notifier.py`（SMTP 邮件 + Bark 4 类核心告警）、`integrations/gdrive_sync.py`（rclone 增量归档至 `EmotionIndex/` + 成功后 60 天本地 raw 清理）与 `deploy/systemd/` 定时服务模板及部署手册。

## 当前所处阶段与待推进事项

- **代码开发阶段已全部完成**：[docs/SPEC_V2.md](SPEC_V2.md) 规划的 PR-1 ～ PR-4 均已合入 `main`，全部离线单测与架构/防乱门禁全绿。
- **下一步（尚未做实盘/线上联调）**：
  1. 在 VPS 上配置 RSSHub（Docker）与未入库的 `/etc/emotion-index/emotion-index.env`（DeepSeek Key、RSS 路由、SMTP、Bark、rclone Google Drive）。
  2. 执行真实外网连通性干跑（`capture` + `report --sync --dry-run`），检查实际抓包数据质量、HTML 日报视觉效果与同步路径。
  3. 启用 `deploy/systemd/` 定时任务进入每日自动试运行。
