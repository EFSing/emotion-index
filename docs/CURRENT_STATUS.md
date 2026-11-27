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

## 待推进事项

- V2 完整规格书见 [docs/SPEC_V2.md](SPEC_V2.md)。
- 当前处于 **准备启动 PR-3（DeepSeek 谷值 5 折门禁客户端 + 内容哈希持久化缓存 + 中英双语词典降级 + `score` 子命令）** 状态。
