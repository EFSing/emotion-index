# 跨设备恢复与当前状态（HANDOFF）

## 1. Current State (Single Source Of Truth)

- **项目名称**：`emotion-index`（社交媒体股市情绪监控与情绪指数系统）
- **本地唯一路径**：`D:\ChatGPT\projects\emotion-index`
- **远端仓库**：`https://github.com/EFSing/emotion-index.git`
- **默认主分支**：`main`
- **当前语义阶段**：`V2_SPEC_LOCKED_READY_FOR_PR1`（V1 防乱工程底座与 CI 已建立；V2 双市场三轨情绪指数任务书 [docs/SPEC_V2.md](docs/SPEC_V2.md) 已拍板冻结，尚未开始 V2 编码）
- **动态 Git / GitHub 状态**：`EXTERNAL_VERIFY_REQUIRED`（每次会话启动时通过 `git status -sb`、`git log -n 3` 及 GitHub Actions 页面实时核验）
- **Next Action**：
  1. 承接编码的模型请完整阅读 [AGENTS.md](AGENTS.md)、[docs/SPEC_V2.md](docs/SPEC_V2.md) 与 [tests/test_architecture.py](tests/test_architecture.py)。
  2. 从 `main` 切出独立功能分支，按 [docs/SPEC_V2.md](docs/SPEC_V2.md) 第六节顺序启动 **PR-1（双市场核心契约 + 4 大板块 + 三轨聚合与背离引擎，纯内存全离线）**。
  3. 严禁引入 `akshare`，每个 PR 均须保证 `python -m unittest discover -s tests -v` 与 `git diff --check` 全绿。

## 2. 合并基线与里程碑台账

恢复时核对 [main 提交记录](https://github.com/EFSing/emotion-index/commits/main/)、[main CI 状态](https://github.com/EFSing/emotion-index/actions/workflows/ci.yml?branch=main) 与 [当前状态](docs/CURRENT_STATUS.md)。

| 阶段 / PR | 分支 | 状态 | 说明 |
| --- | --- | --- | --- |
| V1 初始化基线 | `main` | 已建立 | 完成目录防乱治理、7 层单向模块化架构、`emotion-index smoke` 离线冒烟流水线、架构静态门禁测试与跨平台 CI |

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
