"""
Bot模块
主应用程序入口
"""
import os
import asyncio
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from bot.handlers import BotHandlers
from bot.watches import WatchManager
from utils.config import get_config, ConfigError
from utils.logger import setup_global_logging, get_bot_logger
from utils.retry import retry_telegram_async

# 加载环境变量
load_dotenv()

logger = get_bot_logger()


class BinanceTraderBot:
    """Binance交易机器人主应用程序"""
    
    def __init__(self):
        self.config = get_config()
        self.app: Optional[Application] = None
        self.watch_manager: Optional[WatchManager] = None
        self.handlers: Optional[BotHandlers] = None
        
        # Telegram网络错误计数
        self.telegram_network_errors = 0
        self.telegram_network_error_limit = 10
        
        logger.info("Binance交易机器人初始化完成")
    
    def _create_telegram_request(self, ca_bundle: Optional[str]) -> HTTPXRequest:
        """
        创建Telegram请求配置
        
        Args:
            ca_bundle: SSL证书文件路径
            
        Returns:
            HTTPXRequest实例
        """
        httpx_kwargs = {"trust_env": False}
        if ca_bundle:
            httpx_kwargs["verify"] = ca_bundle
        
        return HTTPXRequest(
            connection_pool_size=32,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=10,
            pool_timeout=10,
            httpx_kwargs=httpx_kwargs,
        )
    
    def _create_updates_request(self, ca_bundle: Optional[str]) -> HTTPXRequest:
        """
        创建更新请求配置
        
        Args:
            ca_bundle: SSL证书文件路径
            
        Returns:
            HTTPXRequest实例
        """
        httpx_kwargs = {"trust_env": False}
        if ca_bundle:
            httpx_kwargs["verify"] = ca_bundle
        
        return HTTPXRequest(
            connection_pool_size=2,
            connect_timeout=10,
            read_timeout=35,
            write_timeout=10,
            pool_timeout=10,
            httpx_kwargs=httpx_kwargs,
        )
    
    async def post_init(self, app: Application) -> None:
        """
        应用程序初始化后处理
        
        Args:
            app: Telegram应用程序
        """
        self.app = app
        self.watch_manager = WatchManager(app)
        self.handlers = BotHandlers(self.watch_manager)
        
        # 恢复监控任务
        self.watch_manager.restore_watches()
        
        # 设置命令处理器
        self.setup_handlers()
        
        logger.info("应用程序初始化完成")
    
    @retry_telegram_async
    async def on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """
        错误处理
        
        Args:
            update: 更新对象
            ctx: 上下文
        """
        error = ctx.error
        
        # 处理网络错误
        if isinstance(error, (NetworkError, TimedOut)):
            self.telegram_network_errors += 1
            logger.warning(
                f"Telegram网络错误 {self.telegram_network_errors}/{self.telegram_network_error_limit}: {error}"
            )
            
            if self.telegram_network_errors >= self.telegram_network_error_limit:
                logger.error("Telegram网络错误达到限制，退出以便launchd重启")
                os._exit(75)
            return
        
        # 重置网络错误计数
        self.telegram_network_errors = 0
        
        # 记录其他错误
        logger.exception("Telegram处理器错误", exc_info=error)
    
    def setup_handlers(self) -> None:
        """设置命令处理器"""
        if not self.handlers:
            raise RuntimeError("处理器未初始化")
        
        # 命令处理器
        self.app.add_handler(CommandHandler("start", self.handlers.cmd_start))
        self.app.add_handler(CommandHandler("panel", self.handlers.cmd_panel))
        self.app.add_handler(CommandHandler("config", self.handlers.cmd_panel))
        self.app.add_handler(CommandHandler("help", self.handlers.cmd_panel))
        
        # 回调查询处理器
        self.app.add_handler(CallbackQueryHandler(self.handlers.on_callback))
        
        # 文本消息处理器
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.on_text))
        
        # 错误处理器
        self.app.add_error_handler(self.on_error)
        
        logger.info("命令处理器设置完成")
    
    def run(self) -> None:
        """运行机器人"""
        try:
            # 创建Telegram应用程序
            bot_request = self._create_telegram_request(self.config.ssl_cert_file)
            updates_request = self._create_updates_request(self.config.ssl_cert_file)
            
            app = (
                Application.builder()
                .token(self.config.bot_token)
                .request(bot_request)
                .get_updates_request(updates_request)
                .post_init(self.post_init)
                .build()
            )
            
            self.app = app
            
            # 注意：setup_handlers() 现在在 post_init 回调中被调用
            # 因为 post_init 会初始化 handlers
            
            logger.info(f"机器人启动中 (testnet={self.config.testnet})")
            
            # 运行轮询
            app.run_polling(
                drop_pending_updates=True,
                bootstrap_retries=-1,
                allowed_updates=Update.ALL_TYPES,
            )
            
        except ConfigError as e:
            logger.error(f"配置错误: {e}")
            raise
        except Exception as e:
            logger.error(f"机器人启动失败: {e}")
            raise
    
    async def shutdown(self) -> None:
        """关闭机器人"""
        if self.watch_manager:
            self.watch_manager.stop_all_watches()
        
        if self.app:
            await self.app.shutdown()
        
        logger.info("机器人已关闭")


def main() -> None:
    """主函数"""
    try:
        # 设置日志
        setup_global_logging(log_level="INFO", log_file="bot.log")
        
        # 创建并运行机器人
        bot = BinanceTraderBot()
        bot.run()
        
    except ConfigError as e:
        print(f"配置错误: {e}")
        print("请检查.env文件和环境变量设置")
        exit(1)
    except KeyboardInterrupt:
        print("\n机器人已停止")
    except Exception as e:
        print(f"机器人运行失败: {e}")
        exit(1)


if __name__ == "__main__":
    main()