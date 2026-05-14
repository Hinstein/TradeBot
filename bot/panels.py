"""
面板显示模块
处理Telegram Bot的面板显示和文本生成
"""
from typing import Dict, Any
from telegram.constants import ParseMode

from bot.keyboards import get_keyboard_manager
from utils.config import get_config
from utils.logger import get_bot_logger

logger = get_bot_logger()


class PanelManager:
    """面板管理器"""
    
    def __init__(self):
        self.config = get_config()
        self.keyboard_manager = get_keyboard_manager()
    
    def generate_panel_text(self, settings: Dict[str, Any]) -> str:
        """
        生成面板文本
        
        Args:
            settings: 用户设置
            
        Returns:
            面板文本
        """
        environment = settings.get("environment", "testnet")
        
        if environment == "testnet":
            env = "🧪 测试环境"
            env_icon = "🧪"
            warn = ""
        else:
            env = "💰 *生产环境*"
            env_icon = "💰"
            warn = "\n⚠️ *真钱环境* 请再三确认参数"
        
        side_tag = "↗️ LONG" if settings["side"] == "long" else "↘️ SHORT"
        armed = "🟢 已解锁" if settings.get("armed") else "🔒 已锁定"
        
        # 止盈文本
        if settings.get("tp_mode") == "split":
            tp_line = f"🎯 止盈: 分批 `+{settings['tp1_pct']:g}%` / `+{settings['tp2_pct']:g}%`"
        else:
            tp_line = f"🎯 止盈: `+{settings['tp']:g}%`"
        
        # 获取环境信息
        env_info = self.config.get_environment_info()
        api_source = env_info.get("api_key_source", "")
        
        return (
            f"*📊 交易配置*  {env}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔐 开仓锁: {armed}\n"
            f"🧭 方向: `{side_tag}`\n"
            f"⚡ 杠杆: `{settings['leverage']}x`\n"
            f"💰 保证金: `{settings['margin']} USDT`\n"
            f"{tp_line}\n"
            f"🛡 止损: `-{settings['sl']:g}%`{warn}\n"
            f"\n_需先解锁才能点币种开仓_"
            f"\n_环境: {environment} ({api_source})_"
        )
    
    def generate_positions_text(self, positions: list, watches: Dict[str, Any]) -> str:
        """
        生成持仓文本
        
        Args:
            positions: 持仓列表
            watches: 监控任务
            
        Returns:
            持仓文本
        """
        if not positions:
            return "📊 当前无持仓"
        
        lines = ["*📊 持仓*"]
        
        for p in positions:
            from decimal import Decimal
            amt = Decimal(p.get("positionAmt", "0"))
            side_tag = "LONG ↗️" if amt > 0 else "SHORT ↘️"
            pnl = Decimal(p.get("unRealizedProfit", "0"))
            pnl_tag = "🟢" if pnl >= 0 else "🔴"
            symbol = p["symbol"]
            
            # 监控信息
            watch_info = ""
            if symbol in watches:
                phase = watches[symbol].get("phase", 1)
                if phase == 1:
                    watch_info = f"\n• 分批止盈: 等待TP1"
                else:
                    watch_info = f"\n• 分批止盈: 等待TP2 (止损已保本)"
            
            lines.append(
                f"\n*{symbol}* {side_tag}\n"
                f"• 数量: `{abs(amt)}`\n"
                f"• 开仓价: `{p['entryPrice']}`\n"
                f"• 标记价: `{p.get('markPrice', '?')}`\n"
                f"• 未实现: {pnl_tag} `{pnl}` USDT"
                f"{watch_info}"
            )
        
        return "\n".join(lines)
    
    def generate_trade_result_text(self, result: Dict[str, Any], split_tp: bool) -> str:
        """
        生成交易结果文本
        
        Args:
            result: 交易结果
            split_tp: 是否分批止盈
            
        Returns:
            交易结果文本
        """
        if split_tp:
            return (
                f"✅ *{result['symbol']}* {result['side']} (分批止盈)\n"
                f"• 成交价: `{result['entry']}`\n"
                f"• 数量: `{result['qty']}`\n"
                f"• 杠杆: `{result['leverage']}x`\n"
                f"• 保证金: `{result['margin']} USDT`\n"
                f"• TP1 (50%): `{result['tp1_price']}`\n"
                f"• TP2 (50%): `{result['tp2_price']}`\n"
                f"• 止损: `{result['sl_price']}`\n"
                f"• TP1触发后止损自动移至成本价"
            )
        else:
            return (
                f"✅ *{result['symbol']}* {result['side']}\n"
                f"• 成交价: `{result['entry']}`\n"
                f"• 数量: `{result['qty']}`\n"
                f"• 杠杆: `{result['leverage']}x`\n"
                f"• 保证金: `{result['margin']} USDT`\n"
                f"• 止盈: `{result['tp_price']}`\n"
                f"• 止损: `{result['sl_price']}`"
            )
    
    def generate_close_all_result_text(self, result: Dict[str, Any]) -> str:
        """
        生成全部平仓结果文本
        
        Args:
            result: 平仓结果
            
        Returns:
            平仓结果文本
        """
        if not result["closed"] and not result["failed"]:
            return "无持仓可平"
        
        lines = ["✅ 已平仓:"]
        
        for item in result["closed"]:
            lines.append(f"• {item}")
        
        if result["failed"]:
            lines.append("\n❌ 平仓失败:")
            for item in result["failed"]:
                lines.append(f"• {item}")
        
        return "\n".join(lines)
    
    def get_menu_title(self, key: str) -> str:
        """
        获取菜单标题
        
        Args:
            key: 菜单键名
            
        Returns:
            菜单标题
        """
        titles = {
            "leverage": "⚡ 杠杆",
            "margin": "💰 保证金 (USDT)", 
            "sl": "🛡 止损 %",
        }
        
        return titles.get(key, "设置")
    
    def validate_custom_input(
        self, 
        key: str, 
        value: float, 
        current_settings: Dict[str, Any]
    ) -> tuple[bool, str]:
        """
        验证自定义输入
        
        Args:
            key: 设置键名
            value: 输入值
            current_settings: 当前设置
            
        Returns:
            (是否有效, 错误消息)
        """
        limits = {
            "leverage": (1, 125),
            "margin": (1, 1_000_000),
            "tp": (0.1, 100),
            "sl": (0.1, 100),
            "tp1_pct": (0.1, 100),
            "tp2_pct": (0.1, 100),
        }
        
        # 检查范围
        if key in limits:
            lo, hi = limits[key]
            if not (lo <= value <= hi):
                return False, f"超出范围 ({lo} ~ {hi})"
        
        # 检查整数
        if key == "leverage":
            if not float(value).is_integer():
                return False, "杠杆必须是整数"
            value = int(value)
        
        # 检查分批止盈逻辑
        if key == "tp1_pct" and value >= current_settings.get("tp2_pct", 100):
            return False, "TP1 必须小于 TP2"
        
        if key == "tp2_pct" and value <= current_settings.get("tp1_pct", 0):
            return False, "TP2 必须大于 TP1"
        
        return True, ""
    
    def get_setting_label(self, key: str) -> str:
        """
        获取设置标签
        
        Args:
            key: 设置键名
            
        Returns:
            设置标签
        """
        labels = {
            "leverage": "杠杆",
            "margin": "保证金", 
            "tp": "止盈",
            "sl": "止损",
            "tp1_pct": "TP1",
            "tp2_pct": "TP2",
        }
        
        return labels.get(key, key)


# 全局面板管理器实例
_panel_manager: 'PanelManager' = None


def get_panel_manager() -> PanelManager:
    """获取面板管理器实例（单例模式）"""
    global _panel_manager
    if _panel_manager is None:
        _panel_manager = PanelManager()
    return _panel_manager