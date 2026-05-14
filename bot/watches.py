"""
监控任务模块
处理分批止盈的用户数据流监控
"""
import asyncio
import json
import threading
from decimal import Decimal
from typing import Any, Dict, Optional

from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
from telegram.ext import Application

from trader.client import create_client, _filter_value
from trader.orders import OrderManager
from utils.config import get_config
from utils.retry import format_exception
from utils.logger import get_watch_logger

logger = get_watch_logger()


class WatchError(Exception):
    """监控错误"""
    pass


class WatchManager:
    """监控管理器"""

    def __init__(self, app: Application):
        self.config = get_config()
        self.app = app
        self.loop = asyncio.get_running_loop()
        self.watch_tasks: Dict[str, asyncio.Task] = {}
        self.ws_client: Optional[UMFuturesWebsocketClient] = None
        self.listen_key: Optional[str] = None
        self.keepalive_task: Optional[asyncio.Task] = None
        self.reconnect_task: Optional[asyncio.Task] = None
        self.stream_lock = threading.Lock()
        self.stopping = False
        self.reconnect_attempts = 0
        self.restore_alerts_sent: set[tuple[str, str, str]] = set()

    async def notify_plain(self, chat_id: int, text: str) -> None:
        """发送普通通知"""
        try:
            await self.app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.warning(f"通知聊天 {chat_id} 失败: {format_exception(e)}")

    async def _notify_restore_issue(self, symbol: str, watch: Dict[str, Any], state: str, reason: str) -> None:
        """通知恢复阶段发现的高风险监控异常"""
        if not self._should_alert_restore_reason(reason):
            logger.info(f"恢复异常已降噪为仅日志: {symbol} state={state} reason={reason}")
            return

        dedupe_key = (symbol, state, reason)
        if dedupe_key in self.restore_alerts_sent:
            logger.info(f"跳过重复恢复告警: {symbol} state={state} reason={reason}")
            return

        chat_id = watch.get("notify_chat")
        if not chat_id:
            logger.warning(f"监控任务恢复异常缺少 notify_chat: {symbol} state={state} reason={reason}")
            return

        phase = watch.get("phase", 1)
        result = "已从本地清理" if state == "stale" else "本次未恢复监控"
        reason_text = self._format_restore_reason(reason)

        text = (
            f"⚠️ 恢复监控时发现 {symbol} 状态异常 "
            f"(phase={phase}，原因：{reason_text})，{result}，请手动检查仓位和挂单。"
        )
        self.restore_alerts_sent.add(dedupe_key)
        await self.notify_plain(chat_id, text)

    def _should_alert_restore_reason(self, reason: str) -> bool:
        """判断恢复异常是否需要主动告警"""
        alert_reasons = {
            "state_check_failed",
            "tp1_missing",
            "tp2_missing",
            "sl_missing",
            "phase_conflict_tp2",
            "phase_conflict_sl",
            "phase_unknown",
        }
        return reason in alert_reasons

    def _reset_restore_alerts(self) -> None:
        """开始新一轮恢复前清空本轮告警去重状态"""
        self.restore_alerts_sent.clear()

    def _format_restore_reason(self, reason: str) -> str:
        """将恢复阶段内部原因转换为更易读的提示"""
        reason_map = {
            "state_check_failed": "状态校验失败",
            "position_missing": "未查到仓位信息",
            "position_closed": "仓位已平",
            "tp1_missing": "TP1 订单不存在或已失效",
            "tp2_missing": "TP2 订单不存在或已失效",
            "sl_missing": "止损单不存在或已失效",
            "phase1_orders_ok": "Phase 1 订单状态正常",
            "phase2_orders_ok": "Phase 2 订单状态正常",
            "phase_conflict_tp2": "TP2 状态与本地监控不一致",
            "phase_conflict_sl": "止损单状态与本地监控不一致",
            "phase_unknown": "本地监控阶段未知",
        }
        return reason_map.get(reason, reason)

    def remove_watch(self, symbol: str) -> None:
        """移除监控任务"""
        watches = self.config.load_watches()
        watches.pop(symbol, None)
        self.config.save_watches(watches)
        self.watch_tasks.pop(symbol, None)
        logger.info(f"监控任务已移除: {symbol}")

    def start_watch(self, symbol: str, watch: Dict[str, Any]) -> None:
        """注册监控任务并确保用户数据流运行"""
        self.watch_tasks[symbol] = asyncio.create_task(self._watch_placeholder(symbol))
        self._ensure_stream()
        logger.info(f"监控任务已启动: {symbol} phase={watch.get('phase', 1)}")

    async def _watch_placeholder(self, symbol: str) -> None:
        try:
            while symbol in self.config.load_watches():
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info(f"监控 {symbol}: 已取消")

    def stop_watch(self, symbol: str) -> bool:
        """停止监控任务"""
        exists = symbol in self.config.load_watches() or symbol in self.watch_tasks
        task = self.watch_tasks.get(symbol)
        if task and not task.done():
            task.cancel()
        self.remove_watch(symbol)
        if not self.config.load_watches():
            self.stop_user_stream()
        logger.info(f"监控任务已停止: {symbol}")
        return exists

    def stop_all_watches(self) -> None:
        """停止所有监控任务"""
        for task in self.watch_tasks.values():
            if not task.done():
                task.cancel()
        self.watch_tasks.clear()
        self.stop_user_stream()
        logger.info("所有监控任务已停止")

    def _reset_restore_alerts(self) -> None:
        """开始新一轮恢复前清空本轮告警去重状态"""
        self.restore_alerts_sent.clear()

    def _format_restore_reason(self, reason: str) -> str:
        """将恢复阶段内部原因转换为更易读的提示"""
        reason_map = {
            "state_check_failed": "状态校验失败",
            "position_missing": "未查到仓位信息",
            "position_closed": "仓位已平",
            "tp1_missing": "TP1 订单不存在或已失效",
            "tp2_missing": "TP2 订单不存在或已失效",
            "sl_missing": "止损单不存在或已失效",
            "phase1_orders_ok": "Phase 1 订单状态正常",
            "phase2_orders_ok": "Phase 2 订单状态正常",
            "phase_conflict_tp2": "TP2 状态与本地监控不一致",
            "phase_conflict_sl": "止损单状态与本地监控不一致",
            "phase_unknown": "本地监控阶段未知",
        }
        return reason_map.get(reason, reason)

    def remove_watch(self, symbol: str) -> None:
        """移除监控任务"""
        watches = self.config.load_watches()
        watches.pop(symbol, None)
        self.config.save_watches(watches)
        self.watch_tasks.pop(symbol, None)
        logger.info(f"监控任务已移除: {symbol}")

    def start_watch(self, symbol: str, watch: Dict[str, Any]) -> None:
        """注册监控任务并确保用户数据流运行"""
        self.watch_tasks[symbol] = asyncio.create_task(self._watch_placeholder(symbol))
        self._ensure_stream()
        logger.info(f"监控任务已启动: {symbol} phase={watch.get('phase', 1)}")

    async def _watch_placeholder(self, symbol: str) -> None:
        try:
            while symbol in self.config.load_watches():
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info(f"监控 {symbol}: 已取消")

    def stop_watch(self, symbol: str) -> bool:
        """停止监控任务"""
        exists = symbol in self.config.load_watches() or symbol in self.watch_tasks
        task = self.watch_tasks.get(symbol)
        if task and not task.done():
            task.cancel()
        self.remove_watch(symbol)
        if not self.config.load_watches():
            self.stop_user_stream()
        logger.info(f"监控任务已停止: {symbol}")
        return exists

    def stop_all_watches(self) -> None:
        """停止所有监控任务"""
        for task in self.watch_tasks.values():
            if not task.done():
                task.cancel()
        self.watch_tasks.clear()
        self.stop_user_stream()
        logger.info("所有监控任务已停止")

    async def _notify_restore_summary(
        self,
        restored_symbols: list[str],
        stale_symbols: list[str],
        skipped_symbols: list[str],
    ) -> None:
        """发送恢复结果汇总通知"""
        watches = self.config.load_watches()
        chat_ids = {
            watch.get("notify_chat")
            for watch in watches.values()
            if watch.get("notify_chat")
        }

        if not chat_ids:
            logger.info("恢复汇总未发送：没有可用的 notify_chat")
            return

        def format_symbols(symbols: list[str]) -> str:
            return "、".join(symbols) if symbols else "无"

        text = (
            "📋 本次监控恢复完成\n"
            f"恢复成功: {len(restored_symbols)} ({format_symbols(restored_symbols)})\n"
            f"已清理: {len(stale_symbols)} ({format_symbols(stale_symbols)})\n"
            f"待人工检查: {len(skipped_symbols)} ({format_symbols(skipped_symbols)})"
        )

        for chat_id in chat_ids:
            await self.notify_plain(chat_id, text)

    def restore_watches(self) -> None:
        """从磁盘恢复监控任务"""
        asyncio.create_task(self._restore_watches_async())

    async def _restore_watches_async(self) -> None:
        """并发校验并恢复监控任务"""
        self._reset_restore_alerts()
        watches = self.config.load_watches()
        if not watches:
            logger.info("没有需要恢复的监控任务")
            return

        watch_items = list(watches.items())
        logger.info(f"开始校验并恢复 {len(watch_items)} 个监控任务")

        state_tasks = [
            asyncio.to_thread(self._get_restore_state, symbol, watch)
            for symbol, watch in watch_items
        ]
        states = await asyncio.gather(*state_tasks)

        restored = 0
        stale = 0
        skipped = 0
        restored_symbols: list[str] = []
        stale_symbols: list[str] = []
        skipped_symbols: list[str] = []

        for (symbol, watch), (state, reason) in zip(watch_items, states):
            if state == "valid":
                self.watch_tasks[symbol] = asyncio.create_task(self._watch_placeholder(symbol))
                restored += 1
                restored_symbols.append(symbol)
                logger.info(f"监控任务已恢复: {symbol} phase={watch.get('phase', 1)} reason={reason}")
            elif state == "stale":
                self.remove_watch(symbol)
                stale += 1
                stale_symbols.append(symbol)
                logger.info(f"监控任务已清理: {symbol} reason={reason}")
                asyncio.create_task(self._notify_restore_issue(symbol, watch, state, reason))
            else:
                skipped += 1
                skipped_symbols.append(symbol)
                logger.warning(f"监控任务跳过恢复: {symbol} reason={reason}")
                asyncio.create_task(self._notify_restore_issue(symbol, watch, state, reason))

        if skipped and not restored and not stale:
            logger.warning("监控任务校验未能确认任何实时状态，本次未恢复本地 watch")

        if restored:
            self._ensure_stream()
        elif stale and not skipped:
            logger.info("监控任务校验完成，所有本地 watch 都已清理")

        logger.info(
            f"监控任务恢复完成: restored={restored}, stale={stale}, skipped={skipped}"
        )
        asyncio.create_task(
            self._notify_restore_summary(restored_symbols, stale_symbols, skipped_symbols)
        )

        return

        if skipped and not restored and not stale:
            logger.warning("监控任务校验未能确认任何实时状态，本次未恢复本地 watch")

        if restored:
            self._ensure_stream()
        elif stale and not skipped:
            logger.info("监控任务校验完成，所有本地 watch 都已清理")

        logger.info(
            f"监控任务恢复完成: restored={restored}, stale={stale}, skipped={skipped}"
        )
        asyncio.create_task(
            self._notify_restore_summary(restored_symbols, stale_symbols, skipped_symbols)
        )

    def _get_restore_state(self, symbol: str, watch: Dict[str, Any]) -> tuple[str, str]:
        """检查监控任务是否仍需恢复"""
        try:
            client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
            positions = client.get_position_risk(symbol=symbol)
        except Exception as e:
            logger.warning(f"校验监控任务失败 {symbol}: {format_exception(e)}")
            return "unknown", "state_check_failed"

        if not positions:
            return "stale", "position_missing"

        position = positions[0]
        amount = Decimal(position.get("positionAmt", "0"))
        if amount == Decimal("0"):
            return "stale", "position_closed"

        try:
            phase = int(watch.get("phase", 1))
            if phase == 1:
                if not self._is_regular_order_active(client, symbol, watch.get("tp1_order_id")):
                    return "stale", "tp1_missing"
                if not self._is_regular_order_active(client, symbol, watch.get("tp2_order_id")):
                    return "stale", "tp2_missing"
                if not self._is_algo_order_active(client, symbol, watch.get("sl_id")):
                    return "stale", "sl_missing"
                return "valid", "phase1_orders_ok"

            if phase == 2:
                if not self._is_regular_order_active(client, symbol, watch.get("tp2_order_id")):
                    return "unknown", "phase_conflict_tp2"
                if not self._is_algo_order_active(client, symbol, watch.get("sl_id")):
                    return "unknown", "phase_conflict_sl"
                return "valid", "phase2_orders_ok"

            return "unknown", "phase_unknown"
        except Exception as e:
            logger.warning(f"校验订单状态失败 {symbol}: {format_exception(e)}")
            return "unknown", "state_check_failed"

    def _get_restore_state(self, symbol: str, watch: Dict[str, Any]) -> tuple[str, str]:
        """检查监控任务是否仍需恢复"""
        try:
            client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
            positions = client.get_position_risk(symbol=symbol)
        except Exception as e:
            logger.warning(f"校验监控任务失败 {symbol}: {format_exception(e)}")
            return "unknown", "state_check_failed"

        if not positions:
            return "stale", "position_missing"

        position = positions[0]
        amount = Decimal(position.get("positionAmt", "0"))
        if amount == Decimal("0"):
            return "stale", "position_closed"

        try:
            phase = int(watch.get("phase", 1))
            if phase == 1:
                if not self._is_regular_order_active(client, symbol, watch.get("tp1_order_id")):
                    return "stale", "tp1_missing"
                if not self._is_regular_order_active(client, symbol, watch.get("tp2_order_id")):
                    return "stale", "tp2_missing"
                if not self._is_algo_order_active(client, symbol, watch.get("sl_id")):
                    return "stale", "sl_missing"
                return "valid", "phase1_orders_ok"

            if phase == 2:
                if not self._is_regular_order_active(client, symbol, watch.get("tp2_order_id")):
                    return "unknown", "phase_conflict_tp2"
                if not self._is_algo_order_active(client, symbol, watch.get("sl_id")):
                    return "unknown", "phase_conflict_sl"
                return "valid", "phase2_orders_ok"

            return "unknown", "phase_unknown"
        except Exception as e:
            logger.warning(f"校验订单状态失败 {symbol}: {format_exception(e)}")
            return "unknown", "state_check_failed"

    def _is_regular_order_active(self, client, symbol: str, order_id: Any) -> bool:
        """检查普通订单是否仍然有效"""
        if order_id is None:
            return False

        order = client.query_order(symbol=symbol, orderId=int(order_id))
        status = (order.get("status") or "").upper()
        return status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}

    def _is_algo_order_active(self, client, symbol: str, algo_id: Any) -> bool:
        """检查条件单是否仍然有效"""
        if algo_id is None:
            return False

        order = client.query_algo_order(symbol=symbol, algoId=int(algo_id))
        status = (order.get("status") or order.get("algoStatus") or "").upper()
        return status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}

    def _ensure_stream(self) -> None:
        if self.stopping or self.ws_client or self.reconnect_task:
            return
        self.reconnect_task = asyncio.create_task(self._connect_user_stream())

    async def _connect_user_stream(self) -> None:
        try:
            while not self.stopping and self.config.load_watches():
                try:
                    await self._open_user_stream()
                    self.reconnect_attempts = 0
                    return
                except Exception as e:
                    self.reconnect_attempts += 1
                    delay = min(60, 5 * self.reconnect_attempts)
                    logger.warning(
                        f"用户数据流连接失败，第 {self.reconnect_attempts} 次，{delay}s 后重试: {format_exception(e)}"
                    )
                    await asyncio.sleep(delay)
        finally:
            self.reconnect_task = None

    def restore_watches(self) -> None:
        """从磁盘恢复监控任务"""
        self._reset_restore_alerts()
        watches = self.config.load_watches()
        if not watches:
            logger.info("没有需要恢复的监控任务")
            return

        logger.info(f"开始校验并恢复 {len(watches)} 个监控任务")
        restored = 0
        stale = 0
        skipped = 0

        for symbol, watch in watches.items():
            state, reason = self._get_restore_state(symbol, watch)
            if state == "valid":
                self.watch_tasks[symbol] = asyncio.create_task(self._watch_placeholder(symbol))
                restored += 1
                logger.info(f"监控任务已恢复: {symbol} phase={watch.get('phase', 1)} reason={reason}")
            elif state == "stale":
                self.remove_watch(symbol)
                stale += 1
                logger.info(f"监控任务已清理: {symbol} reason={reason}")
                asyncio.create_task(self._notify_restore_issue(symbol, watch, state, reason))
            else:
                skipped += 1
                logger.warning(f"监控任务跳过恢复: {symbol} reason={reason}")
                asyncio.create_task(self._notify_restore_issue(symbol, watch, state, reason))

        if skipped and not restored and not stale:
            logger.warning("监控任务校验未能确认任何实时状态，本次未恢复本地 watch")

        if restored:
            self._ensure_stream()
        elif stale and not skipped:
            logger.info("监控任务校验完成，所有本地 watch 都已清理")

        logger.info(
            f"监控任务恢复完成: restored={restored}, stale={stale}, skipped={skipped}"
        )

    def _get_restore_state(self, symbol: str, watch: Dict[str, Any]) -> tuple[str, str]:
        """检查监控任务是否仍需恢复"""
        try:
            client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
            positions = client.get_position_risk(symbol=symbol)
        except Exception as e:
            logger.warning(f"校验监控任务失败 {symbol}: {format_exception(e)}")
            return "unknown", "state_check_failed"

        if not positions:
            return "stale", "position_missing"

        position = positions[0]
        amount = Decimal(position.get("positionAmt", "0"))
        if amount == Decimal("0"):
            return "stale", "position_closed"

        try:
            phase = int(watch.get("phase", 1))
            if phase == 1:
                if not self._is_regular_order_active(client, symbol, watch.get("tp1_order_id")):
                    return "stale", "tp1_missing"
                if not self._is_regular_order_active(client, symbol, watch.get("tp2_order_id")):
                    return "stale", "tp2_missing"
                if not self._is_algo_order_active(client, symbol, watch.get("sl_id")):
                    return "stale", "sl_missing"
                return "valid", "phase1_orders_ok"

            if phase == 2:
                if not self._is_regular_order_active(client, symbol, watch.get("tp2_order_id")):
                    return "unknown", "phase_conflict_tp2"
                if not self._is_algo_order_active(client, symbol, watch.get("sl_id")):
                    return "unknown", "phase_conflict_sl"
                return "valid", "phase2_orders_ok"

            return "unknown", "phase_unknown"
        except Exception as e:
            logger.warning(f"校验订单状态失败 {symbol}: {format_exception(e)}")
            return "unknown", "state_check_failed"

    def _is_regular_order_active(self, client, symbol: str, order_id: Any) -> bool:
        """检查普通订单是否仍然有效"""
        if order_id is None:
            return False

        order = client.query_order(symbol=symbol, orderId=int(order_id))
        status = (order.get("status") or "").upper()
        return status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}

    def _is_algo_order_active(self, client, symbol: str, algo_id: Any) -> bool:
        """检查条件单是否仍然有效"""
        if algo_id is None:
            return False

        order = client.query_algo_order(symbol=symbol, algoId=int(algo_id))
        status = (order.get("status") or order.get("algoStatus") or "").upper()
        return status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}

    def _ensure_stream(self) -> None:
        if self.stopping or self.ws_client or self.reconnect_task:
            return
        self.reconnect_task = asyncio.create_task(self._connect_user_stream())

    async def _connect_user_stream(self) -> None:
        try:
            while not self.stopping and self.config.load_watches():
                try:
                    await self._open_user_stream()
                    self.reconnect_attempts = 0
                    return
                except Exception as e:
                    self.reconnect_attempts += 1
                    delay = min(60, 5 * self.reconnect_attempts)
                    logger.warning(
                        f"用户数据流连接失败，第 {self.reconnect_attempts} 次，{delay}s 后重试: {format_exception(e)}"
                    )
                    await asyncio.sleep(delay)
        finally:
            self.reconnect_task = None

    async def _open_user_stream(self) -> None:
        client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
        resp = await asyncio.to_thread(client.new_listen_key)
        listen_key = resp.get("listenKey") if isinstance(resp, dict) else None
        if not listen_key:
            raise WatchError(f"创建 listenKey 失败: {resp}")

        stream_url = "wss://stream.binancefuture.com" if self.config.testnet else "wss://fstream.binance.com"
        ws_client = UMFuturesWebsocketClient(
            stream_url=stream_url,
            on_message=self._on_ws_message,
            on_close=self._on_ws_close,
            on_error=self._on_ws_error,
        )
        ws_client.user_data(listen_key)

        with self.stream_lock:
            self.listen_key = listen_key
            self.ws_client = ws_client

        self.keepalive_task = asyncio.create_task(self._keepalive_listen_key())
        logger.info(f"Binance 用户数据 WebSocket 已启动 (testnet={self.config.testnet})")

    async def _keepalive_listen_key(self) -> None:
        try:
            while not self.stopping and self.listen_key:
                await asyncio.sleep(30 * 60)
                listen_key = self.listen_key
                if not listen_key:
                    return
                client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
                await asyncio.to_thread(client.renew_listen_key, listen_key)
                logger.info("Binance listenKey 已续期")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"listenKey 续期失败: {format_exception(e)}")
            self._schedule_reconnect()

    def stop_user_stream(self) -> None:
        self.stopping = True
        if self.keepalive_task and not self.keepalive_task.done():
            self.keepalive_task.cancel()
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()

        with self.stream_lock:
            ws_client = self.ws_client
            listen_key = self.listen_key
            self.ws_client = None
            self.listen_key = None

        if ws_client:
            try:
                ws_client.stop()
            except Exception as e:
                logger.warning(f"关闭 WebSocket 失败: {format_exception(e)}")

        if listen_key:
            try:
                client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
                client.close_listen_key(listen_key)
            except Exception as e:
                logger.debug(f"关闭 listenKey 失败: {format_exception(e)}")

        self.stopping = False

    def _on_ws_message(self, _, raw: str) -> None:
        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self.handle_ws_message(raw))
        )

    def _on_ws_close(self, *_: Any) -> None:
        logger.warning("Binance 用户数据 WebSocket 已关闭")
        self._schedule_reconnect()

    def _on_ws_error(self, _, error: Exception) -> None:
        logger.warning(f"Binance 用户数据 WebSocket 错误: {format_exception(error)}")
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        def schedule() -> None:
            if self.stopping or self.reconnect_task:
                return
            self._clear_stream_state()
            if self.config.load_watches():
                self.reconnect_task = asyncio.create_task(self._connect_user_stream())

        self.loop.call_soon_threadsafe(schedule)

    def _clear_stream_state(self) -> None:
        if self.keepalive_task and not self.keepalive_task.done():
            self.keepalive_task.cancel()
        with self.stream_lock:
            self.ws_client = None
            self.listen_key = None

    async def handle_ws_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug(f"忽略非 JSON WebSocket 消息: {raw}")
            return

        event_type = message.get("e")
        if event_type == "ORDER_TRADE_UPDATE":
            await self._handle_order_trade_update(message)
        elif event_type == "ACCOUNT_UPDATE":
            await self._handle_account_update(message)
        elif event_type == "listenKeyExpired":
            logger.warning("Binance listenKey 已过期，准备重连")
            self._schedule_reconnect()

    async def _handle_order_trade_update(self, message: Dict[str, Any]) -> None:
        order = message.get("o", {})
        symbol = order.get("s")
        status = order.get("X")
        order_id = order.get("i")
        if not symbol or status != "FILLED" or order_id is None:
            return

        watches = self.config.load_watches()
        watch = watches.get(symbol)
        if not watch:
            return

        if int(order_id) == int(watch.get("tp1_order_id", -1)) and watch.get("phase", 1) == 1:
            await self._move_sl_to_entry(symbol, watch)
        elif int(order_id) == int(watch.get("tp2_order_id", -1)):
            self.remove_watch(symbol)
            await self.notify_plain(watch["notify_chat"], f"✅ {symbol} TP2 已成交，分批止盈监控已结束。")

    async def _handle_account_update(self, message: Dict[str, Any]) -> None:
        account = message.get("a", {})
        positions = account.get("P", [])
        if not positions:
            return

        watches = self.config.load_watches()
        for position in positions:
            symbol = position.get("s")
            if symbol not in watches:
                continue
            amount = Decimal(position.get("pa", "0"))
            if amount == Decimal("0"):
                watch = watches[symbol]
                self.remove_watch(symbol)
                await self.notify_plain(
                    watch["notify_chat"],
                    f"✅ {symbol} 仓位已平，已停止本地分批止盈监控。",
                )

    async def _move_sl_to_entry(self, symbol: str, watch: Dict[str, Any]) -> None:
        notify_chat = watch["notify_chat"]
        close_side = watch["close_side"]
        entry = Decimal(watch["entry"])
        sl_id = watch["sl_id"]

        try:
            client = create_client(self.config.api_key, self.config.api_secret, self.config.testnet)
            order_manager = OrderManager(client)
            symbol_info = await asyncio.to_thread(order_manager.resolve_symbol, symbol.replace("USDT", ""))
            tick = _filter_value(symbol_info, "PRICE_FILTER", "tickSize")

            await asyncio.to_thread(order_manager.cancel_algo_order_safe, symbol, sl_id)
            new_sl_id = await asyncio.to_thread(
                order_manager.place_stop_loss_market,
                symbol,
                close_side,
                entry,
                tick,
            )

            watches = self.config.load_watches()
            if symbol in watches:
                watches[symbol]["sl_id"] = new_sl_id
                watches[symbol]["phase"] = 2
                self.config.save_watches(watches)

            await self.notify_plain(notify_chat, f"🎯 {symbol} TP1 已成交，止损已移至成本价 {entry}")
            logger.info(f"监控 {symbol}: TP1 已成交，止损已移至成本价")
        except Exception as e:
            logger.error(f"监控 {symbol}: 移动止损失败: {format_exception(e)}")
            await self.notify_plain(
                notify_chat,
                f"⚠️ {symbol} TP1 已成交，但移动止损到成本价失败。请手动检查仓位和止损。错误：{format_exception(e)}",
            )

    def get_watch_status(self) -> Dict[str, Any]:
        """获取监控状态"""
        watches = self.config.load_watches()
        return {
            "total_watches": len(watches),
            "active_watches": [
                {
                    "symbol": symbol,
                    "running": self.ws_client is not None,
                    "phase": watch.get("phase", 1),
                }
                for symbol, watch in watches.items()
            ],
            "inactive_watches": [],
            "websocket_running": self.ws_client is not None,
        }

    def cleanup_finished_watches(self) -> int:
        """清理已完成的监控任务"""
        return 0
