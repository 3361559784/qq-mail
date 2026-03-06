# QQ 邮箱自动回复（Azure Functions 版）

基于 QQ 邮箱 IMAP/SMTP + GitHub Models 的自动回复服务，运行于 Azure Functions Timer Trigger。

## 主要能力

- 主模型 + 多层备选模型降级调用
- 回复尾部标注实际使用模型
- 中等强度过滤（系统通知/广告自动跳过）
- `SINCE + 判重` 拉取策略
- 状态存储支持 `Azure Table`（默认）与本地文件回退
- 支持发件人白名单/黑名单（邮箱和域名）

## 项目结构

```text
.
├── function_app.py        # Azure Functions Timer 入口
├── host.json              # Functions 主配置
├── runner.py              # run_once 核心执行逻辑
├── main.py                # 本地 CLI 调试入口
├── config.py
├── model_chain.py
├── mail_client.py
├── filter_rules.py
├── storage.py
└── tests/
```

## 本地运行

```bash
cp .env.example .env
cp data/allow_senders.example.txt data/allow_senders.txt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py --once --verbose
```

## 暂停 Azure 自动链路（本地开发阶段）

1. GitHub Actions 仅保留手动触发（当前工作流已切换为 `workflow_dispatch`）。
2. Azure Portal -> Function App -> 环境变量新增：`AzureWebJobs.qq_mail_timer.Disabled=1`。
3. 可选保险开关：`QQ_MAIL_TIMER_DISABLED=true`（代码层直接跳过 timer 执行）。

## Azure Functions 调度

- `TIMER_SCHEDULE` 使用 UTC cron。
- 默认值：`0 */5 * * * *`（每 5 分钟）。

UTC 映射示例：

- KST `09:00` = UTC `00:00`
- 中国时区 `09:00` = UTC `01:00`

## 状态存储后端

- `STORAGE_BACKEND=auto`：优先 Table；无连接串回退本地 JSON
- `STORAGE_BACKEND=table`：强制 Table
- `STORAGE_BACKEND=file`：强制本地文件（仅开发调试）
- `TABLE_CONNECTION_STRING` 为空时会回落使用 `AzureWebJobsStorage`
- `DENY_SENDERS_FILE`：黑名单文件路径（默认 `data/deny_senders.txt`）

Table 默认表名：

- `processedstate`
- `frequentsenderstate`

## CI/CD

GitHub Actions：

- 当前仅 `workflow_dispatch` 手动触发（本地 Workbench 开发阶段暂停自动部署）
- Python `3.11`
- 先执行单测，再通过 `Azure/functions-action@v1` 部署到 Flex Consumption

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## Workbench（本地邮件工作台）

### 本地 Web

```bash
python3 main.py workbench-web --host 127.0.0.1 --port 8787
```

### 单次同步

```bash
python3 main.py workbench-sync
```

### 向量问答

```bash
python3 main.py workbench-search \"去年比赛邮件提到的截止时间是什么\"
```

### 任务列表

```bash
python3 main.py workbench-tasks --status open
```

## 排查是否已回复

系统会写统一决策日志，格式以 `DECISION | action=...` 开头。

- `action=reply`：已回复
- `action=skip`：未回复（会带具体 `reason`）
- `action=error`：发送或处理失败

在 Azure Log Analytics 可用以下 KQL 快速查询：

```kql
AppTraces
| where TimeGenerated > ago(24h)
| where Message contains "DECISION |"
| order by TimeGenerated desc
```

查看具体“发给谁、发了什么（预览）”：

```kql
AppTraces
| where TimeGenerated > ago(24h)
| where Message contains "REPLY_SENT |"
| project TimeGenerated, Message
| order by TimeGenerated desc
```

启用给自己发回执邮件（默认开启）：

- `SELF_NOTIFY_ON_REPLY=true`
- `SELF_NOTIFY_EMAIL=`（留空默认发到 `QQ_EMAIL`）
- `SELF_NOTIFY_BODY_CHARS=1200`

回执日志查询：

```kql
AppTraces
| where TimeGenerated > ago(24h)
| where Message contains "NOTIFY_SENT |"
| project TimeGenerated, Message
| order by TimeGenerated desc
```
