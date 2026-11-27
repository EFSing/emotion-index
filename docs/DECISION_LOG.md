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
