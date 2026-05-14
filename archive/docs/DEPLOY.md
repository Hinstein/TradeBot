# 部署说明（新 Mac）

## 你是 Claude

请引导用户完成部署。跟着下面步骤走就行。

### 前置
- Python 3.10+（`python3 --version`）
- 能访问 `api.binance.com` 和 `api.telegram.org`（用 `curl` 验证）
- 这台机器**没有装 AVG、Avast、Kaspersky 等做 HTTPS 深度检测的杀软**，否则 Python TLS 会被 MITM

### 步骤

1. 解压到 `~/code/binance-trader`
2. 建 venv + 装依赖：
   ```bash
   cd ~/code/binance-trader
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```
3. 问用户拿这些凭据（提醒用 `!` 前缀在当前 shell 设环境变量，别直接贴聊天）：
   - `BINANCE_API_KEY` / `BINANCE_API_SECRET`（主网，合约权限，IP 白名单）
   - `TELEGRAM_BOT_TOKEN`（BotFather）
   - `TELEGRAM_ALLOWED_USER_IDS`（用户自己 Telegram 的 user id）
4. 用户决定环境：
   - 测试先跑 testnet：`.env` 里 `BINANCE_TESTNET=true`，key 用 testnet.binancefuture.com 的
   - 正式：`BINANCE_TESTNET=false`，key 用币安主网的
5. 验证网络：
   ```bash
   python3 -c "
   import ssl, socket
   ctx = ssl.create_default_context()
   with socket.create_connection(('fapi.binance.com', 443), timeout=8) as s:
       with ctx.wrap_socket(s, server_hostname='fapi.binance.com') as ss:
           cert = ss.getpeercert()
           print('issuer:', dict(x[0] for x in cert['issuer']).get('commonName'))
   "
   ```
   应输出 `GeoTrust TLS RSA CA G1` 或类似 DigiCert 相关 CA。如果报 `self-signed` → 有 MITM，先查 `security find-certificate -a -c "AVG"` / `"Avast"` / `"Kaspersky"`。
6. 启动：`./run.sh`（前台）或 `nohup ./run.sh > bot.log 2>&1 &`（后台）
7. 用户在 Telegram 给 bot 发 `/start`，看到面板即成功

### 首次主网开仓检查清单（重要）
- `/start` 面板顶部显示 `💰 MAINNET` 和 `⚠️ 真钱环境`
- 默认参数先调小：`settings.json` 删掉，让它用 `.env` 里的 DEFAULT_*，建议 `DEFAULT_MARGIN_USDT=10 DEFAULT_LEVERAGE=3`
- 面板上开仓锁默认 🔒，必须用户主动解锁才能下单
- 第一笔跑通后再调回想要的参数

### 架构一句话
- `bot.py`：Telegram 入口、按钮面板、指令
- `trader.py`：交易引擎，开仓 + TP/SL + 应急平仓，带网络重试
- `settings.json`：运行时持久化的默认参数（首次启动自动生成）
- `.env`：凭据和初始默认值

### 可能的坑
- 有持仓时改杠杆会报 `-4067`，代码已自动跳过
- 时间戳 `-1021`：代码用 `recvWindow=10000`，网络抖动时能扛
- TP/SL 用新的 `/fapi/v1/algoOrder` 端点（2025-12-09 币安强制迁移）
- 市面上 `python-binance` 老 SDK 不支持 Algo Order，本项目用 `binance-futures-connector==4.1.0` + 直调 sign_request

### 开机自启（可选）
```bash
cp trader-bot.plist ~/Library/LaunchAgents/
# 修改里面的 /ABSOLUTE/PATH/TO/... 路径为当前机器上的实际项目路径
launchctl load ~/Library/LaunchAgents/trader-bot.plist
```
