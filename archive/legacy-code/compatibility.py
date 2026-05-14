"""
兼容性模块
确保原有代码可以继续工作
"""
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Literal

from trader.client import create_client, BinanceClient, _filter_value, _round_step
from trader.orders import OrderManager, OrderError
from trader import TradeExecutor, TradeError

Side = Literal["long", "short"]


# 保持原有函数名和签名
def make_client(api_key: str, api_secret: str, testnet: bool) -> BinanceClient:
    """
    创建客户端（兼容原有代码）
    
    Args:
        api_key: API密钥
        api_secret: API密钥
        testnet: 是否使用测试网络
        
    Returns:
        Binance客户端
    """
    return create_client(api_key, api_secret, testnet)


def cancel_order(client: BinanceClient, symbol: str, order_id: int) -> None:
    """
    取消订单（兼容原有代码）
    
    Args:
        client: Binance客户端
        symbol: 交易对
        order_id: 订单ID
    """
    order_manager = OrderManager(client)
    order_manager.cancel_order_safe(symbol, order_id)


def cancel_algo_order(client: BinanceClient, symbol: str, algo_id: int) -> None:
    """
    取消算法订单（兼容原有代码）
    
    Args:
        client: Binance客户端
        symbol: 交易对
        algo_id: 算法订单ID
    """
    order_manager = OrderManager(client)
    order_manager.cancel_algo_order_safe(symbol, algo_id)


def place_sl_market(
    client: BinanceClient, 
    symbol: str, 
    close_side: str,
    sl_price: Decimal, 
    tick: Decimal
) -> int:
    """
    放置止损市价单（兼容原有代码）
    
    Args:
        client: Binance客户端
        symbol: 交易对
        close_side: 平仓方向
        sl_price: 止损价格
        tick: 价格步长
        
    Returns:
        算法订单ID
    """
    order_manager = OrderManager(client)
    return order_manager.place_stop_loss_market(symbol, close_side, sl_price, tick)


# 导出所有必要的函数和类
__all__ = [
    "TradeError",
    "make_client",
    "cancel_order",
    "cancel_algo_order",
    "place_sl_market",
    "execute_trade",  # 从trader模块导入
    "Side",
]