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

## 待决策事项

- V2 核心业务口径、数据源清单、DeepSeek 谷值省钱规则、Google Drive 归档与 PR-1 → PR-4 拆分均已拍板冻结，详见 [docs/SPEC_V2.md](SPEC_V2.md)。
- 当前处于 **等待启动 PR-1 编码** 状态（未写入 V2 业务代码）。
