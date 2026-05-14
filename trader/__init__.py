"""
交易模块
提供交易执行功能
"""
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Literal, Optional, List

from trader.client import create_client, BinanceClient
from trader.orders import OrderManager, OrderError
from trader.risk import get_risk_manager, RiskError
from utils.retry import RetryError, format_exception
from utils.logger import get_trader_logger

logger = get_trader_logger()

Side = Literal["long", "short"]


class TradeError(Exception):
    """交易错误"""
    pass


class TradeExecutor:
    """交易执行器"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        """
        初始化交易执行器
        
        Args:
            api_key: API密钥
            api_secret: API密钥
            testnet: 是否使用测试网络
        """
        self.client = create_client(api_key, api_secret, testnet)
        self.order_manager = OrderManager(self.client)
        self.risk_manager = get_risk_manager()
        
        logger.info(f"交易执行器已初始化 (testnet={testnet})")
    
    def execute_trade(
        self,
        token: str,
        side: Side,
        leverage: int,
        margin_usdt: float,
        tp_pct: float,
        sl_pct: float,
        margin_type: str = "ISOLATED",
        split_tp: bool = False,
        tp1_pct: float = 0.0,
        tp2_pct: float = 0.0,
        account_balance: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        执行交易
        
        Args:
            token: 交易对（如BTC）
            side: 交易方向（long/short）
            leverage: 杠杆倍数
            margin_usdt: 保证金金额（USDT）
            tp_pct: 止盈百分比
            sl_pct: 止损百分比
            margin_type: 保证金类型（ISOLATED/CROSSED）
            split_tp: 是否使用分批止盈
            tp1_pct: 第一批止盈百分比
            tp2_pct: 第二批止盈百分比
            account_balance: 账户余额（用于风险评估）
            
        Returns:
            交易结果字典
            
        Raises:
            TradeError: 交易执行失败
        """
        order_side = "BUY" if side == "long" else "SELL"
        order_submitted = False

        try:
            # 验证交易参数
            self._validate_trade_params(
                token, side, leverage, margin_usdt, tp_pct, sl_pct,
                split_tp, tp1_pct, tp2_pct, account_balance
            )
            
            # 解析交易对
            symbol_info = self.order_manager.resolve_symbol(token)
            symbol = symbol_info["symbol"]
            
            # 设置交易参数
            self.order_manager.setup_trading(symbol, leverage, margin_type)
            
            # 获取标记价格
            mark_price = Decimal(self.client.mark_price(symbol=symbol)["markPrice"])
            
            # 计算订单数量
            qty = self.order_manager.calculate_order_quantity(
                symbol_info, margin_usdt, leverage, mark_price
            )
            
            # 执行市价单
            order = self.client.new_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=format(qty, "f"),
                newOrderRespType="RESULT",
            )
            order_submitted = True

            # 获取平均成交价
            entry = self.order_manager.get_avg_fill_price(symbol, order)
            
            # 获取价格步长
            from trader.client import _filter_value
            tick = _filter_value(symbol_info, "PRICE_FILTER", "tickSize")
            
            # 计算价格水平
            close_side = "SELL" if order_side == "BUY" else "BUY"
            tp_price, sl_price, tp1_price, tp2_price = (
                self.order_manager.calculate_price_levels(
                    entry, order_side, tp_pct, sl_pct, tick, tp1_pct, tp2_pct
                )
            )
            
            # 放置止损单
            sl_id = self.order_manager.place_stop_loss_market(
                symbol, close_side, sl_price, tick
            )
            
            result = {
                "symbol": symbol,
                "side": side,
                "qty": str(qty),
                "entry": str(entry),
                "sl_price": str(sl_price),
                "leverage": leverage,
                "margin": margin_usdt,
                "split_tp": split_tp,
                "sl_id": sl_id,
            }
            
            if split_tp:
                # 分批止盈逻辑
                step = _filter_value(symbol_info, "LOT_SIZE", "stepSize")
                half_qty = (qty / Decimal(2)).quantize(step, rounding=ROUND_DOWN)
                
                if half_qty <= 0:
                    raise TradeError("数量太小，无法分批止盈")
                
                # 放置第一批止盈单
                tp1_order = self.client.new_order(
                    symbol=symbol,
                    side=close_side,
                    type="LIMIT",
                    price=format(tp1_price, "f"),
                    quantity=format(half_qty, "f"),
                    timeInForce="GTC",
                    reduceOnly="true",
                    recvWindow=10000,
                )
                
                # 放置第二批止盈单
                tp2_order = self.client.new_order(
                    symbol=symbol,
                    side=close_side,
                    type="LIMIT",
                    price=format(tp2_price, "f"),
                    quantity=format(half_qty, "f"),
                    timeInForce="GTC",
                    reduceOnly="true",
                    recvWindow=10000,
                )
                
                result.update({
                    "tp1_price": str(tp1_price),
                    "tp2_price": str(tp2_price),
                    "tp1_order_id": tp1_order["orderId"],
                    "tp2_order_id": tp2_order["orderId"],
                })
                
                logger.info(f"分批止盈交易完成: {symbol} {side} {qty}")
            else:
                # 单一止盈逻辑
                tp_id = self.order_manager.place_take_profit_market(
                    symbol, close_side, tp_price, tick
                )
                
                result.update({
                    "tp_price": str(tp_price),
                    "tp_id": tp_id,
                })
                
                logger.info(f"单一止盈交易完成: {symbol} {side} {qty}")
            
            # 更新交易统计
            position_size = self.risk_manager.calculate_position_size(
                margin_usdt, leverage, entry, qty
            )
            self.risk_manager.update_trade_stats(
                pnl=Decimal("0"),  # 开仓时盈亏为0
                is_win=False,      # 开仓不算胜率
                position_opened=True,
                position_closed=False
            )
            
            return result
            
        except (OrderError, RiskError, RetryError) as e:
            # 已知错误类型
            raise TradeError(format_exception(e)) from e
            
        except Exception as e:
            logger.error(f"交易执行异常: {format_exception(e)}", exc_info=True)
            symbol = symbol_info["symbol"] if 'symbol_info' in locals() else token.upper()
            if not symbol.endswith("USDT"):
                symbol = f"{symbol}USDT"

            if not order_submitted:
                raise TradeError(f"开仓失败，订单未成交：{format_exception(e)}") from e

            try:
                closed = self.order_manager.emergency_close_position(symbol, order_side)
                if closed:
                    raise TradeError(f"交易失败，仓位已紧急平仓：{format_exception(e)}")
                raise TradeError(
                    f"🚨 交易失败且紧急平仓失败！请手动检查 {symbol} 仓位。错误：{format_exception(e)}"
                )
            except TradeError:
                raise
            except Exception as close_error:
                raise TradeError(
                    f"🚨 交易完全失败！请立即检查仓位。原始错误：{format_exception(e)}；"
                    f"平仓错误：{format_exception(close_error)}"
                )
    
    def _validate_trade_params(
        self,
        token: str,
        side: str,
        leverage: int,
        margin_usdt: float,
        tp_pct: float,
        sl_pct: float,
        split_tp: bool,
        tp1_pct: float,
        tp2_pct: float,
        account_balance: Optional[float]
    ) -> None:
        """验证交易参数"""
        # 基本参数验证
        if not token or len(token.strip()) == 0:
            raise TradeError("交易对不能为空")
        
        # 风险管理验证
        is_valid, errors = self.risk_manager.validate_trade_params(
            leverage, margin_usdt, tp_pct, sl_pct, side, account_balance
        )
        
        if not is_valid:
            raise TradeError(f"交易参数验证失败: {'; '.join(errors)}")
        
        # 分批止盈验证
        if split_tp:
            is_valid, errors = self.risk_manager.validate_split_tp_params(
                tp1_pct, tp2_pct, sl_pct
            )
            
            if not is_valid:
                raise TradeError(f"分批止盈参数验证失败: {'; '.join(errors)}")
    
    def close_all_positions(self) -> Dict[str, Any]:
        """
        平掉所有持仓
        
        Returns:
            平仓结果
        """
        try:
            positions = self.client.get_position_risk()
            closed = []
            failed = []
            
            for p in positions:
                amt = Decimal(p.get("positionAmt", "0"))
                if amt == 0:
                    continue
                
                symbol = p["symbol"]
                close_side = "SELL" if amt > 0 else "BUY"
                
                try:
                    self.client.new_order(
                        symbol=symbol,
                        side=close_side,
                        type="MARKET",
                        quantity=format(abs(amt), "f"),
                        reduceOnly="true",
                        recvWindow=10000,
                    )
                    
                    # 取消所有挂单
                    try:
                        self.client.sign_request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
                    except Exception:
                        pass
                    
                    closed.append(f"{symbol} ({abs(amt)})")
                    
                    # 更新交易统计
                    self.risk_manager.update_trade_stats(
                        pnl=Decimal("0"),  # 平仓盈亏需要实际计算
                        is_win=False,
                        position_opened=False,
                        position_closed=True
                    )
                    
                except Exception as e:
                    failed.append(f"{symbol}: {e}")
            
            result = {
                "success": len(failed) == 0,
                "closed": closed,
                "failed": failed,
                "total_closed": len(closed),
                "total_failed": len(failed),
            }
            
            if failed:
                logger.warning(f"部分平仓失败: {failed}")
            
            return result
            
        except Exception as e:
            raise TradeError(f"平仓所有持仓失败: {e}")
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        获取所有持仓
        
        Returns:
            持仓列表
        """
        try:
            positions = self.client.get_position_risk()
            active_positions = []
            
            for p in positions:
                amt = Decimal(p.get("positionAmt", "0"))
                if amt != 0:
                    active_positions.append({
                        "symbol": p["symbol"],
                        "positionAmt": str(amt),
                        "entryPrice": p.get("entryPrice", "0"),
                        "markPrice": p.get("markPrice", "0"),
                        "unRealizedProfit": p.get("unRealizedProfit", "0"),
                        "leverage": p.get("leverage", "0"),
                        "marginType": p.get("marginType", ""),
                    })
            
            return active_positions
            
        except Exception as e:
            raise TradeError(f"获取持仓失败: {e}")


# 兼容性函数
def execute_trade(
    client: BinanceClient,
    token: str,
    side: Side,
    leverage: int,
    margin_usdt: float,
    tp_pct: float,
    sl_pct: float,
    margin_type: str = "ISOLATED",
    split_tp: bool = False,
    tp1_pct: float = 0.0,
    tp2_pct: float = 0.0,
) -> Dict[str, Any]:
    """
    兼容性函数，保持原有接口
    
    Args:
        client: Binance客户端
        token: 交易对
        side: 交易方向
        leverage: 杠杆倍数
        margin_usdt: 保证金金额
        tp_pct: 止盈百分比
        sl_pct: 止损百分比
        margin_type: 保证金类型
        split_tp: 是否分批止盈
        tp1_pct: 第一批止盈百分比
        tp2_pct: 第二批止盈百分比
        
    Returns:
        交易结果
    """
    executor = TradeExecutor(
        api_key=client.api_key,
        api_secret=client.api_secret,
        testnet=client.testnet
    )
    
    return executor.execute_trade(
        token=token,
        side=side,
        leverage=leverage,
        margin_usdt=margin_usdt,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        margin_type=margin_type,
        split_tp=split_tp,
        tp1_pct=tp1_pct,
        tp2_pct=tp2_pct
    )


# 导出所有必要的函数和类
__all__ = [
    "TradeError",
    "TradeExecutor",
    "execute_trade",
    "Side",
]