"""
重试机制模块
提供统一的错误重试逻辑
"""
import asyncio
import time
from typing import Any, Callable, Type, Tuple, List, Optional
from functools import wraps
import logging

# 延迟导入，避免在没有安装依赖时失败
try:
    from binance.error import ClientError
    BINANCE_AVAILABLE = True
except ImportError:
    BINANCE_AVAILABLE = False
    # 创建虚拟的ClientError类
    class ClientError(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.error_code = kwargs.get('error_code', None)
            self.error_message = kwargs.get('error_message', '')

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class RetryError(Exception):
    """重试失败错误"""
    pass


def format_exception(e: Exception) -> str:
    """格式化异常，保留底层错误码和消息。"""
    if BINANCE_AVAILABLE and isinstance(e, ClientError):
        code = getattr(e, "error_code", None)
        message = getattr(e, "error_message", "")
        return f"Binance API错误 {code}: {message}"

    cause = getattr(e, "__cause__", None)
    if cause:
        return f"{type(e).__name__}: {e}；原始错误：{format_exception(cause)}"

    text = str(e)
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


class RetryConfig:
    """重试配置"""
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        exponential_backoff: bool = True,
        retry_on_exceptions: Optional[List[Type[Exception]]] = None
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_backoff = exponential_backoff
        
        # 默认重试的异常类型
        if retry_on_exceptions is None:
            self.retry_on_exceptions = [
                TimeoutError,
                ConnectionError,
            ]
            # 如果requests可用，添加requests异常
            if REQUESTS_AVAILABLE:
                self.retry_on_exceptions.extend([
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.SSLError,
                ])
        else:
            self.retry_on_exceptions = retry_on_exceptions
    
    def get_delay(self, attempt: int) -> float:
        """计算重试延迟时间"""
        if not self.exponential_backoff:
            return self.base_delay
        
        delay = self.base_delay * (2 ** (attempt - 1))
        return min(delay, self.max_delay)


def retry_sync(config: RetryConfig = RetryConfig()):
    """同步函数重试装饰器"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(1, config.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # 检查是否应该重试
                    should_retry = False
                    for exc_type in config.retry_on_exceptions:
                        if isinstance(e, exc_type):
                            should_retry = True
                            break
                    
                    # 如果是Binance客户端错误，检查错误码
                    if BINANCE_AVAILABLE and isinstance(e, ClientError):
                        error_code = getattr(e, 'error_code', None)
                        # 重试特定的错误码
                        retry_codes = [-1021, -1003, -1001]  # 时间戳错误、服务繁忙等
                        if error_code in retry_codes:
                            should_retry = True
                    
                    if not should_retry:
                        raise
                    if attempt == config.max_retries:
                        break

                    # 计算延迟并等待
                    delay = config.get_delay(attempt)
                    logging.debug(
                        f"重试 {func.__name__}: 第{attempt}次尝试失败，{delay}秒后重试。错误: {format_exception(e)}"
                    )
                    time.sleep(delay)

            # 所有重试都失败
            if last_exception:
                raise RetryError(
                    f"{func.__name__} 连续重试 {config.max_retries} 次失败：{format_exception(last_exception)}"
                ) from last_exception
            else:
                raise RetryError(f"{func.__name__} 执行失败")
        
        return wrapper
    return decorator


def retry_async(config: RetryConfig = RetryConfig()):
    """异步函数重试装饰器"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(1, config.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # 检查是否应该重试
                    should_retry = False
                    for exc_type in config.retry_on_exceptions:
                        if isinstance(e, exc_type):
                            should_retry = True
                            break
                    
                    # 如果是Binance客户端错误，检查错误码
                    if BINANCE_AVAILABLE and isinstance(e, ClientError):
                        error_code = getattr(e, 'error_code', None)
                        # 重试特定的错误码
                        retry_codes = [-1021, -1003, -1001]  # 时间戳错误、服务繁忙等
                        if error_code in retry_codes:
                            should_retry = True
                    
                    if not should_retry:
                        raise
                    if attempt == config.max_retries:
                        break

                    # 计算延迟并等待
                    delay = config.get_delay(attempt)
                    logging.debug(
                        f"重试 {func.__name__}: 第{attempt}次尝试失败，{delay}秒后重试。错误: {format_exception(e)}"
                    )
                    await asyncio.sleep(delay)

            # 所有重试都失败
            if last_exception:
                raise RetryError(
                    f"{func.__name__} 连续重试 {config.max_retries} 次失败：{format_exception(last_exception)}"
                ) from last_exception
            else:
                raise RetryError(f"{func.__name__} 执行失败")
        
        return wrapper
    return decorator


# 预定义的配置
NETWORK_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=0.5,
    max_delay=5.0,
    exponential_backoff=True,
    retry_on_exceptions=None  # 使用默认配置
)

BINANCE_API_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=0.5,
    max_delay=5.0,
    exponential_backoff=True,
    retry_on_exceptions=None  # 使用默认配置
)

TELEGRAM_RETRY_CONFIG = RetryConfig(
    max_retries=2,
    base_delay=1.0,
    max_delay=5.0,
    exponential_backoff=True,
    retry_on_exceptions=None  # 使用默认配置
)


def retry_network(func: Callable) -> Callable:
    """网络操作重试装饰器"""
    return retry_sync(NETWORK_RETRY_CONFIG)(func)


def retry_network_async(func: Callable) -> Callable:
    """异步网络操作重试装饰器"""
    return retry_async(NETWORK_RETRY_CONFIG)(func)


def retry_binance_api(func: Callable) -> Callable:
    """Binance API操作重试装饰器"""
    return retry_sync(BINANCE_API_RETRY_CONFIG)(func)


def retry_binance_api_async(func: Callable) -> Callable:
    """异步Binance API操作重试装饰器"""
    return retry_async(BINANCE_API_RETRY_CONFIG)(func)


def retry_telegram(func: Callable) -> Callable:
    """Telegram操作重试装饰器"""
    return retry_sync(TELEGRAM_RETRY_CONFIG)(func)


def retry_telegram_async(func: Callable) -> Callable:
    """异步Telegram操作重试装饰器"""
    return retry_async(TELEGRAM_RETRY_CONFIG)(func)