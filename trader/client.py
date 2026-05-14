"""
Binance客户端模块
提供安全的Binance API客户端
"""
import time
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Optional

from binance.um_futures import UMFutures
from binance.error import ClientError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.retry import retry_binance_api, RetryError
from utils.logger import get_trader_logger

logger = get_trader_logger()


class BinanceClientError(Exception):
    """Binance客户端错误"""
    pass


class BinanceClient:
    """安全的Binance客户端"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        """
        初始化Binance客户端
        
        Args:
            api_key: API密钥
            api_secret: API密钥
            testnet: 是否使用测试网络
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        # 验证API密钥
        self._validate_api_keys()
        
        # 创建客户端
        self._client = self._create_client()
        
        # 连接池统计
        self._connection_stats = {
            "total_requests": 0,
            "failed_requests": 0,
            "last_error": None
        }
    
    def _validate_api_keys(self) -> None:
        """验证API密钥"""
        if not self.api_key or not isinstance(self.api_key, str):
            raise BinanceClientError("API密钥不能为空")
        
        if not self.api_secret or not isinstance(self.api_secret, str):
            raise BinanceClientError("API密钥不能为空")
        
        if len(self.api_key) < 10 or len(self.api_secret) < 10:
            raise BinanceClientError("API密钥格式无效")
    
    def _create_client(self) -> UMFutures:
        """创建Binance客户端"""
        base_url = "https://testnet.binancefuture.com" if self.testnet else "https://fapi.binance.com"
        
        try:
            client = UMFutures(
                key=self.api_key,
                secret=self.api_secret,
                base_url=base_url
            )
            
            # 配置重试策略
            retry = Retry(
                total=4,
                backoff_factor=0.6,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET", "POST", "DELETE", "PUT"]),
                raise_on_status=False,
            )
            
            # 配置连接池
            adapter = HTTPAdapter(
                max_retries=retry,
                pool_connections=10,
                pool_maxsize=10,
                pool_block=False
            )
            
            client.session.mount("https://", adapter)
            client.session.mount("http://", adapter)
            
            # 设置超时
            client.session.timeout = (10, 30)  # 连接超时10秒，读取超时30秒
            
            logger.info(f"Binance客户端已创建 (testnet={self.testnet})")
            return client
            
        except Exception as e:
            raise BinanceClientError(f"创建Binance客户端失败: {e}")
    
    @property
    def client(self) -> UMFutures:
        """获取底层客户端"""
        return self._client
    
    @retry_binance_api
    def sign_request(self, method: str, path: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        签名请求
        
        Args:
            method: HTTP方法
            path: 请求路径
            params: 请求参数
            
        Returns:
            响应数据
        """
        self._connection_stats["total_requests"] += 1
        
        try:
            return self._client.sign_request(method, path, params or {})
        except ClientError as e:
            self._connection_stats["failed_requests"] += 1
            self._connection_stats["last_error"] = str(e)
            logger.error(f"Binance API错误: {e.error_code} - {e.error_message}")
            raise
        except Exception as e:
            self._connection_stats["failed_requests"] += 1
            self._connection_stats["last_error"] = str(e)
            logger.error(f"Binance请求异常: {e}")
            raise
    
    @retry_binance_api
    def get_position_risk(self, symbol: Optional[str] = None, recvWindow: int = 10000) -> list:
        """获取仓位风险"""
        params = {"recvWindow": recvWindow}
        if symbol:
            params["symbol"] = symbol
        
        return self._client.get_position_risk(**params)
    
    @retry_binance_api
    def exchange_info(self) -> Dict[str, Any]:
        """获取交易对信息"""
        return self._client.exchange_info()
    
    @retry_binance_api
    def mark_price(self, symbol: str) -> Dict[str, Any]:
        """获取标记价格"""
        return self._client.mark_price(symbol=symbol)
    
    @retry_binance_api
    def new_order(self, **kwargs) -> Dict[str, Any]:
        """创建新订单"""
        # 确保有recvWindow
        if "recvWindow" not in kwargs:
            kwargs["recvWindow"] = 10000
        
        return self._client.new_order(**kwargs)
    
    @retry_binance_api
    def query_order(self, symbol: str, orderId: int) -> Dict[str, Any]:
        """查询订单"""
        return self._client.query_order(symbol=symbol, orderId=orderId)

    @retry_binance_api
    def query_algo_order(self, symbol: str, algoId: int) -> Dict[str, Any]:
        """查询条件单"""
        return self.sign_request("GET", "/fapi/v1/algoOrder", {
            "symbol": symbol,
            "algoId": algoId,
            "recvWindow": 10000,
        })

    @retry_binance_api
    def cancel_order(self, symbol: str, orderId: int) -> Dict[str, Any]:
        """取消订单"""
        return self._client.cancel_order(symbol=symbol, orderId=orderId)
    
    @retry_binance_api
    def change_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """修改杠杆"""
        return self._client.change_leverage(symbol=symbol, leverage=leverage)
    
    def change_margin_type(self, symbol: str, marginType: str) -> Dict[str, Any]:
        """修改保证金类型"""
        try:
            return self._client.change_margin_type(symbol=symbol, marginType=marginType)
        except ClientError as e:
            if e.error_code == -4046:
                logger.info(f"{symbol} 保证金类型已是 {marginType}，跳过修改")
                return {"code": e.error_code, "msg": e.error_message}
            raise

    def new_listen_key(self) -> Dict[str, Any]:
        """创建用户数据流 listenKey"""
        return self._client.new_listen_key()

    def renew_listen_key(self, listen_key: str) -> Dict[str, Any]:
        """续期用户数据流 listenKey"""
        return self._client.renew_listen_key(listen_key)

    def close_listen_key(self, listen_key: str) -> Dict[str, Any]:
        """关闭用户数据流 listenKey"""
        return self._client.close_listen_key(listen_key)

    def get_connection_stats(self) -> Dict[str, Any]:
        """获取连接统计信息"""
        return {
            **self._connection_stats,
            "success_rate": (
                (self._connection_stats["total_requests"] - self._connection_stats["failed_requests"]) 
                / max(self._connection_stats["total_requests"], 1)
            ) * 100,
            "testnet": self.testnet
        }
    
    def health_check(self) -> bool:
        """健康检查"""
        try:
            # 简单的ping测试
            self._client.time()
            return True
        except Exception as e:
            logger.warning(f"Binance健康检查失败: {e}")
            return False


# 工具函数
def _filter_value(symbol_info: Dict[str, Any], filter_type: str, key: str) -> Decimal:
    """从交易对信息中获取过滤值"""
    for f in symbol_info["filters"]:
        if f["filterType"] == filter_type:
            return Decimal(f[key])
    raise KeyError(f"{filter_type}.{key} not found")


def _round_step(value: Decimal, step: Decimal) -> Decimal:
    """按步长舍入"""
    return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def create_client(api_key: str, api_secret: str, testnet: bool = True) -> BinanceClient:
    """
    创建Binance客户端
    
    Args:
        api_key: API密钥
        api_secret: API密钥
        testnet: 是否使用测试网络
        
    Returns:
        BinanceClient实例
    """
    return BinanceClient(api_key, api_secret, testnet)


# 工具函数（保持向后兼容）
def _filter_value(symbol_info: Dict[str, Any], filter_type: str, key: str) -> Decimal:
    """从交易对信息中获取过滤值"""
    for f in symbol_info["filters"]:
        if f["filterType"] == filter_type:
            return Decimal(f[key])
    raise KeyError(f"{filter_type}.{key} not found")


def _round_step(value: Decimal, step: Decimal) -> Decimal:
    """按步长舍入"""
    return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def _resolve_symbol(client: BinanceClient, token: str) -> Dict[str, Any]:
    """
    解析交易对
    
    Args:
        client: Binance客户端
        token: 交易对（如BTC）
        
    Returns:
        交易对信息
    """
    candidate = token.upper().strip()
    if not candidate.endswith("USDT"):
        candidate = f"{candidate}USDT"
    
    info = client.exchange_info()
    
    for s in info["symbols"]:
        if (s["symbol"] == candidate and 
            s["status"] == "TRADING" and 
            s["contractType"] == "PERPETUAL"):
            return s
    
    raise OrderError(f"交易对未找到或不可交易: {candidate}")