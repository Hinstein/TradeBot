"""
键盘布局模块
处理Telegram Bot的键盘和按钮
"""
from typing import Dict, Any, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


class KeyboardManager:
    """键盘管理器"""
    
    def __init__(self):
        # 配置选项
        self.leverage_choices = [1, 3, 5, 10, 20, 50, 75, 100]
        self.margin_choices = [10, 20, 50, 100, 200, 500, 1000]
        self.tp_choices = [1, 2, 3, 5, 8, 10, 15, 20]
        self.sl_choices = [0.5, 1, 2, 3, 5, 8, 10]
        self.quick_tokens = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP"]
    
    def create_panel_keyboard(self, settings: Dict[str, Any]) -> InlineKeyboardMarkup:
        """
        创建主面板键盘
        
        Args:
            settings: 用户设置
            
        Returns:
            键盘布局
        """
        environment = settings.get("environment", "testnet")
        side_btn_text = "🔄 切换到 SHORT" if settings["side"] == "long" else "🔄 切换到 LONG"
        lock_btn = "🔒 锁定开仓" if settings.get("armed") else "🔓 解锁开仓"
        
        # 环境切换按钮文本
        if environment == "testnet":
            env_btn_text = "🧪 测试环境"
            env_switch_btn = "💰 切换到生产环境"
        else:
            env_btn_text = "💰 生产环境"
            env_switch_btn = "🧪 切换到测试环境"
        
        # 止盈按钮文本
        if settings.get("tp_mode") == "split":
            tp_btn = "🎯 止盈 (分批)"
        else:
            tp_btn = f"🎯 止盈 +{settings['tp']:g}%"
        
        # 构建键盘行
        rows = [
            [InlineKeyboardButton(lock_btn, callback_data="toggle:armed")],
            [InlineKeyboardButton(side_btn_text, callback_data="toggle:side")],
            [
                InlineKeyboardButton(f"⚡ 杠杆 {settings['leverage']}x", callback_data="menu:leverage"),
                InlineKeyboardButton(f"💰 保证金 {settings['margin']:g}U", callback_data="menu:margin"),
            ],
            [
                InlineKeyboardButton(tp_btn, callback_data="menu:tp"),
                InlineKeyboardButton(f"🛡 止损 -{settings['sl']:g}%", callback_data="menu:sl"),
            ],
            [InlineKeyboardButton(env_btn_text, callback_data="menu:environment")],
            [InlineKeyboardButton("── ⚡ 一键开仓 ──", callback_data="noop")],
        ]
        
        # 快速交易按钮
        for i in range(0, len(self.quick_tokens), 3):
            rows.append([
                InlineKeyboardButton(token, callback_data=f"trade:{token}")
                for token in self.quick_tokens[i:i + 3]
            ])
        
        # 持仓管理按钮
        rows.append([
            InlineKeyboardButton("📊 持仓", callback_data="positions"),
            InlineKeyboardButton("✖️ 全部平仓", callback_data="close_all_ask"),
        ])
        
        # 刷新按钮
        rows.append([InlineKeyboardButton("🔄 刷新", callback_data="refresh")])
        
        return InlineKeyboardMarkup(rows)
    
    def create_environment_menu_keyboard(self, settings: Dict[str, Any]) -> InlineKeyboardMarkup:
        """
        创建环境菜单键盘
        
        Args:
            settings: 用户设置
            
        Returns:
            键盘布局
        """
        from utils.config import get_config
        
        current_env = settings.get("environment", "testnet")
        config = get_config()
        env_info = config.get_environment_info()
        
        rows = []
        
        # 环境选择按钮
        testnet_mark = " ✓" if current_env == "testnet" else ""
        mainnet_mark = " ✓" if current_env == "mainnet" else ""
        
        rows.append([
            InlineKeyboardButton(f"🧪 测试环境{testnet_mark}", callback_data="set:environment:testnet"),
            InlineKeyboardButton(f"💰 生产环境{mainnet_mark}", callback_data="set:environment:mainnet"),
        ])
        
        # 环境信息
        rows.append([InlineKeyboardButton("── 环境信息 ──", callback_data="noop")])
        
        # 测试环境信息
        testnet_info = []
        if env_info.get("has_testnet_keys"):
            testnet_info.append("✅ 专用测试密钥")
        else:
            testnet_info.append("⚠️ 使用主网密钥")
        
        rows.append([InlineKeyboardButton(
            f"测试环境: {', '.join(testnet_info)}",
            callback_data="noop"
        )])
        
        # 生产环境信息
        mainnet_info = []
        if env_info.get("has_mainnet_keys"):
            mainnet_info.append("✅ 主网密钥已设置")
        else:
            mainnet_info.append("❌ 主网密钥未设置")
        
        rows.append([InlineKeyboardButton(
            f"生产环境: {', '.join(mainnet_info)}",
            callback_data="noop"
        )])
        
        # 警告信息（如果切换到生产环境但未设置密钥）
        if current_env == "testnet" and not env_info.get("has_mainnet_keys"):
            rows.append([InlineKeyboardButton(
                "⚠️ 切换到生产环境需要设置主网API密钥",
                callback_data="noop"
            )])
        
        # 返回按钮
        rows.append([InlineKeyboardButton("⬅ 返回配置", callback_data="refresh")])
        
        return InlineKeyboardMarkup(rows)
    
    def create_choice_keyboard(
        self, 
        key: str, 
        choices: List[float], 
        current: float, 
        back_label: str
    ) -> InlineKeyboardMarkup:
        """
        创建选择键盘
        
        Args:
            key: 设置键名
            choices: 选项列表
            current: 当前值
            back_label: 返回按钮标签
            
        Returns:
            键盘布局
        """
        rows, row = [], []
        
        for value in choices:
            mark = " ✓" if float(value) == float(current) else ""
            row.append(InlineKeyboardButton(f"{value:g}{mark}", callback_data=f"set:{key}:{value}"))
            
            if len(row) == 4:
                rows.append(row)
                row = []
        
        if row:
            rows.append(row)
        
        # 自定义选项
        rows.append([InlineKeyboardButton("✏️ 自定义", callback_data=f"custom:{key}")])
        
        # 返回按钮
        rows.append([InlineKeyboardButton(f"⬅ 返回 {back_label}", callback_data="refresh")])
        
        return InlineKeyboardMarkup(rows)
    
    def create_tp_menu_keyboard(self, settings: Dict[str, Any]) -> InlineKeyboardMarkup:
        """
        创建止盈菜单键盘
        
        Args:
            settings: 用户设置
            
        Returns:
            键盘布局
        """
        mode = settings.get("tp_mode", "single")
        single_mark = " ✓" if mode == "single" else ""
        split_mark = " ✓" if mode == "split" else ""
        
        rows = [
            [
                InlineKeyboardButton(f"单一止盈{single_mark}", callback_data="tp_mode:single"),
                InlineKeyboardButton(f"分批止盈{split_mark}", callback_data="tp_mode:split"),
            ],
        ]
        
        if mode == "single":
            rows.append([InlineKeyboardButton("── 单一止盈设置 ──", callback_data="noop")])
            
            row = []
            for value in self.tp_choices:
                mark = " ✓" if float(value) == float(settings.get("tp", 5)) else ""
                row.append(InlineKeyboardButton(f"{value:g}%{mark}", callback_data=f"set:tp:{value}"))
                
                if len(row) == 4:
                    rows.append(row)
                    row = []
            
            if row:
                rows.append(row)
            
            rows.append([InlineKeyboardButton("✏️ 自定义", callback_data="custom:tp")])
        
        else:
            rows.append([InlineKeyboardButton("── 分批止盈设置 ──", callback_data="noop")])
            
            rows.append([
                InlineKeyboardButton(
                    f"第一批 TP1: +{settings.get('tp1_pct', 3):g}%", 
                    callback_data="custom:tp1_pct"
                ),
                InlineKeyboardButton(
                    f"第二批 TP2: +{settings.get('tp2_pct', 5):g}%", 
                    callback_data="custom:tp2_pct"
                ),
            ])
            
            rows.append([InlineKeyboardButton(
                "ℹ️ 各平50%仓，TP1触发后止损移至成本价", 
                callback_data="noop"
            )])
        
        # 返回按钮
        rows.append([InlineKeyboardButton("⬅ 返回配置", callback_data="refresh")])
        
        return InlineKeyboardMarkup(rows)
    
    def create_close_all_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        """
        创建全部平仓确认键盘
        
        Returns:
            键盘布局
        """
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 确认全部平仓", callback_data="close_all_do"),
            InlineKeyboardButton("❌ 取消", callback_data="refresh"),
        ]])
    
    def get_choice_list(self, key: str) -> List[float]:
        """
        获取选项列表
        
        Args:
            key: 设置键名
            
        Returns:
            选项列表
        """
        choice_map = {
            "leverage": self.leverage_choices,
            "margin": self.margin_choices,
            "sl": self.sl_choices,
        }
        
        return choice_map.get(key, [])
    
    def get_custom_input_hint(self, key: str) -> str:
        """
        获取自定义输入提示
        
        Args:
            key: 设置键名
            
        Returns:
            提示文本
        """
        hints = {
            "leverage": "1 ~ 125，整数",
            "margin": "USDT，最小 1",
            "tp": "止盈 %，如 2.5",
            "sl": "止损 %，如 1.5",
            "tp1_pct": "第一批止盈 %，如 3.0",
            "tp2_pct": "第二批止盈 %，如 5.0",
        }
        
        return hints.get(key, "")


# 全局键盘管理器实例
_keyboard_manager: 'KeyboardManager' = None


def get_keyboard_manager() -> KeyboardManager:
    """获取键盘管理器实例（单例模式）"""
    global _keyboard_manager
    if _keyboard_manager is None:
        _keyboard_manager = KeyboardManager()
    return _keyboard_manager