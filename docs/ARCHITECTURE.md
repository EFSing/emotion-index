# V1 架构与防乱边界

## 1. 模块分层与单向依赖

`src/emotion_index/` 采用 Python 模块化单体结构。下表规定了各层职责与唯一允许依赖的本项目上层模块；所有层间与层内依赖必须构成有向无环图（DAG），并由 `tests/test_architecture.py` 在每次测试与 CI 中静态强制校验。

| 层 | 职责 | 允许依赖的内部层 | 允许外部 I/O |
| --- | --- | --- | --- |
| `domain` | 不可变领域值对象：市场、观察窗口、社交帖子快照、情绪得分、指数快照与审计收据 | 无 | 否（纯内存） |
| `sources` | 数据源白名单、帖子 ID 去重、发布时间（`published_at`）与可见时间（`available_at`）相对决策截止时刻（`cutoff_at`）的校验 | `domain` | 否（纯内存） |
| `sentiment` | 纯内存文本情绪极性计算、互动权重折算与综合情绪指数（0～100 恐慌/贪婪刻度及 [-1, 1] 净情绪值）聚合 | `domain` | 否（纯内存） |
| `research` | 按标的（Ticker）、来源平台与情绪状态（极度恐慌～极度乐观）的分布及市场时段对齐统计 | `domain` | 否（纯内存） |
| `reporting` | 只读 HTML 报告渲染与内嵌机器可读 JSON 审计投影 | `domain` | 否（纯内存） |
| `integrations` | 外部 I/O 边界：读取合成/本地快照文件、未来社交平台采集适配，以及 `.runtime/` 下的原子文件发布器 | `domain` | **是（唯一 I/O 边界）** |
| `app` | CLI 参数解析与流程编排：组合 `integrations` 与各纯计算层完成端到端任务 | 以上全部层 | 仅调用 `integrations` |

## 2. 社交媒体时点（Point-in-Time）与防泄漏契约

社交媒体情绪研究最常见的污染源是**未来信息泄漏**（如盘后抓取的帖子混入盘前决策，或使用数天后的累计点赞/评论数回填发帖当时的热度）。本架构在 `domain` 与 `sources` 层强制执行以下契约：

1. **双时间戳强制校验**：
   - `published_at`：帖子在原平台显示的内容发布 UTC 时间。
   - `available_at`：本系统实际采集并落地快照的 UTC 时间（或历史归档可证明的可见时间）。
   - 任何 `available_at < published_at` 的异常记录直接作为脏数据拒绝。
2. **决策截止时刻（`cutoff_at`）过滤**：
   - 每个情绪观察窗口显式声明 `window_start` 与 `cutoff_at`（全部为带时区的 UTC 时间戳）。
   - 只有满足 `window_start <= published_at <= cutoff_at` 且 `available_at <= cutoff_at` 的帖子才进入正式情绪指数计算。
   - 若帖子发生在窗口内但采集时间晚于 `cutoff_at`（即 `available_at > cutoff_at`），`sources` 层将其单独归入 `late_unavailable_count` 诊断计数，**严禁**计入当期情绪指数。
3. **重复帖子拦截**：
   - 同一 `(platform, post_id)` 出现重复记录时，明确抛出校验错误或按显式去重规则拦截，防止爬虫重抓导致情绪权重翻倍。

## 3. 原子发布与目录防乱守卫

- 所有输出产物统一由 `integrations.publisher` 写入 `.runtime/artifacts/`。
- 发布器先在同目录写入临时文件，校验 SHA-256 后原子替换或硬链接发布：
  - 同一身份且内容 SHA-256 完全一致时，幂等返回已有路径；
  - 同一身份但内容冲突时，显式报错并保留原文件，防止静默覆盖历史证据。
- `tests/test_architecture.py` 通过 Git 索引静态验证：
  - 任何被 `.gitignore` 忽略的文件不得被跟踪；
  - `.runtime/`、`data/`、`artifacts/`、`logs/`、`cache/` 等运行目录及 `.sqlite`、`.parquet`、`.html`、`.env` 等文件不得进入 Git 版本库。
