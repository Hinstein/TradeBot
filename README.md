# Binance Trader Bot

Telegram 控制的 Binance USDT-M Futures 交易机器人。当前运行入口是：

```text
run.sh -> main.py -> bot/__init__.py
```

## 当前状态

- Telegram 负责参数面板、开仓按钮、持仓查询和全部平仓。
- Binance 交易使用 REST API 下单。
- 分批止盈监控使用 Binance User Data WebSocket，不再轮询 `get_position_risk`。
- 运行方式使用 macOS `launchd`，配置文件是 `trader-bot.plist`。

## 启动和停止

前台启动：

```bash
./run.sh
```

launchd 启动：

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/trader-bot.plist
```

重启：

```bash
launchctl kickstart -k "gui/$(id -u)/trader-bot"
```

停止并卸载：

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/trader-bot.plist
```

## 目录结构

```text
main.py                 程序入口，调用 bot.main()
run.sh                  激活 venv 并运行 main.py
bot/                    Telegram Bot 层
  __init__.py           Application 启动、handler 注册、launchd 运行主流程
  handlers.py           Telegram 命令、按钮回调、开仓/平仓入口
  keyboards.py          面板按钮
  panels.py             面板和结果文本
  watches.py            Binance User Data WebSocket 分批止盈监控
trader/                 交易层
  __init__.py           TradeExecutor，开仓主流程
  client.py             Binance REST 客户端包装
  orders.py             下单、撤单、止盈止损、数量/价格计算
  risk.py               风险参数校验
utils/                  通用工具
  config.py             .env、settings.json、watches.json 配置读取
  logger.py             日志配置
  retry.py              重试和错误格式化
```

## 关键运行文件

这些文件是运行时会使用或生成的：

```text
.env                    API key、Telegram token、环境变量，本地私密文件
settings.json           Telegram 面板保存的参数
watches.json            当前分批止盈监控状态
bot.log                 运行日志
surge-ca-bundle.pem     本机 TLS/代理证书配置，如不用 Surge 可移除相关环境变量
```

## 当前交易流程

1. Telegram 点击币种按钮。
2. `bot/handlers.py` 读取 `settings.json`。
3. `TradeExecutor` 计算数量、校验最小名义价值和最大数量。
4. REST API 发送市价单。
5. 创建止损单和止盈单。
6. 如果是分批止盈，写入 `watches.json`。
7. `bot/watches.py` 通过 Binance User Data WebSocket 接收成交和仓位事件。
8. TP1 成交后自动把止损移动到成本价。
9. 仓位归零后自动清理本地监控。

## 已归档的旧文件

旧结构和临时验证文件已经移到 `archive/`，需要恢复时可以从这里取回。

### 旧版重复代码

```text
archive/legacy-code/bot.py
archive/legacy-code/trader.py
archive/legacy-code/compatibility.py
```

### 旧文档/迁移记录

```text
archive/docs/DEPLOY.md
archive/docs/ENVIRONMENT_SWITCH_GUIDE.md
archive/docs/ENVIRONMENT_SWITCH_STATUS.md
archive/docs/OPTIMIZATION_SUMMARY.md
```

### 临时测试/验证脚本

```text
archive/scripts/init.py
archive/scripts/verify_setup.py
archive/scripts/test_basic.py
archive/scripts/test_env_simple.py
archive/scripts/test_environment_switch.py
```

### 运行时或系统生成文件

这些不应该作为源码维护：

```text
.DS_Store
bot.log
__pycache__/
venv/
settings.json
watches.json
.env
```

其中 `.env`、`settings.json`、`watches.json` 不能随便删除；它们是本机运行状态或私密配置。

## 后续精简建议

1. 确认 `archive/` 中内容不再需要后，可以整体删除。
2. 后续可把 `settings.json`、`watches.json` 移到单独的 runtime 目录，进一步区分源码和运行状态。
3. 如果不再使用 Surge/本地 HTTPS 代理，可移除 `surge-ca-bundle.pem` 和 `.env` 中的 `SSL_CERT_FILE`。

## 安全注意

- 当前 `.env` 里包含私密凭据，不要提交或分享。
- 如果日志里出现 Telegram bot token，建议去 BotFather 轮换 token。
- 主网交易前确认面板显示为生产环境，并把保证金和杠杆调到安全范围。
- 小币种可能触发最大数量限制，程序会在下单前本地拦截。
