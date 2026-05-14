from __future__ import annotations

import asyncio
import json
import logging
import os
from decimal import Decimal
from pathlib import Path

from telegram.error import NetworkError, TimedOut

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from trader import (
    execute_trade, make_client, TradeError,
    cancel_order, cancel_algo_order, place_sl_market,
    _round_step, _filter_value, _resolve_symbol,
)

# uid -> param key being awaited for custom input
_pending_input: dict[int, str] = {}

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_IDS = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip()}
API_KEY = os.environ["BINANCE_API_KEY"]
API_SECRET = os.environ["BINANCE_API_SECRET"]
TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

SETTINGS_FILE = Path(__file__).parent / "settings.json"
WATCHES_FILE = Path(__file__).parent / "watches.json"

DEFAULTS = {
    "side": os.getenv("DEFAULT_SIDE", "long"),
    "leverage": int(os.getenv("DEFAULT_LEVERAGE", "5")),
    "margin": float(os.getenv("DEFAULT_MARGIN_USDT", "50")),
    "tp": float(os.getenv("DEFAULT_TP_PCT", "5")),
    "sl": float(os.getenv("DEFAULT_SL_PCT", "2")),
    "armed": False,
    "tp_mode": "single",
    "tp1_pct": 3.0,
    "tp2_pct": 5.0,
}

LEVERAGE_CHOICES = [1, 3, 5, 10, 20, 50, 75, 100]
MARGIN_CHOICES = [10, 20, 50, 100, 200, 500, 1000]
TP_CHOICES = [1, 2, 3, 5, 8, 10, 15, 20]
SL_CHOICES = [0.5, 1, 2, 3, 5, 8, 10]
QUICK_TOKENS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP"]

# global app reference, set in main()
_app: Application | None = None

# active split-tp monitors: symbol -> asyncio.Task
_watch_tasks: dict[str, asyncio.Task] = {}
_telegram_network_errors = 0

TELEGRAM_NETWORK_ERROR_LIMIT = 10


# ── watches persistence ───────────────────────────────────────────────────────

def load_watches() -> dict:
    if WATCHES_FILE.exists():
        try:
            return json.loads(WATCHES_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_watches(w: dict) -> None:
    WATCHES_FILE.write_text(json.dumps(w, indent=2))


# ── settings ──────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        data = json.loads(SETTINGS_FILE.read_text())
        return {**DEFAULTS, **data}
    return dict(DEFAULTS)


def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


# ── auth ──────────────────────────────────────────────────────────────────────

def _authorized(uid: int) -> bool:
    if not ALLOWED_IDS:
        log.warning("TELEGRAM_ALLOWED_USER_IDS empty, uid=%s", uid)
        return False
    return uid in ALLOWED_IDS


async def _deny(update: Update) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    msg = f"⛔ 未授权\n你的 user_id: `{uid}`"
    if update.callback_query:
        await update.callback_query.answer("未授权", show_alert=True)
    elif update.message:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ── panel ─────────────────────────────────────────────────────────────────────

def _panel_text(s: dict) -> str:
    env = "🧪 Testnet" if TESTNET else "💰 *MAINNET*"
    side_tag = "↗️ LONG" if s["side"] == "long" else "↘️ SHORT"
    armed = "🟢 已解锁" if s.get("armed") else "🔒 已锁定"
    warn = "" if TESTNET else "\n⚠️ *真钱环境* 请再三确认参数"
    if s.get("tp_mode") == "split":
        tp_line = f"🎯 止盈: 分批 `+{s['tp1_pct']:g}%` / `+{s['tp2_pct']:g}%`"
    else:
        tp_line = f"🎯 止盈: `+{s['tp']:g}%`"
    return (
        f"*📊 交易配置*  {env}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔐 开仓锁: {armed}\n"
        f"🧭 方向: `{side_tag}`\n"
        f"⚡ 杠杆: `{s['leverage']}x`\n"
        f"💰 保证金: `{s['margin']} USDT`\n"
        f"{tp_line}\n"
        f"🛡 止损: `-{s['sl']:g}%`{warn}\n"
        f"\n_需先解锁才能点币种开仓_"
    )


def _panel_keyboard(s: dict) -> InlineKeyboardMarkup:
    side_btn_text = "🔄 切换到 SHORT" if s["side"] == "long" else "🔄 切换到 LONG"
    lock_btn = "🔒 锁定开仓" if s.get("armed") else "🔓 解锁开仓"
    tp_btn = "🎯 止盈 (分批)" if s.get("tp_mode") == "split" else f"🎯 止盈 +{s['tp']:g}%"
    rows = [
        [InlineKeyboardButton(lock_btn, callback_data="toggle:armed")],
        [InlineKeyboardButton(side_btn_text, callback_data="toggle:side")],
        [
            InlineKeyboardButton(f"⚡ 杠杆 {s['leverage']}x", callback_data="menu:leverage"),
            InlineKeyboardButton(f"💰 保证金 {s['margin']:g}U", callback_data="menu:margin"),
        ],
        [
            InlineKeyboardButton(tp_btn, callback_data="menu:tp"),
            InlineKeyboardButton(f"🛡 止损 -{s['sl']:g}%", callback_data="menu:sl"),
        ],
        [InlineKeyboardButton("── ⚡ 一键开仓 ──", callback_data="noop")],
    ]
    for i in range(0, len(QUICK_TOKENS), 3):
        rows.append([
            InlineKeyboardButton(t, callback_data=f"trade:{t}")
            for t in QUICK_TOKENS[i:i + 3]
        ])
    rows.append([
        InlineKeyboardButton("📊 持仓", callback_data="positions"),
        InlineKeyboardButton("✖️ 全部平仓", callback_data="close_all_ask"),
    ])
    rows.append([InlineKeyboardButton("🔄 刷新", callback_data="refresh")])
    return InlineKeyboardMarkup(rows)


def _choice_keyboard(key: str, choices: list, current, back_label: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for v in choices:
        mark = " ✓" if float(v) == float(current) else ""
        row.append(InlineKeyboardButton(f"{v:g}{mark}", callback_data=f"set:{key}:{v}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ 自定义", callback_data=f"custom:{key}")])
    rows.append([InlineKeyboardButton(f"⬅ 返回 {back_label}", callback_data="refresh")])
    return InlineKeyboardMarkup(rows)


def _tp_menu_keyboard(s: dict) -> InlineKeyboardMarkup:
    mode = s.get("tp_mode", "single")
    single_mark = " ✓" if mode == "single" else ""
    split_mark = " ✓" if mode == "split" else ""
    rows = [
        [
            InlineKeyboardButton(f"单一止盈{single_mark}", callback_data="tp_mode:single"),
            InlineKeyboardButton(f"分批止盈{split_mark}", callback_data="tp_mode:split"),
        ],
    ]
    if mode == "single":
        rows.append([InlineKeyboardButton("── 单一止盈设置 ──", callback_data="noop")])
        row = []
        for v in TP_CHOICES:
            mark = " ✓" if float(v) == float(s.get("tp", 5)) else ""
            row.append(InlineKeyboardButton(f"{v:g}%{mark}", callback_data=f"set:tp:{v}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("✏️ 自定义", callback_data="custom:tp")])
    else:
        rows.append([InlineKeyboardButton("── 分批止盈设置 ──", callback_data="noop")])
        rows.append([
            InlineKeyboardButton(f"第一批 TP1: +{s.get('tp1_pct', 3):g}%", callback_data="custom:tp1_pct"),
            InlineKeyboardButton(f"第二批 TP2: +{s.get('tp2_pct', 5):g}%", callback_data="custom:tp2_pct"),
        ])
        rows.append([InlineKeyboardButton(
            "ℹ️ 各平50%仓，TP1触发后止损移至成本价", callback_data="noop"
        )])
    rows.append([InlineKeyboardButton("⬅ 返回配置", callback_data="refresh")])
    return InlineKeyboardMarkup(rows)


async def _render_panel(update: Update, via_edit: bool = False) -> None:
    s = load_settings()
    text = _panel_text(s)
    kb = _panel_keyboard(s)
    if via_edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_chat.send_message(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ── split-tp monitor ──────────────────────────────────────────────────────────

WATCH_RETRY_ATTEMPTS = 3


async def _notify_plain(app: Application, chat_id: int, text: str) -> None:
    try:
        await app.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        log.warning("notify chat %s failed: %s", chat_id, e)


def _remove_watch(symbol: str) -> None:
    watches = load_watches()
    watches.pop(symbol, None)
    save_watches(watches)


async def _retry_watch_action(description: str, action) -> tuple[bool, object | None]:
    last_error = None
    for attempt in range(1, WATCH_RETRY_ATTEMPTS + 1):
        try:
            return True, action()
        except Exception as e:
            last_error = e
            log.warning("%s failed attempt %s/%s: %s", description, attempt, WATCH_RETRY_ATTEMPTS, e)
            if attempt < WATCH_RETRY_ATTEMPTS:
                await asyncio.sleep(2 * attempt)
    return False, last_error


async def _watch_split_tp(symbol: str, watch: dict, app: Application) -> None:
    """
    Monitor a split-TP position:
    - phase 1: wait for TP1 to fill (position drops to ~50%) → move SL to entry
    - phase 2: wait for TP2 to fill (position reaches 0) → clean up
    """
    client = make_client(API_KEY, API_SECRET, TESTNET)
    notify_chat = watch["notify_chat"]
    close_side = watch["close_side"]
    sl_id = watch["sl_id"]
    original_qty = Decimal(watch["qty"])
    entry = Decimal(watch["entry"])
    phase = watch.get("phase", 1)

    try:
        symbol_info = _resolve_symbol(client, symbol.replace("USDT", ""))
        tick = _filter_value(symbol_info, "PRICE_FILTER", "tickSize")
    except Exception as e:
        log.error("watch %s: failed to get tick size: %s", symbol, e)
        await _notify_plain(app, notify_chat, f"⚠️ {symbol} 监控启动失败，无法获取交易规则：{e}")
        return

    log.info("watch %s: started phase=%s", symbol, phase)

    while True:
        await asyncio.sleep(5)
        try:
            client = make_client(API_KEY, API_SECRET, TESTNET)
            ok, result = await _retry_watch_action(
                f"watch {symbol}: get position",
                lambda: client.get_position_risk(symbol=symbol, recvWindow=60000),
            )
            if not ok:
                await _notify_plain(app, notify_chat, f"⚠️ {symbol} 监控连续 {WATCH_RETRY_ATTEMPTS} 次获取持仓失败，已停止监控。错误：{result}")
                _remove_watch(symbol)
                break

            positions = result
            amt = abs(Decimal(positions[0].get("positionAmt", "0"))) if positions else Decimal("0")

            if amt == Decimal("0"):
                log.info("watch %s: position is closed, stopping watch", symbol)
                _remove_watch(symbol)
                await _notify_plain(app, notify_chat, f"✅ {symbol} 仓位已平，已停止本地分批止盈监控。")
                break

            if phase == 1:
                if amt <= original_qty * Decimal("0.6"):
                    log.info("watch %s: TP1 detected, moving SL to entry %s", symbol, entry)
                    try:
                        cancel_algo_order(client, symbol, sl_id)
                    except Exception as e:
                        log.warning("watch %s: cancel old SL failed: %s", symbol, e)

                    ok, result = await _retry_watch_action(
                        f"watch {symbol}: move SL to entry",
                        lambda: place_sl_market(client, symbol, close_side, entry, tick),
                    )
                    if not ok:
                        await _notify_plain(app, notify_chat, f"⚠️ {symbol} TP1 触发，但连续 {WATCH_RETRY_ATTEMPTS} 次移动止损到成本价失败，已停止监控。请手动检查仓位和止损。错误：{result}")
                        _remove_watch(symbol)
                        break

                    new_sl_id = result
                    watches = load_watches()
                    if symbol in watches:
                        watches[symbol]["sl_id"] = new_sl_id
                        watches[symbol]["phase"] = 2
                        save_watches(watches)
                    sl_id = new_sl_id
                    phase = 2
                    await _notify_plain(app, notify_chat, f"🎯 {symbol} TP1 已触发，止损已移至成本价 {entry}")

            elif phase == 2:
                if amt == Decimal("0"):
                    log.info("watch %s: position closed, stopping watch", symbol)
                    _remove_watch(symbol)
                    await _notify_plain(app, notify_chat, f"✅ {symbol} 仓位已全部平仓")
                    break

        except asyncio.CancelledError:
            log.info("watch %s: cancelled", symbol)
            break
        except Exception as e:
            log.warning("watch %s: poll error: %s", symbol, e)


def _start_watch(symbol: str, watch: dict, app: Application) -> None:
    if symbol in _watch_tasks and not _watch_tasks[symbol].done():
        _watch_tasks[symbol].cancel()
    task = asyncio.create_task(_watch_split_tp(symbol, watch, app))
    _watch_tasks[symbol] = task


# ── command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not _authorized(uid):
        await update.message.reply_text(
            f"👋 你好\n你的 user_id: `{uid}`\n⛔ 未授权",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await _render_panel(update)


async def cmd_panel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        await _deny(update)
        return
    await _render_panel(update)


# ── text handler ──────────────────────────────────────────────────────────────

async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not _authorized(uid):
        await _deny(update)
        return
    text = (update.message.text or "").strip()

    if uid in _pending_input:
        key = _pending_input.pop(uid)
        limits = {
            "leverage": (1, 125),
            "margin":   (1, 1_000_000),
            "tp":       (0.1, 100),
            "sl":       (0.1, 100),
            "tp1_pct":  (0.1, 100),
            "tp2_pct":  (0.1, 100),
        }
        try:
            value = float(text)
        except ValueError:
            await update.message.reply_text("❌ 请输入有效数字")
            return
        lo, hi = limits[key]
        if not (lo <= value <= hi):
            await update.message.reply_text(f"❌ 超出范围 ({lo} ~ {hi})")
            return
        if key == "leverage":
            value = int(value)
        # validate tp1 < tp2
        s = load_settings()
        if key == "tp1_pct" and value >= s.get("tp2_pct", 100):
            await update.message.reply_text("❌ TP1 必须小于 TP2")
            return
        if key == "tp2_pct" and value <= s.get("tp1_pct", 0):
            await update.message.reply_text("❌ TP2 必须大于 TP1")
            return
        s[key] = value
        save_settings(s)
        labels = {"leverage": "杠杆", "margin": "保证金", "tp": "止盈",
                  "sl": "止损", "tp1_pct": "TP1", "tp2_pct": "TP2"}
        await update.message.reply_text(f"✅ {labels[key]} 已设为 `{value}`", parse_mode=ParseMode.MARKDOWN)
        await _render_panel(update)
        return

    if not text or " " in text or len(text) > 20:
        return
    await _execute_and_reply(update, text, update.effective_chat.send_message)


# ── trade execution ───────────────────────────────────────────────────────────

async def _execute_and_reply(update: Update, token: str, sender) -> None:
    s = load_settings()
    if not s.get("armed"):
        await sender("🔒 开仓已锁定，请先在面板点【🔓 解锁开仓】")
        return

    split = s.get("tp_mode") == "split"
    env_tag = "🧪" if TESTNET else "💰"
    if split:
        tp_desc = f"tp1+{s['tp1_pct']:g}% tp2+{s['tp2_pct']:g}%"
    else:
        tp_desc = f"tp+{s['tp']:g}%"
    await sender(
        f"{env_tag} 开仓中... *{token.upper()}* {s['side']} "
        f"{s['leverage']}x {s['margin']:g}U {tp_desc} sl-{s['sl']:g}%",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        client = make_client(API_KEY, API_SECRET, TESTNET)
        result = execute_trade(
            client=client,
            token=token,
            side=s["side"],
            leverage=s["leverage"],
            margin_usdt=s["margin"],
            tp_pct=s["tp"],
            sl_pct=s["sl"],
            split_tp=split,
            tp1_pct=s.get("tp1_pct", 3.0),
            tp2_pct=s.get("tp2_pct", 5.0),
        )
    except TradeError as ex:
        await sender(f"❌ 交易失败: {ex}")
        return
    except Exception as ex:
        log.exception("trade crashed")
        await sender(f"💥 异常: {type(ex).__name__}: {ex}")
        return

    if result.get("split_tp"):
        text = (
            f"✅ *{result['symbol']}* {result['side']} (分批止盈)\n"
            f"• 成交价: `{result['entry']}`\n"
            f"• 数量: `{result['qty']}`\n"
            f"• 杠杆: `{result['leverage']}x`\n"
            f"• 保证金: `{result['margin']} USDT`\n"
            f"• TP1 (50%): `{result['tp1_price']}`\n"
            f"• TP2 (50%): `{result['tp2_price']}`\n"
            f"• 止损: `{result['sl_price']}`\n"
            f"• TP1触发后止损自动移至成本价"
        )
        await sender(text, parse_mode=ParseMode.MARKDOWN)

        # register watch
        close_side = "SELL" if s["side"] == "long" else "BUY"
        watch = {
            "side": s["side"],
            "close_side": close_side,
            "entry": result["entry"],
            "qty": result["qty"],
            "sl_id": result["sl_id"],
            "tp1_order_id": result["tp1_order_id"],
            "tp2_order_id": result["tp2_order_id"],
            "tp1_price": result["tp1_price"],
            "tp2_price": result["tp2_price"],
            "phase": 1,
            "notify_chat": update.effective_chat.id,
        }
        watches = load_watches()
        watches[result["symbol"]] = watch
        save_watches(watches)
        _start_watch(result["symbol"], watch, _app)
    else:
        text = (
            f"✅ *{result['symbol']}* {result['side']}\n"
            f"• 成交价: `{result['entry']}`\n"
            f"• 数量: `{result['qty']}`\n"
            f"• 杠杆: `{result['leverage']}x`\n"
            f"• 保证金: `{result['margin']} USDT`\n"
            f"• 止盈: `{result['tp_price']}`\n"
            f"• 止损: `{result['sl_price']}`"
        )
        await sender(text, parse_mode=ParseMode.MARKDOWN)


# ── callback handler ──────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = update.effective_user.id
    if not _authorized(uid):
        await q.answer("未授权", show_alert=True)
        return
    data = q.data or ""
    await q.answer()

    if data == "noop":
        return

    if data == "refresh":
        await _render_panel(update, via_edit=True)
        return

    if data == "toggle:side":
        s = load_settings()
        s["side"] = "short" if s["side"] == "long" else "long"
        save_settings(s)
        await _render_panel(update, via_edit=True)
        return

    if data == "toggle:armed":
        s = load_settings()
        s["armed"] = not s.get("armed", False)
        save_settings(s)
        await _render_panel(update, via_edit=True)
        return

    if data == "menu:tp":
        s = load_settings()
        await q.edit_message_text(
            "*🎯 止盈设置*",
            reply_markup=_tp_menu_keyboard(s),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("tp_mode:"):
        mode = data.split(":", 1)[1]
        s = load_settings()
        s["tp_mode"] = mode
        save_settings(s)
        await q.edit_message_text(
            "*🎯 止盈设置*",
            reply_markup=_tp_menu_keyboard(s),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("menu:"):
        key = data.split(":", 1)[1]
        s = load_settings()
        titles = {"leverage": "⚡ 杠杆", "margin": "💰 保证金 (USDT)", "sl": "🛡 止损 %"}
        choices = {"leverage": LEVERAGE_CHOICES, "margin": MARGIN_CHOICES, "sl": SL_CHOICES}
        await q.edit_message_text(
            f"*选择 {titles[key]}*\n\n当前: `{s[key]}`",
            reply_markup=_choice_keyboard(key, choices[key], s[key], "配置"),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("set:"):
        _, key, raw = data.split(":", 2)
        s = load_settings()
        s[key] = int(raw) if key == "leverage" else float(raw)
        save_settings(s)
        await _render_panel(update, via_edit=True)
        return

    if data.startswith("custom:"):
        key = data.split(":", 1)[1]
        hints = {
            "leverage": "1 ~ 125，整数",
            "margin": "USDT，最小 1",
            "tp": "止盈 %，如 2.5",
            "sl": "止损 %，如 1.5",
            "tp1_pct": "第一批止盈 %，如 3.0",
            "tp2_pct": "第二批止盈 %，如 5.0",
        }
        _pending_input[uid] = key
        await q.edit_message_text(
            f"✏️ 请直接发送数字\n_{hints.get(key, '')}_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("trade:"):
        token = data.split(":", 1)[1]
        await _execute_and_reply(update, token, update.effective_chat.send_message)
        return

    if data == "positions":
        client = make_client(API_KEY, API_SECRET, TESTNET)
        try:
            positions = client.sign_request("GET", "/fapi/v3/positionRisk", {})
        except Exception:
            positions = client.get_position_risk()
        active = [p for p in positions if Decimal(p.get("positionAmt", "0")) != 0]
        if not active:
            await update.effective_chat.send_message("📊 当前无持仓")
            return
        watches = load_watches()
        lines = ["*📊 持仓*"]
        for p in active:
            amt = Decimal(p["positionAmt"])
            side_tag = "LONG ↗️" if amt > 0 else "SHORT ↘️"
            pnl = Decimal(p.get("unRealizedProfit", "0"))
            pnl_tag = "🟢" if pnl >= 0 else "🔴"
            sym = p["symbol"]
            watch_info = ""
            if sym in watches:
                ph = watches[sym].get("phase", 1)
                watch_info = f"\n• 分批止盈: {'等待TP1' if ph == 1 else '等待TP2 (止损已保本)'}"
            lines.append(
                f"\n*{sym}* {side_tag}\n"
                f"• 数量: `{abs(amt)}`\n"
                f"• 开仓价: `{p['entryPrice']}`\n"
                f"• 标记价: `{p.get('markPrice', '?')}`\n"
                f"• 未实现: {pnl_tag} `{pnl}` USDT"
                f"{watch_info}"
            )
        await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "close_all_ask":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 确认全部平仓", callback_data="close_all_do"),
            InlineKeyboardButton("❌ 取消", callback_data="refresh"),
        ]])
        await q.edit_message_text("⚠️ *确认平掉全部持仓？*\n同时会撤销所有 TP/SL 挂单", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return

    if data == "close_all_do":
        client = make_client(API_KEY, API_SECRET, TESTNET)
        try:
            positions = client.sign_request("GET", "/fapi/v3/positionRisk", {})
        except Exception:
            positions = client.get_position_risk()
        closed = []
        for p in positions:
            amt = Decimal(p.get("positionAmt", "0"))
            if amt == 0:
                continue
            symbol = p["symbol"]
            close_side = "SELL" if amt > 0 else "BUY"
            try:
                client.new_order(symbol=symbol, side=close_side, type="MARKET",
                                 quantity=format(abs(amt), "f"), reduceOnly="true")
                try:
                    client.sign_request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
                except Exception:
                    pass
                # cancel watch
                if symbol in _watch_tasks:
                    _watch_tasks[symbol].cancel()
                watches = load_watches()
                watches.pop(symbol, None)
                save_watches(watches)
                closed.append(f"• {symbol} ({abs(amt)})")
            except Exception as ex:
                closed.append(f"• {symbol} ❌ {ex}")
        msg = "✅ 已平仓:\n" + "\n".join(closed) if closed else "无持仓可平"
        await q.edit_message_text(msg)
        return


# ── startup / main ────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    """Restore watches from disk on startup."""
    global _app
    _app = app
    watches = load_watches()
    if watches:
        log.info("restoring %d watch(es) from disk", len(watches))
        for symbol, watch in watches.items():
            _start_watch(symbol, watch, app)
            log.info("restored watch: %s phase=%s", symbol, watch.get("phase", 1))


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _telegram_network_errors
    error = ctx.error
    if isinstance(error, (NetworkError, TimedOut)):
        _telegram_network_errors += 1
        log.warning(
            "telegram network error %s/%s: %s",
            _telegram_network_errors,
            TELEGRAM_NETWORK_ERROR_LIMIT,
            error,
        )
        if _telegram_network_errors >= TELEGRAM_NETWORK_ERROR_LIMIT:
            log.error("telegram network error limit reached, exiting for launchd restart")
            os._exit(75)
        return

    _telegram_network_errors = 0
    log.exception("telegram handler error", exc_info=error)


def _telegram_httpx_kwargs(ca_bundle: str | None) -> dict:
    kwargs = {"trust_env": False}
    if ca_bundle:
        kwargs["verify"] = ca_bundle
    return kwargs


def main() -> None:
    global _app
    ca_bundle = os.getenv("SSL_CERT_FILE")
    bot_request = HTTPXRequest(
        connection_pool_size=32,
        connect_timeout=10,
        read_timeout=20,
        write_timeout=10,
        pool_timeout=10,
        httpx_kwargs=_telegram_httpx_kwargs(ca_bundle),
    )
    updates_request = HTTPXRequest(
        connection_pool_size=2,
        connect_timeout=10,
        read_timeout=35,
        write_timeout=10,
        pool_timeout=10,
        httpx_kwargs=_telegram_httpx_kwargs(ca_bundle),
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(bot_request)
        .get_updates_request(updates_request)
        .post_init(post_init)
        .build()
    )
    _app = app
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("config", cmd_panel))
    app.add_handler(CommandHandler("help", cmd_panel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    log.info("bot starting (testnet=%s)", TESTNET)
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()

