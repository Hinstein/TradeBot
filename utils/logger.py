"""
日志管理模块
提供结构化的日志记录
"""
import logging
import sys
from pathlib import Path
from typing import Optional


class LoggerConfig:
    """日志配置类"""
    
    def __init__(self, log_level: str = "INFO", log_file: Optional[str] = None):
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.log_file = log_file
        
        # 配置日志格式
        self.formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    def setup_logging(self) -> None:
        """设置日志配置"""
        # 获取根日志记录器
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)
        
        # 清除现有处理器
        root_logger.handlers.clear()
        
        if self.log_file:
            log_path = Path(self.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setFormatter(self.formatter)
            root_logger.addHandler(file_handler)
        else:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(self.formatter)
            root_logger.addHandler(console_handler)
        
        # 设置第三方库的日志级别
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("binance").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的日志记录器"""
    return logging.getLogger(name)


# 全局日志配置
_logger_config: Optional[LoggerConfig] = None


def setup_global_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    """设置全局日志配置"""
    global _logger_config
    _logger_config = LoggerConfig(log_level, log_file)
    _logger_config.setup_logging()


def get_bot_logger() -> logging.Logger:
    """获取Bot专用的日志记录器"""
    return get_logger("bot")


def get_trader_logger() -> logging.Logger:
    """获取Trader专用的日志记录器"""
    return get_logger("trader")


def get_watch_logger() -> logging.Logger:
    """获取监控任务专用的日志记录器"""
    return get_logger("watch")