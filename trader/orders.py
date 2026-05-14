"""
订单管理模块
处理订单创建、取消和管理
"""
import time
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from binance.error import ClientError

from trader.client import BinanceClient, _filter_value, _round_step
from utils.retry import retry_binance_api, RetryError
from utils.logger import get_trader_logger

logger = get_trader_logger()


class OrderError(Exception):
    """订单错误"""
    pass


class OrderManager:
    """订单管理器"""
    
    def __init__(self, client: BinanceClient):
        self.client = client
    
    @retry_binance_api
    def resolve_symbol(self, token: str) -> Dict[str, Any]:
        """
        解析交易对
        
        Args:
            token: 交易对（如BTC）
            
        Returns:
            交易对信息
        """
        candidate = token.upper().strip()
        if not candidate.endswith("USDT"):
            candidate = f"{candidate}USDT"
        
        info = self.client.exchange_info()
        
        for s in info["symbols"]:
            if (s["symbol"] == candidate and 
                s["status"] == "TRADING" and 
                s["contractType"] == "PERPETUAL"):
                return s
        
        raise OrderError(f"交易对未找到或不可交易: {candidate}")
    
    def setup_trading(self, symbol: str, leverage: int, margin_type: str = "ISOLATED") -> None:
        """
        设置交易参数
        
        Args:
            symbol: 交易对
            leverage: 杠杆倍数
            margin_type: 保证金类型
        """
        try:
            positions = self.client.get_position_risk(symbol=symbol)
            pos = positions[0] if positions else {}
            has_position = Decimal(pos.get("positionAmt", "0")) != 0
            current_lev = int(pos.get("leverage", "0") or 0)
            current_margin_type = (pos.get("marginType", "") or "").upper()
            
            # 如果有持仓，不修改设置
            if has_position:
                logger.info(f"{symbol} 已有持仓，跳过设置修改")
                return
            
            # 修改保证金类型
            if current_margin_type != margin_type.upper():
                try:
                    self.client.change_margin_type(symbol=symbol, marginType=margin_type)
                    logger.info(f"{symbol} 保证金类型已修改为: {margin_type}")
                except ClientError as e:
                    if e.error_code != -4046:  # 不是"无仓位时不能修改保证金类型"或"不需要修改保证金类型"错误
                        raise
                    # 错误代码-4046可能表示：
                    # 1. "No need to change margin type." (不需要修改保证金类型)
                    # 2. "无仓位时不能修改保证金类型" (无仓位时不能修改保证金类型)
                    logger.debug(f"{symbol} 保证金类型修改跳过: {e.error_message} ({e.error_code})")
            
            # 修改杠杆
            if current_lev != leverage:
                self.client.change_leverage(symbol=symbol, leverage=leverage)
                logger.info(f"{symbol} 杠杆已修改为: {leverage}x")
                
        except Exception as e:
            raise OrderError(f"设置交易参数失败: {e}")
    
    @retry_binance_api
    def get_avg_fill_price(self, symbol: str, order: Dict[str, Any]) -> Decimal:
        """
        获取平均成交价
        
        Args:
            symbol: 交易对
            order: 订单信息
            
        Returns:
            平均成交价
        """
        avg = order.get("avgPrice")
        if avg and Decimal(avg) > 0:
            return Decimal(avg)
        
        # 轮询获取成交价
        for attempt in range(5):
            time.sleep(0.3)
            try:
                query = self.client.query_order(symbol=symbol, orderId=order["orderId"])
                if query.get("avgPrice") and Decimal(query["avgPrice"]) > 0:
                    return Decimal(query["avgPrice"])
            except Exception as e:
                logger.warning(f"查询订单成交价失败 (尝试{attempt+1}/5): {e}")
        
        # 如果还是获取不到，使用标记价格
        try:
            mark_price = Decimal(self.client.mark_price(symbol=symbol)["markPrice"])
            logger.warning(f"使用标记价格作为成交价: {mark_price}")
            return mark_price
        except Exception as e:
            raise OrderError(f"无法获取成交价: {e}")
    
    @retry_binance_api
    def cancel_order_safe(self, symbol: str, order_id: int) -> bool:
        """
        安全取消订单
        
        Args:
            symbol: 交易对
            order_id: 订单ID
            
        Returns:
            是否成功取消
        """
        try:
            self.client.cancel_order(symbol=symbol, orderId=order_id)
            logger.info(f"订单已取消: {symbol} #{order_id}")
            return True
        except ClientError as e:
            # 忽略已成交或已取消的订单错误
            if e.error_code in (-2011, -2013):  # 订单不存在或已取消
                logger.debug(f"订单已不存在: {symbol} #{order_id}")
                return True
            logger.error(f"取消订单失败: {e}")
            return False
        except Exception as e:
            logger.error(f"取消订单异常: {e}")
            return False
    
    @retry_binance_api
    def cancel_algo_order_safe(self, symbol: str, algo_id: int) -> bool:
        """
        安全取消算法订单
        
        Args:
            symbol: 交易对
            algo_id: 算法订单ID
            
        Returns:
            是否成功取消
        """
        try:
            self.client.sign_request("DELETE", "/fapi/v1/algoOrder", {
                "algoId": algo_id,
                "recvWindow": 10000,
            })
            logger.info(f"算法订单已取消: {symbol} #{algo_id}")
            return True
        except ClientError as e:
            # 忽略已成交或已取消的订单错误
            if e.error_code in (-2011, -2013):  # 订单不存在或已取消
                logger.debug(f"算法订单已不存在: {symbol} #{algo_id}")
                return True
            logger.error(f"取消算法订单失败: {e}")
            return False
        except Exception as e:
            logger.error(f"取消算法订单异常: {e}")
            return False
    
    @retry_binance_api
    def place_stop_loss_market(
        self, 
        symbol: str, 
        close_side: str,
        sl_price: Decimal, 
        tick: Decimal
    ) -> int:
        """
        放置止损市价单
        
        Args:
            symbol: 交易对
            close_side: 平仓方向
            sl_price: 止损价格
            tick: 价格步长
            
        Returns:
            算法订单ID
        """
        sl_price = _round_step(sl_price, tick)
        
        resp = self.client.sign_request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "triggerPrice": format(sl_price, "f"),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "recvWindow": 10000,
        })
        
        algo_id = resp.get("algoId") or resp.get("orderId")
        if not algo_id:
            raise OrderError("创建止损单失败: 未返回订单ID")
        
        logger.info(f"止损单已创建: {symbol} @ {sl_price} (ID: {algo_id})")
        return algo_id
    
    @retry_binance_api
    def place_take_profit_market(
        self,
        symbol: str,
        close_side: str,
        tp_price: Decimal,
        tick: Decimal
    ) -> int:
        """
        放置止盈市价单
        
        Args:
            symbol: 交易对
            close_side: 平仓方向
            tp_price: 止盈价格
            tick: 价格步长
            
        Returns:
            算法订单ID
        """
        tp_price = _round_step(tp_price, tick)
        
        resp = self.client.sign_request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": close_side,
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": format(tp_price, "f"),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "recvWindow": 10000,
        })
        
        algo_id = resp.get("algoId") or resp.get("orderId")
        if not algo_id:
            raise OrderError("创建止盈单失败: 未返回订单ID")
        
        logger.info(f"止盈单已创建: {symbol} @ {tp_price} (ID: {algo_id})")
        return algo_id
    
    @retry_binance_api
    def emergency_close_position(self, symbol: str, side: str) -> bool:
        """
        紧急平仓
        
        Args:
            symbol: 交易对
            side: 开仓方向（BUY/SELL）
            
        Returns:
            是否成功平仓
        """
        close_side = "SELL" if side == "BUY" else "BUY"
        
        for attempt in range(3):
            try:
                positions = self.client.get_position_risk(symbol=symbol)
                amt = Decimal(positions[0]["positionAmt"])
                
                if amt == 0:
                    logger.info(f"{symbol} 仓位已为0")
                    return True
                
                self.client.new_order(
                    symbol=symbol,
                    side=close_side,
                    type="MARKET",
                    quantity=format(abs(amt), "f"),
                    reduceOnly="true",
                    recvWindow=10000,
                )
                
                logger.info(f"紧急平仓成功: {symbol} {abs(amt)}")
                return True
                
            except Exception as e:
                logger.warning(f"紧急平仓失败 (尝试{attempt+1}/3): {e}")
                time.sleep(0.5 * (attempt + 1))
        
        logger.error(f"紧急平仓完全失败: {symbol}")
        return False
    
    def calculate_order_quantity(
        self,
        symbol_info: Dict[str, Any],
        margin_usdt: float,
        leverage: int,
        mark_price: Decimal
    ) -> Decimal:
        """
        计算订单数量
        
        Args:
            symbol_info: 交易对信息
            margin_usdt: 保证金金额
            leverage: 杠杆倍数
            mark_price: 标记价格
            
        Returns:
            订单数量
        """
        step = _filter_value(symbol_info, "LOT_SIZE", "stepSize")
        notional = Decimal(str(margin_usdt)) * Decimal(leverage)
        qty = _round_step(notional / mark_price, step)
        
        if qty <= 0:
            raise OrderError(
                f"计算数量为0 - 保证金 {margin_usdt} USDT 在价格 {mark_price} 下太小"
            )
        
        lot_step = None
        market_max_qty = None
        lot_max_qty = None
        for f in symbol_info["filters"]:
            if f["filterType"] == "MARKET_LOT_SIZE":
                market_max_qty = Decimal(f["maxQty"])
            elif f["filterType"] == "LOT_SIZE":
                lot_step = Decimal(f["stepSize"])
                lot_max_qty = Decimal(f["maxQty"])

        # 验证最小数量
        min_qty = _filter_value(symbol_info, "LOT_SIZE", "minQty")
        if qty < min_qty:
            raise OrderError(
                f"数量 {qty} 小于最小数量 {min_qty}"
            )

        max_qty = market_max_qty or lot_max_qty
        if max_qty and qty > max_qty:
            max_notional = max_qty * mark_price
            max_margin = (max_notional / Decimal(leverage)).quantize(Decimal("0.01"))
            raise OrderError(
                f"数量 {qty} 超过 {symbol_info['symbol']} 单笔最大数量 {max_qty}；"
                f"当前保证金 {margin_usdt}U、杠杆 {leverage}x，建议保证金不超过 {max_margin}U 或降低杠杆"
            )

        min_notional = Decimal("50")
        for f in symbol_info["filters"]:
            if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = Decimal(f.get("notional") or f.get("minNotional") or min_notional)
                break

        actual_notional = qty * mark_price
        if actual_notional < min_notional:
            required_margin = (min_notional / Decimal(leverage)).quantize(Decimal("0.01"))
            raise OrderError(
                f"订单名义价值 {actual_notional:.2f} USDT 小于币安最小要求 {min_notional} USDT；"
                f"当前保证金 {margin_usdt}U、杠杆 {leverage}x，建议保证金至少 {required_margin}U 或提高杠杆"
            )

        return qty
    
    def calculate_price_levels(
        self,
        entry_price: Decimal,
        side: str,
        tp_pct: float,
        sl_pct: float,
        tick: Decimal,
        tp1_pct: Optional[float] = None,
        tp2_pct: Optional[float] = None
    ) -> Tuple[Decimal, Decimal, Optional[Decimal], Optional[Decimal]]:
        """
        计算价格水平
        
        Args:
            entry_price: 入场价格
            side: 交易方向
            tp_pct: 止盈百分比
            sl_pct: 止损百分比
            tick: 价格步长
            tp1_pct: 第一批止盈百分比
            tp2_pct: 第二批止盈百分比
            
        Returns:
            (止盈价, 止损价, 第一批止盈价, 第二批止盈价)
        """
        direction = Decimal(1) if side == "BUY" else Decimal(-1)
        
        # 计算止损价
        sl_price = _round_step(
            entry_price * (Decimal(1) - direction * Decimal(str(sl_pct)) / Decimal(100)),
            tick
        )
        
        # 计算止盈价
        tp_price = _round_step(
            entry_price * (Decimal(1) + direction * Decimal(str(tp_pct)) / Decimal(100)),
            tick
        )
        
        # 计算分批止盈价
        tp1_price = None
        tp2_price = None
        
        if tp1_pct and tp2_pct:
            tp1_price = _round_step(
                entry_price * (Decimal(1) + direction * Decimal(str(tp1_pct)) / Decimal(100)),
                tick
            )
            tp2_price = _round_step(
                entry_price * (Decimal(1) + direction * Decimal(str(tp2_pct)) / Decimal(100)),
                tick
            )
        
        return tp_price, sl_price, tp1_price, tp2_price