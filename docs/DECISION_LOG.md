# 关键架构决策日志（DECISION LOG）

## 2026-11-27 · 项目初始化、目录防乱与时点防泄漏契约

### 背景
新建社交媒体股市情绪监控项目时，需从源头避免历史项目中曾出现的多工作树并列、运行产物与缓存散落、Windows/Linux 换行符污染，以及社交媒体回测中最易发生的“事后抓取/事后互动量回填导致未来信息泄漏”问题。

### 决策
1. **目录与仓库规范化**：
   - 将原含零宽字符（`U+200C`）与空格的目录名规范化为 `D:\ChatGPT\projects\emotion-index`，远端 GitHub 仓库命名为 `EFSing/emotion-index`，默认主分支为 `main`，虚拟环境固定为项目内 `.venv`。
2. **单向分层与纯计算隔离**：
   - 采用 `src/emotion_index/` 7 层模块化单体（`domain`、`sources`、`sentiment`、`research`、`reporting`、`integrations`、`app`）。
   - 所有外部文件/网络 I/O 收敛于 `integrations`，纯计算层仅接收不可变领域对象，并由 `tests/test_architecture.py` 通过 AST 静态测试强制门禁。
3. **双时间戳与决策截止（`cutoff_at`）强制校验**：
   - 每条社交帖子快照必须同时携带平台发布时间 `published_at` 与实际可见/采集时间 `available_at`。
   - 观察窗口显式指定 `window_start` 与 `cutoff_at`；凡 `available_at > cutoff_at` 的记录一律进入延迟未就绪诊断计数（`late_unavailable_count`），禁止参与当期情绪指数计算。
4. **运行产物唯一容器（`.runtime/`）与原子发布**：
   - 所有原始采集数据、缓存与生成报告均限制在 `.runtime/` 目录内并通过 `.gitignore` 与架构测试双重锁定。
   - 报告输出采用临时文件写入 + SHA-256 校验原子发布，保证相同输入幂等复用、不同内容冲突显式拒绝。

## 2026-11-27 · V2 双市场三轨情绪指数与零新增付费任务书冻结

详见完整任务书 [docs/SPEC_V2.md](SPEC_V2.md)，关键拍板结论如下：
1. **双市场与 4 大对称风格板块**：严格隔离 `US` 与 `CN` 情绪池；聚焦大盘整体情绪 + 4 大宏观风格板块（`TECH_GROWTH`、`CYCLICAL_FINANCE`、`DEFENSIVE_DIVIDEND`、`HIGH_BETA_THEME`），不做全市场个股撒网。
2. **零新增付费数据源与明文禁用 `akshare`**：严禁引入 `akshare`；全面采用官方免费公开 API（ApeWisdom / Bluesky / StockTwits / Finnhub / CNN Fear & Greed）、VPS 自托管 RSSHub（雪球 `/xueqiu/hots`、财联社、华尔街见闻、新浪、金十，单源 Cookie 过期优雅降级并 Bark 提醒）与复用既有稳定行情源（`yfinance` / 腾讯 / 新浪 / `BaoStock`）。小红书保留高权重校准插件位（`.runtime/inbox/xhs/`），不强绑每日自动流程。
3. **三轨情绪指标与背离警示**：并列展示 ① 社交文本极性（0–100）、② 近 20 日声量 Z-Score、③ 客观盘面情绪锚，显式标出社交过热/恐慌与盘面背离警示。
4. **DeepSeek 谷值 5 折门禁与防烧钱机制**：采用采集与打分时间解耦（A 股 16:00 冻结采集 → 18:05 谷值打分；美股美东 17:30 采集并天然落在谷值窗口直接打分），强制代码级高峰拦截、每批 20–30 条 JSON 打包、每市场 Top 100 配额、内容哈希缓存与故障降级 `DEGRADED_LEXICON`。
5. **无域名交付与 Google Drive 归档**：VPS 定时输出自包含 HTML 日报 + 邮件推送 + Bark 极端/异常提醒 + 对外只读 `emotion_summary.json` 契约；历史快照、JSONL 账本与缓存增量同步至 Google Drive `EmotionIndex/`，Git 严守零数据入库。
