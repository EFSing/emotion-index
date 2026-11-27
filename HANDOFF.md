# 跨设备恢复与当前状态（HANDOFF）

## 1. Current State (Single Source Of Truth)

- **项目名称**：`emotion-index`（社交媒体股市情绪监控与情绪指数系统）
- **本地唯一路径**：`D:\ChatGPT\projects\emotion-index`
- **远端仓库**：`https://github.com/EFSing/emotion-index.git`
- **默认主分支**：`main`
- **当前语义阶段**：`V1_PROJECT_BOOTSTRAP`（防乱工程底座、7 层单向架构骨架、时点防泄漏契约、离线冒烟流水线与 CI 已就绪）
- **动态 Git / GitHub 状态**：`EXTERNAL_VERIFY_REQUIRED`（每次会话启动时通过 `git status -sb`、`git log -n 3` 及 GitHub Actions 页面实时核验）
- **Next Action**：
  1. 明确首批接入的社交媒体数据源（如 X/Twitter、Reddit `r/wallstreetbets` / `r/stocks`、雪球、东财股吧或 Truth Social 等）及采集方式（官方 API / RSS / 本地导入快照）。
  2. 在 `integrations` 层定义首套真实数据源的只读原始快照与 `available_at` 收据格式。

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
