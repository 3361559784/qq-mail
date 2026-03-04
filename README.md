# QQ 邮箱自动回复（模块化二期）

使用 QQ 邮箱 IMAP/SMTP + GitHub Models 实现自动回复服务，具备：

- 主模型 + 多层备选模型降级调用
- 邮件末尾自动标注实际使用模型
- 中等强度过滤（广告/通知/系统邮件不回复）
- `SINCE + 本地判重` 拉取策略，避免仅依赖未读状态
- 常用邮箱自动学习（窗口化 + 最大事件数限制）

## 项目结构

```text
.
├── main.py                # CLI 入口
├── config.py              # 配置加载
├── model_chain.py         # 模型链调用
├── mail_client.py         # IMAP/SMTP + 邮件解析
├── filter_rules.py        # 过滤规则
├── storage.py             # 状态存储（判重/常用邮箱）
├── data/
│   └── allow_senders.example.txt
└── tests/
```

## 前置准备

1. 在 QQ 邮箱开启 IMAP/SMTP，获取授权码（不是 QQ 密码）。
2. 准备 GitHub Token，包含 `models:read` 权限。
3. Python 3.10+。

## 配置

```bash
cp .env.example .env
cp data/allow_senders.example.txt data/allow_senders.txt
```

至少配置：

- `QQ_EMAIL`
- `QQ_AUTH_CODE`
- `GITHUB_TOKEN`

关键配置：

- `GITHUB_MODEL_PRIMARY`：主模型
- `GITHUB_MODEL_FALLBACKS`：逗号分隔备选模型
- `MODEL_SIGNATURE_TEMPLATE`：默认 `--\n使用 {model} 模型自动生成回复`
- `IMAP_FETCH_DAYS`：按天窗口拉取邮件
- `ALLOW_SENDERS_FILE`：手动白名单文件
- `FREQUENT_*`：常用邮箱自动学习参数

兼容项：

- 若只设置 `GITHUB_MODEL`，将自动作为主模型使用。

## 安装与运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py --once --verbose
```

常驻：

```bash
python3 main.py
```

## 过滤规则（FILTER_LEVEL=medium）

硬过滤（命中即不回复）：

- `Auto-Submitted != no`
- `Precedence` 属于 `bulk/list/junk/auto_reply`
- 存在 `List-Unsubscribe`
- `Return-Path: <>`
- 发件人包含 `no-reply/noreply/mailer-daemon/postmaster`
- 主题命中系统/通知关键词

软判定：

- 根据问句、请求语气、自然语言长度等计算人类信号分
- 低分邮件仅在命中手动白名单或常用邮箱时放行

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## GitHub Models 参考

- Chat Completions API: <https://docs.github.com/en/rest/models/inference>
- Model Catalog API: <https://docs.github.com/en/rest/models/catalog>
