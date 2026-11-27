# Emotion Index VPS 定时部署

| 服务 | 调度 | 工作 |
| --- | --- | --- |
| `emotion-index-cn-capture` | 周一至周五 16:00，`Asia/Shanghai` | 采集并冻结 A 股原始响应 |
| `emotion-index-cn-report` | 周一至周五 18:05，`Asia/Shanghai` | 读取当天冻结记录，在谷值窗口打分并交付 |
| `emotion-index-us-daily` | 周一至周五 17:30，`America/New_York` | 先冻结美股数据，再打分并交付。时区自动跟随夏令时 |

服务仅处理当前交易日。定时器跳过周末，交易所休市日用环境变量维护。机器错过计划时刻时，定时器跳过补触发。默认以该轮采集收据的完成时间为 `cutoff_at`，所以 16:00 开始采集的样本通常在 16:00 之后才可见。需要严格按 16:00 回放时，可为指定交易日传入 `--cutoff-at YYYY-MM-DDT16:00:00+08:00`，晚于截止时间的样本会被排除；一个交易日只冻结一份日报。

## 安装

在 VPS 上将唯一 checkout 放在 `/opt/emotion-index`，用 Python 3.12 或更新版本建立项目内 `.venv` 并执行 `.venv/bin/pip install -e .`。创建专用系统用户及同名组 `emotion-index`，让该用户可以读取代码和虚拟环境，并拥有 `.runtime/` 的写入权限。安装 systemd、时区数据库、rclone 和 util-linux 的 flock。使用其他部署路径或用户时，修改三个服务模板中的对应值。

将环境文件放在未入库的 `/etc/emotion-index/emotion-index.env`，所有者设为 root，权限设为 `0600`。systemd 读取该文件并注入服务进程。变量按需填写。

| 环境变量 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY`、`EMOTION_INDEX_DEEPSEEK_HOLIDAYS` | DeepSeek 凭证及中国法定节假日，后者使用逗号分隔的 ISO 日期 |
| `EMOTION_INDEX_CN_MARKET_HOLIDAYS`、`EMOTION_INDEX_US_MARKET_HOLIDAYS` | 两个交易所的额外休市日，使用逗号分隔的 ISO 日期 |
| `EMOTION_INDEX_RSS_<US或CN>_<来源>_URL`、对应的 `_COOKIE` | RSS/RSSHub 地址及可选认证。来源键包括 `XUEQIU`、`XUEQIU_USER`、`CLS`、`WALLSTREETCN`、`JIN10`，以及 CN 的 `SINA`、`JISILU` |
| `EMOTION_INDEX_FINNHUB_TOKEN` | 可选 Finnhub 采集凭证 |
| `EMOTION_INDEX_SMTP_HOST`、`EMOTION_INDEX_SMTP_FROM`、`EMOTION_INDEX_SMTP_TO` | SMTP 主机、发件邮箱、逗号分隔的收件邮箱。留空 HOST 可关闭邮件 |
| `EMOTION_INDEX_SMTP_TLS`、`EMOTION_INDEX_SMTP_PORT`、`EMOTION_INDEX_SMTP_USER`、`EMOTION_INDEX_SMTP_PASSWORD` | 默认 `starttls` / 587，支持 `ssl` / 465。启用认证时同时设置用户名和密码 |
| `EMOTION_INDEX_BARK_KEY`、`EMOTION_INDEX_BARK_URL` | Bark 设备密钥及可选 HTTPS 服务地址，默认 `https://api.day.app`。留空设备密钥可关闭 Bark |
| `EMOTION_INDEX_RCLONE_REMOTE` | rclone 远端名，默认 `gdrive`。用 rclone 的环境远端配置注入凭证，例如 `RCLONE_CONFIG_GDRIVE_TYPE=drive` 与 `RCLONE_CONFIG_GDRIVE_TOKEN` |

从仓库根目录执行以下安装命令。

```sh
sudo install -d -o emotion-index -g emotion-index -m 0700 /opt/emotion-index/.runtime/tmp
sudo install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/emotion-index-*.service /etc/systemd/system/emotion-index-*.timer
sudo systemctl daemon-reload
sudo systemctl enable --now emotion-index-cn-capture.timer emotion-index-cn-report.timer emotion-index-us-daily.timer
systemctl list-timers 'emotion-index-*'
```

查看运行日志可用 `journalctl -u emotion-index-cn-report -u emotion-index-us-daily`。当天采集缺失时，计划出报会报错并保留已有日报。手工补报使用 `emotion-index report --market CN --capture-date YYYY-MM-DD`。对接交付前，可带上 `--sync --dry-run` 查看归档计划。该模式仍生成本地日报，并跳过通知、远端写入和本地清理。

## 日报契约与归档

- 本地 HTML 为 `.runtime/artifacts/daily/EMOTION_<market>_<YYYY-MM-DD>_close.html`。下游以同目录 `emotion_summary_<market>_<YYYY-MM-DD>.json` 的出现作为发布完成标记。HTML 和账本先落盘，JSON 最后原子发布。中断后用同样的命令可恢复至同一份冻结结果。重试通知可能重复投递。
- JSON `schema_version=2`。大盘三轨和 `composite_score` 位于顶层，`sectors` 以四个板块枚举为键。缺失数值为 `null`。声量使用截止时可见的可用帖子数，历史读取排除本日和晚于截止的记录。无可用文本来源的日报保留趋势记录，但该日不进入声量基线。HTML 内的 `emotion-index-audit` JSON 与同日摘要相同。
- 盘面锚按可用分量等权。宽基与风格方向以 50 为中性，每 1 个百分点对应 10 分，单项约束在 0–100。A 股有足够量能历史时，将涨跌幅乘以当日量与之前 20 个交易日均量之比，倍率限定为 0.5–1.5。美股 VIX 以 20 为中性，每高 1 点扣 2 分，五日每上涨 1% 再扣 1 分。风格锚综合自身涨跌与相对宽基表现，防御风格跑赢在大盘部分表示偏避险。未到截止时刻可见、陈旧或不可用的报价不会进入计算。
- SMTP 发送中文摘要和完整 HTML 附件。Bark 在极度狂热、极度恐慌、词典降级或雪球认证失败时触发。
- rclone 增量归档到 `<remote>:EmotionIndex/` 下的 `raw/`、`ledger/`、`artifacts/daily/`、`cache/`。成功后才清理本地约定路径中超过 60 个 UTC 日的原始快照与收据，远端保留历史。计划服务共用 `flock` 锁，手工修改或备份运行数据时也使用该锁。恢复时分别把目录复制回 `.runtime/data/raw/`、`.runtime/data/ledger/`、`.runtime/artifacts/daily/`、`.runtime/cache/`。
