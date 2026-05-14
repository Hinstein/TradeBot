"""
配置管理模块
处理环境变量验证、配置加载和验证
"""
import os
import json
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
from decimal import Decimal


class ConfigError(Exception):
    """配置相关错误"""
    pass


class Config:
    """配置管理类"""
    
    def __init__(self):
        self._validate_env_vars()
        self._load_settings()
        
    def _validate_env_vars(self) -> None:
        """验证必需的环境变量"""
        # 检查是否有测试环境配置
        testnet_mode = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        
        if testnet_mode:
            # 测试环境：检查测试环境API密钥
            testnet_key = os.getenv("BINANCE_TESTNET_API_KEY")
            testnet_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
            
            if not testnet_key or not testnet_secret:
                print("⚠️  警告: 测试环境API密钥未设置，将使用主网API密钥进行测试")
                # 回退到主网API密钥
                if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
                    raise ConfigError(
                        "缺少Binance API密钥。请设置BINANCE_API_KEY和BINANCE_API_SECRET环境变量"
                    )
        else:
            # 生产环境：检查主网API密钥
            if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
                raise ConfigError(
                    "生产环境缺少Binance API密钥。请设置BINANCE_API_KEY和BINANCE_API_SECRET环境变量"
                )
        
        # 检查Telegram Bot Token
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            raise ConfigError("缺少TELEGRAM_BOT_TOKEN环境变量")
        
        # 验证Telegram用户ID格式
        allowed_ids_str = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        if allowed_ids_str:
            try:
                ids = {int(x.strip()) for x in allowed_ids_str.split(",") if x.strip()}
                if not ids:
                    raise ConfigError("TELEGRAM_ALLOWED_USER_IDS不能为空")
            except ValueError:
                raise ConfigError("TELEGRAM_ALLOWED_USER_IDS格式错误，应为逗号分隔的整数")
    
    def _load_settings(self) -> None:
        """加载设置文件"""
        self.settings_file = Path(__file__).parent.parent / "settings.json"
        self.watches_file = Path(__file__).parent.parent / "watches.json"
        
        # 默认设置
        self.defaults = {
            "side": os.getenv("DEFAULT_SIDE", "long"),
            "leverage": int(os.getenv("DEFAULT_LEVERAGE", "5")),
            "margin": float(os.getenv("DEFAULT_MARGIN_USDT", "50")),
            "tp": float(os.getenv("DEFAULT_TP_PCT", "5")),
            "sl": float(os.getenv("DEFAULT_SL_PCT", "2")),
            "armed": False,
            "tp_mode": "single",
            "tp1_pct": 3.0,
            "tp2_pct": 5.0,
            "environment": "testnet",  # 默认环境：testnet 或 mainnet
        }
        
        # 验证默认值
        self._validate_defaults()
    
    def _validate_defaults(self) -> None:
        """验证默认值"""
        if self.defaults["side"] not in ["long", "short"]:
            raise ConfigError(f"无效的交易方向: {self.defaults['side']}")
        
        if not 1 <= self.defaults["leverage"] <= 125:
            raise ConfigError(f"杠杆必须在1-125之间: {self.defaults['leverage']}")
        
        if self.defaults["margin"] <= 0:
            raise ConfigError(f"保证金必须大于0: {self.defaults['margin']}")
        
        if self.defaults["tp"] <= 0:
            raise ConfigError(f"止盈百分比必须大于0: {self.defaults['tp']}")
        
        if self.defaults["sl"] <= 0:
            raise ConfigError(f"止损百分比必须大于0: {self.defaults['sl']}")
        
        if self.defaults["environment"] not in ["testnet", "mainnet"]:
            raise ConfigError(f"无效的环境设置: {self.defaults['environment']}")
    
    @property
    def bot_token(self) -> str:
        """获取Telegram Bot Token"""
        return os.environ["TELEGRAM_BOT_TOKEN"]
    
    @property
    def allowed_user_ids(self) -> Set[int]:
        """获取允许的用户ID"""
        ids_str = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        if not ids_str:
            return set()
        return {int(x.strip()) for x in ids_str.split(",") if x.strip()}
    
    @property
    def api_key(self) -> str:
        """获取当前环境的Binance API Key"""
        settings = self.load_settings()
        environment = settings.get("environment", "testnet")
        
        if environment == "testnet":
            # 优先使用测试环境专用API密钥
            testnet_key = os.getenv("BINANCE_TESTNET_API_KEY")
            if testnet_key:
                return testnet_key
            # 回退到主网API密钥
            return os.getenv("BINANCE_API_KEY", "")
        else:
            # 生产环境使用主网API密钥
            return os.getenv("BINANCE_API_KEY", "")
    
    @property
    def api_secret(self) -> str:
        """获取当前环境的Binance API Secret"""
        settings = self.load_settings()
        environment = settings.get("environment", "testnet")
        
        if environment == "testnet":
            # 优先使用测试环境专用API密钥
            testnet_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
            if testnet_secret:
                return testnet_secret
            # 回退到主网API密钥
            return os.getenv("BINANCE_API_SECRET", "")
        else:
            # 生产环境使用主网API密钥
            return os.getenv("BINANCE_API_SECRET", "")
    
    @property
    def testnet(self) -> bool:
        """是否使用测试网络（基于当前环境设置）"""
        settings = self.load_settings()
        environment = settings.get("environment", "testnet")
        return environment == "testnet"
    
    @property
    def environment(self) -> str:
        """获取当前环境"""
        settings = self.load_settings()
        return settings.get("environment", "testnet")
    
    @property
    def ssl_cert_file(self) -> Optional[str]:
        """获取SSL证书文件路径"""
        return os.getenv("SSL_CERT_FILE")
    
    def get_environment_info(self) -> Dict[str, Any]:
        """获取环境信息"""
        settings = self.load_settings()
        environment = settings.get("environment", "testnet")
        
        info = {
            "environment": environment,
            "is_testnet": environment == "testnet",
            "api_key_source": "",
            "has_testnet_keys": bool(os.getenv("BINANCE_TESTNET_API_KEY") and os.getenv("BINANCE_TESTNET_API_SECRET")),
            "has_mainnet_keys": bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET")),
        }
        
        if environment == "testnet":
            if os.getenv("BINANCE_TESTNET_API_KEY"):
                info["api_key_source"] = "测试环境专用密钥"
            else:
                info["api_key_source"] = "主网密钥（测试环境）"
        else:
            info["api_key_source"] = "主网密钥"
        
        return info
    
    def load_settings(self) -> Dict[str, Any]:
        """加载用户设置"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 合并默认值
                return {**self.defaults, **data}
            except json.JSONDecodeError as e:
                raise ConfigError(f"设置文件格式错误: {e}")
            except Exception as e:
                raise ConfigError(f"加载设置文件失败: {e}")
        return dict(self.defaults)
    
    def save_settings(self, settings: Dict[str, Any]) -> None:
        """保存用户设置"""
        try:
            # 验证设置
            self._validate_settings(settings)
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ConfigError(f"保存设置失败: {e}")
    
    def _validate_settings(self, settings: Dict[str, Any]) -> None:
        """验证设置值"""
        # 验证交易方向
        if "side" in settings and settings["side"] not in ["long", "short"]:
            raise ConfigError(f"无效的交易方向: {settings['side']}")
        
        # 验证杠杆
        if "leverage" in settings:
            leverage = settings["leverage"]
            if not isinstance(leverage, (int, float)):
                raise ConfigError(f"杠杆必须是数字: {leverage}")
            if not 1 <= leverage <= 125:
                raise ConfigError(f"杠杆必须在1-125之间: {leverage}")
        
        # 验证保证金
        if "margin" in settings:
            margin = settings["margin"]
            if not isinstance(margin, (int, float)):
                raise ConfigError(f"保证金必须是数字: {margin}")
            if margin <= 0:
                raise ConfigError(f"保证金必须大于0: {margin}")
        
        # 验证止盈止损
        for key in ["tp", "sl", "tp1_pct", "tp2_pct"]:
            if key in settings:
                value = settings[key]
                if not isinstance(value, (int, float)):
                    raise ConfigError(f"{key}必须是数字: {value}")
                if value <= 0:
                    raise ConfigError(f"{key}必须大于0: {value}")
        
        # 验证分批止盈
        if "tp_mode" in settings and settings["tp_mode"] == "split":
            if "tp1_pct" in settings and "tp2_pct" in settings:
                if settings["tp1_pct"] >= settings["tp2_pct"]:
                    raise ConfigError("TP1必须小于TP2")
        
        # 验证环境设置
        if "environment" in settings:
            environment = settings["environment"]
            if environment not in ["testnet", "mainnet"]:
                raise ConfigError(f"无效的环境设置: {environment}")
            
            # 检查环境切换的安全性
            if environment == "mainnet":
                # 切换到生产环境时，检查是否有主网API密钥
                if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
                    raise ConfigError("切换到生产环境需要设置BINANCE_API_KEY和BINANCE_API_SECRET环境变量")
                
                # 检查当前是否在测试环境有未平仓的测试仓位
                current_env = self.load_settings().get("environment", "testnet")
                if current_env == "testnet":
                    # 这里可以添加检查测试环境是否有未平仓的逻辑
                    pass
    
    def load_watches(self) -> Dict[str, Any]:
        """加载监控任务"""
        if self.watches_file.exists():
            try:
                with open(self.watches_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
            except Exception as e:
                raise ConfigError(f"加载监控文件失败: {e}")
        return {}
    
    def save_watches(self, watches: Dict[str, Any]) -> None:
        """保存监控任务"""
        try:
            with open(self.watches_file, 'w', encoding='utf-8') as f:
                json.dump(watches, f, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ConfigError(f"保存监控文件失败: {e}")


# 全局配置实例
_config: Optional[Config] = None


def get_config() -> Config:
    """获取配置实例（单例模式）"""
    global _config
    if _config is None:
        _config = Config()
    return _config