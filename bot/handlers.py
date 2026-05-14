"""
命令处理器模块
处理Telegram Bot的命令和回调
"""
import asyncio
from typing import Dict, Any, Optional
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.keyboards import get_keyboard_manager
from bot.panels import get_panel_manager
from bot.watches import WatchManager
from trader import TradeExecutor, TradeError
from trader.client import create_client
from utils.config import get_config, ConfigError
from utils.logger import get_bot_logger
from utils.retry import retry_telegram_async

logger = get_bot_logger()


class BotHandlers:
    """Bot命令处理器"""
    
    def __init__(self, watch_manager: WatchManager):
        self.config = get_config()
        self.keyboard_manager = get_keyboard_manager()
        self.panel_manager = get_panel_manager()
        self.watch_manager = watch_manager
        
        # 待处理的自定义输入
        self.pending_input: Dict[int, str] = {}
        
        # Telegram网络错误计数
        self.telegram_network_errors = 0
        self.telegram_network_error_limit = 10
        
        logger.info("Bot命令处理器已初始化")
    
    def _authorized(self, user_id: int) -> bool:
        """
        检查用户是否授权
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否授权
        """
        allowed_ids = self.config.allowed_user_ids
        
        if not allowed_ids:
            logger.warning(f"未设置允许的用户ID，user_id={user_id}")
            return False
        
        return user_id in allowed_ids
    
    async def _deny(self, update: Update) -> None:
        """
        拒绝未授权访问
        
        Args:
            update: Telegram更新
        """
        user_id = update.effective_user.id if update.effective_user else 0
        msg = f"⛔ 未授权\n你的 user_id: `{user_id}`"
        
        if update.callback_query:
            await update.callback_query.answer("未授权", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    @retry_telegram_async
    async def _render_panel(self, update: Update, via_edit: bool = False) -> None:
        """
        渲染交易面板
        
        Args:
            update: Telegram更新
            via_edit: 是否通过编辑消息方式
        """
        try:
            settings = self.config.load_settings()
            text = self.panel_manager.generate_panel_text(settings)
            kb = self.keyboard_manager.create_panel_keyboard(settings)
            
            if via_edit and update.callback_query:
                await update.callback_query.edit_message_text(
                    text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.effective_chat.send_message(
                    text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                )
                
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            logger.error(f"渲染面板失败: {e}")
            await update.effective_chat.send_message(f"❌ 渲染面板失败: {e}")
        except ConfigError as e:
            logger.error(f"加载设置失败: {e}")
            await update.effective_chat.send_message(f"❌ 加载设置失败: {e}")
        except Exception as e:
            logger.error(f"渲染面板失败: {e}")
            await update.effective_chat.send_message(f"❌ 渲染面板失败: {e}")
    
    async def cmd_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        处理 /start 命令
        
        Args:
            update: Telegram更新
            _: 上下文
        """
        user_id = update.effective_user.id
        
        if not self._authorized(user_id):
            await update.message.reply_text(
                f"👋 你好\n你的 user_id: `{user_id}`\n⛔ 未授权",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        
        await self._render_panel(update)
    
    async def cmd_panel(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        处理 /panel 命令
        
        Args:
            update: Telegram更新
            _: 上下文
        """
        if not self._authorized(update.effective_user.id):
            await self._deny(update)
            return
        
        await self._render_panel(update)
    
    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        处理文本消息
        
        Args:
            update: Telegram更新
            _: 上下文
        """
        user_id = update.effective_user.id
        
        if not self._authorized(user_id):
            await self._deny(update)
            return
        
        text = (update.message.text or "").strip()
        
        # 处理自定义输入
        if user_id in self.pending_input:
            await self._handle_custom_input(update, text)
            return
        
        # 处理交易命令
        if not text or " " in text or len(text) > 20:
            return
        
        await self._execute_trade(update, text)
    
    async def _handle_custom_input(self, update: Update, text: str) -> None:
        """
        处理自定义输入
        
        Args:
            update: Telegram更新
            text: 输入文本
        """
        user_id = update.effective_user.id
        key = self.pending_input.pop(user_id)
        
        try:
            value = float(text)
        except ValueError:
            await update.message.reply_text("❌ 请输入有效数字")
            return
        
        # 验证输入
        settings = self.config.load_settings()
        is_valid, error_msg = self.panel_manager.validate_custom_input(key, value, settings)
        
        if not is_valid:
            await update.message.reply_text(f"❌ {error_msg}")
            return
        
        # 保存设置
        if key == "leverage":
            value = int(value)
        
        settings[key] = value
        self.config.save_settings(settings)
        
        # 发送确认消息
        label = self.panel_manager.get_setting_label(key)
        await update.message.reply_text(
            f"✅ {label} 已设为 `{value}`", 
            parse_mode=ParseMode.MARKDOWN
        )
        
        # 刷新面板
        await self._render_panel(update)
    
    async def _execute_trade(self, update: Update, token: str) -> None:
        """
        执行交易
        
        Args:
            update: Telegram更新
            token: 交易对
        """
        settings = self.config.load_settings()
        
        # 检查开仓锁
        if not settings.get("armed"):
            await update.effective_chat.send_message("🔒 开仓已锁定，请先在面板点【🔓 解锁开仓】")
            return
        
        # 准备交易参数
        split_tp = settings.get("tp_mode") == "split"
        env_tag = "🧪" if self.config.testnet else "💰"
        
        if split_tp:
            tp_desc = f"tp1+{settings['tp1_pct']:g}% tp2+{settings['tp2_pct']:g}%"
        else:
            tp_desc = f"tp+{settings['tp']:g}%"
        
        # 发送交易开始消息
        await update.effective_chat.send_message(
            f"{env_tag} 开仓中... *{token.upper()}* {settings['side']} "
            f"{settings['leverage']}x {settings['margin']:g}U {tp_desc} sl-{settings['sl']:g}%",
            parse_mode=ParseMode.MARKDOWN,
        )
        
        try:
            # 创建交易执行器
            executor = TradeExecutor(
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
                testnet=self.config.testnet
            )
            
            # 执行交易
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    executor.execute_trade,
                    token=token,
                    side=settings["side"],
                    leverage=settings["leverage"],
                    margin_usdt=settings["margin"],
                    tp_pct=settings["tp"],
                    sl_pct=settings["sl"],
                    split_tp=split_tp,
                    tp1_pct=settings.get("tp1_pct", 3.0),
                    tp2_pct=settings.get("tp2_pct", 5.0),
                ),
                timeout=45,
            )
            
            # 生成结果文本
            text = self.panel_manager.generate_trade_result_text(result, split_tp)
            await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN)
            
            # 如果是分批止盈，启动监控
            if split_tp:
                close_side = "SELL" if settings["side"] == "long" else "BUY"
                watch = {
                    "side": settings["side"],
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
                
                # 保存监控配置
                watches = self.config.load_watches()
                watches[result["symbol"]] = watch
                self.config.save_watches(watches)
                
                # 启动监控
                self.watch_manager.start_watch(result["symbol"], watch)
                
        except asyncio.TimeoutError:
            await update.effective_chat.send_message("❌ 交易执行超时，请检查 Binance 网络和仓位状态。")
        except TradeError as e:
            await update.effective_chat.send_message(f"❌ 交易失败: {e}")
        except Exception as e:
            logger.exception("交易异常")
            await update.effective_chat.send_message(f"💥 异常: {type(e).__name__}: {e}")
    
    async def on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """
        处理回调查询
        
        Args:
            update: Telegram更新
            ctx: 上下文
        """
        query = update.callback_query
        user_id = update.effective_user.id
        
        if not self._authorized(user_id):
            await query.answer("未授权", show_alert=True)
            return
        
        data = query.data or ""
        await query.answer()
        
        # 处理回调数据
        if data == "noop":
            return
        
        elif data == "refresh":
            await self._render_panel(update, via_edit=True)
        
        elif data == "toggle:side":
            await self._handle_toggle_side(update)
        
        elif data == "toggle:armed":
            await self._handle_toggle_armed(update)
        
        elif data == "menu:tp":
            await self._handle_tp_menu(update)
        
        elif data.startswith("tp_mode:"):
            await self._handle_tp_mode(update, data)
        
        elif data.startswith("menu:"):
            key = data.split(":", 1)[1]
            if key == "environment":
                await self._handle_environment_menu(update)
            else:
                await self._handle_menu(update, data)
        
        elif data.startswith("set:"):
            await self._handle_set(update, data)
        
        elif data.startswith("custom:"):
            await self._handle_custom(update, data)
        
        elif data.startswith("trade:"):
            await self._handle_trade(update, data)
        
        elif data == "positions":
            await self._handle_positions(update)
        
        elif data == "close_all_ask":
            await self._handle_close_all_ask(update)
        
        elif data == "close_all_do":
            await self._handle_close_all_do(update)
        
        elif data.startswith("confirm_env:"):
            await self._handle_confirm_environment(update, data)
        
        else:
            logger.warning(f"未知回调数据: {data}")
    
    async def _handle_toggle_side(self, update: Update) -> None:
        """处理切换交易方向"""
        settings = self.config.load_settings()
        settings["side"] = "short" if settings["side"] == "long" else "long"
        self.config.save_settings(settings)
        
        await self._render_panel(update, via_edit=True)
    
    async def _handle_toggle_armed(self, update: Update) -> None:
        """处理切换开仓锁"""
        settings = self.config.load_settings()
        settings["armed"] = not settings.get("armed", False)
        self.config.save_settings(settings)
        
        await self._render_panel(update, via_edit=True)
    
    async def _handle_tp_menu(self, update: Update) -> None:
        """处理止盈菜单"""
        settings = self.config.load_settings()
        kb = self.keyboard_manager.create_tp_menu_keyboard(settings)
        
        await update.callback_query.edit_message_text(
            "*🎯 止盈设置*",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    async def _handle_environment_menu(self, update: Update) -> None:
        """处理环境菜单"""
        settings = self.config.load_settings()
        kb = self.keyboard_manager.create_environment_menu_keyboard(settings)
        
        await update.callback_query.edit_message_text(
            "*🌍 环境设置*",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    async def _handle_tp_mode(self, update: Update, data: str) -> None:
        """处理止盈模式切换"""
        mode = data.split(":", 1)[1]
        settings = self.config.load_settings()
        settings["tp_mode"] = mode
        self.config.save_settings(settings)
        
        kb = self.keyboard_manager.create_tp_menu_keyboard(settings)
        await update.callback_query.edit_message_text(
            "*🎯 止盈设置*",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    async def _handle_menu(self, update: Update, data: str) -> None:
        """处理菜单选择"""
        key = data.split(":", 1)[1]
        settings = self.config.load_settings()
        
        title = self.panel_manager.get_menu_title(key)
        choices = self.keyboard_manager.get_choice_list(key)
        
        kb = self.keyboard_manager.create_choice_keyboard(
            key, choices, settings[key], "配置"
        )
        
        await update.callback_query.edit_message_text(
            f"*选择 {title}*\n\n当前: `{settings[key]}`",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    async def _handle_set(self, update: Update, data: str) -> None:
        """处理设置值"""
        _, key, raw = data.split(":", 2)
        settings = self.config.load_settings()
        
        if key == "environment":
            # 环境切换需要特殊处理
            new_environment = raw
            current_environment = settings.get("environment", "testnet")
            
            if new_environment == current_environment:
                # 环境未改变，直接刷新
                await self._render_panel(update, via_edit=True)
                return
            
            # 检查环境切换的安全性
            if new_environment == "mainnet":
                # 切换到生产环境前的警告
                env_info = self.config.get_environment_info()
                if not env_info.get("has_mainnet_keys"):
                    await update.callback_query.edit_message_text(
                        "❌ *无法切换到生产环境*\n\n"
                        "生产环境API密钥未设置。请检查：\n"
                        "1. 确保已设置 `BINANCE_API_KEY` 环境变量\n"
                        "2. 确保已设置 `BINANCE_API_SECRET` 环境变量\n"
                        "3. 重新启动机器人",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return
                
                # 确认切换到生产环境
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ 确认切换到生产环境", callback_data=f"confirm_env:{new_environment}"),
                    InlineKeyboardButton("❌ 取消", callback_data="refresh"),
                ]])
                
                await update.callback_query.edit_message_text(
                    "⚠️ *确认切换到生产环境*\n\n"
                    "生产环境使用真实资金交易！\n"
                    "请确认：\n"
                    "1. 已设置正确的生产环境API密钥\n"
                    "2. 了解生产环境交易风险\n"
                    "3. 确认当前没有测试环境的未平仓位",
                    reply_markup=kb,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            else:
                # 切换到测试环境
                settings[key] = new_environment
                self.config.save_settings(settings)
                
                # 发送环境切换通知
                await update.effective_chat.send_message(
                    f"✅ 已切换到测试环境\n"
                    f"现在可以使用测试网络进行交易",
                    parse_mode=ParseMode.MARKDOWN,
                )
                
                await self._render_panel(update, via_edit=True)
                return
        else:
            # 其他设置
            settings[key] = int(raw) if key == "leverage" else float(raw)
            self.config.save_settings(settings)
            
            await self._render_panel(update, via_edit=True)
    
    async def _handle_custom(self, update: Update, data: str) -> None:
        """处理自定义输入请求"""
        key = data.split(":", 1)[1]
        user_id = update.effective_user.id
        
        self.pending_input[user_id] = key
        hint = self.keyboard_manager.get_custom_input_hint(key)
        
        await update.callback_query.edit_message_text(
            f"✏️ 请直接发送数字\n_{hint}_",
            parse_mode=ParseMode.MARKDOWN,
        )
    
    async def _handle_trade(self, update: Update, data: str) -> None:
        """处理交易请求"""
        token = data.split(":", 1)[1]
        await self._execute_trade(update, token)
    
    async def _handle_positions(self, update: Update) -> None:
        """处理持仓查询"""
        try:
            client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
            
            # 获取持仓
            positions = client.get_position_risk()
            active_positions = [
                p for p in positions 
                if Decimal(p.get("positionAmt", "0")) != 0
            ]
            
            # 获取监控信息
            watches = self.config.load_watches()
            
            # 生成持仓文本
            text = self.panel_manager.generate_positions_text(active_positions, watches)
            await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            await update.effective_chat.send_message(f"❌ 获取持仓失败: {e}")
    
    async def _handle_close_all_ask(self, update: Update) -> None:
        """处理全部平仓确认请求"""
        kb = self.keyboard_manager.create_close_all_confirmation_keyboard()
        
        await update.callback_query.edit_message_text(
            "⚠️ *确认平掉全部持仓？*\n同时会撤销所有 TP/SL 挂单",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    async def _handle_close_all_do(self, update: Update) -> None:
        """处理全部平仓执行"""
        try:
            executor = TradeExecutor(
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
                testnet=self.config.testnet
            )
            
            result = executor.close_all_positions()
            text = self.panel_manager.generate_close_all_result_text(result)
            
            # 停止所有监控任务
            self.watch_manager.stop_all_watches()
            
            await update.callback_query.edit_message_text(text)
            
        except Exception as e:
            logger.error(f"全部平仓失败: {e}")
            await update.callback_query.edit_message_text(f"❌ 全部平仓失败: {e}")
    
    async def _handle_confirm_environment(self, update: Update, data: str) -> None:
        """处理环境切换确认"""
        new_environment = data.split(":", 1)[1]
        settings = self.config.load_settings()
        
        # 保存新环境设置
        settings["environment"] = new_environment
        self.config.save_settings(settings)
        
        # 发送环境切换成功消息
        if new_environment == "mainnet":
            env_name = "生产环境"
            warning = "\n\n⚠️ *警告：现在使用真实资金交易！*"
        else:
            env_name = "测试环境"
            warning = ""
        
        await update.callback_query.edit_message_text(
            f"✅ 已切换到 {env_name}{warning}",
            parse_mode=ParseMode.MARKDOWN,
        )
        
        # 刷新面板
        await self._render_panel(update)