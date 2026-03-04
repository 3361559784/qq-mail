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

- push 到 `main` 自动触发
- Python `3.11`
- 先执行单测，再通过 `Azure/functions-action@v1` 部署到 Flex Consumption

## 测试

```bash
python3 -m unittest discover -s tests -v
```
