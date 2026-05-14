"""
风险管理模块
处理交易风险控制和验证
"""
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

from utils.logger import get_trader_logger

logger = get_trader_logger()


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class RiskError(Exception):
    """风险错误"""
    pass


class RiskManager:
    """风险管理器"""
    
    def __init__(self):
        # 风险限制
        self.limits = {
            "max_leverage": 125,
            "min_leverage": 1,
            "max_margin_per_trade": 10000,  # USDT
            "min_margin_per_trade": 1,      # USDT
            "max_position_size": 100000,    # USDT名义价值
            "max_daily_trades": 50,
            "max_concurrent_positions": 10,
            "max_drawdown_percent": 20,     # 最大回撤百分比
        }
        
        # 风险参数
        self.risk_params = {
            "low": {
                "max_leverage": 5,
                "max_margin_percent": 5,    # 账户余额百分比
            },
            "medium": {
                "max_leverage": 10,
                "max_margin_percent": 10,
            },
            "high": {
                "max_leverage": 20,
                "max_margin_percent": 20,
            },
            "extreme": {
                "max_leverage": 125,
                "max_margin_percent": 50,
            }
        }
        
        # 交易统计
        self.trade_stats = {
            "daily_trades": 0,
            "concurrent_positions": 0,
            "total_pnl": Decimal("0"),
            "max_drawdown": Decimal("0"),
            "win_rate": 0,
        }
    
    def validate_trade_params(
        self,
        leverage: int,
        margin_usdt: float,
        tp_pct: float,
        sl_pct: float,
        side: str,
        account_balance: Optional[float] = None
    ) -> Tuple[bool, List[str]]:
        """
        验证交易参数
        
        Args:
            leverage: 杠杆倍数
            margin_usdt: 保证金金额
            tp_pct: 止盈百分比
            sl_pct: 止损百分比
            side: 交易方向
            account_balance: 账户余额（可选）
            
        Returns:
            (是否有效, 错误消息列表)
        """
        errors = []
        
        # 验证杠杆
        if not self.limits["min_leverage"] <= leverage <= self.limits["max_leverage"]:
            errors.append(
                f"杠杆必须在{self.limits['min_leverage']}-{self.limits['max_leverage']}之间"
            )
        
        # 验证保证金
        if margin_usdt < self.limits["min_margin_per_trade"]:
            errors.append(f"保证金必须至少{self.limits['min_margin_per_trade']} USDT")
        
        if margin_usdt > self.limits["max_margin_per_trade"]:
            errors.append(f"保证金不能超过{self.limits['max_margin_per_trade']} USDT")
        
        # 验证账户余额百分比
        if account_balance:
            margin_percent = (margin_usdt / account_balance) * 100
            risk_level = self._assess_risk_level(leverage, margin_percent)
            
            if risk_level == RiskLevel.EXTREME:
                errors.append("风险等级过高: 杠杆和保证金比例组合风险极大")
            elif risk_level == RiskLevel.HIGH:
                logger.warning("高风险交易: 杠杆和保证金比例较高")
        
        # 验证止盈止损
        if tp_pct <= 0:
            errors.append("止盈百分比必须大于0")
        
        if sl_pct <= 0:
            errors.append("止损百分比必须大于0")
        
        # 验证风险回报比
        risk_reward_ratio = tp_pct / sl_pct if sl_pct > 0 else 0
        if risk_reward_ratio < 1:
            errors.append(f"风险回报比过低 ({risk_reward_ratio:.2f})，建议至少1:1")
        
        # 验证交易方向
        if side not in ["long", "short"]:
            errors.append(f"无效的交易方向: {side}")
        
        # 验证每日交易次数
        if self.trade_stats["daily_trades"] >= self.limits["max_daily_trades"]:
            errors.append(f"已达到每日最大交易次数: {self.limits['max_daily_trades']}")
        
        # 验证并发持仓数量
        if self.trade_stats["concurrent_positions"] >= self.limits["max_concurrent_positions"]:
            errors.append(f"已达到最大并发持仓数量: {self.limits['max_concurrent_positions']}")
        
        return len(errors) == 0, errors
    
    def validate_split_tp_params(
        self,
        tp1_pct: float,
        tp2_pct: float,
        sl_pct: float
    ) -> Tuple[bool, List[str]]:
        """
        验证分批止盈参数
        
        Args:
            tp1_pct: 第一批止盈百分比
            tp2_pct: 第二批止盈百分比
            sl_pct: 止损百分比
            
        Returns:
            (是否有效, 错误消息列表)
        """
        errors = []
        
        # 验证止盈百分比
        if tp1_pct <= 0 or tp2_pct <= 0:
            errors.append("止盈百分比必须大于0")
        
        # 验证TP1 < TP2
        if tp1_pct >= tp2_pct:
            errors.append("TP1必须小于TP2")
        
        # 验证风险回报比
        avg_tp = (tp1_pct + tp2_pct) / 2
        risk_reward_ratio = avg_tp / sl_pct if sl_pct > 0 else 0
        
        if risk_reward_ratio < 1:
            errors.append(f"平均风险回报比过低 ({risk_reward_ratio:.2f})")
        
        return len(errors) == 0, errors
    
    def _assess_risk_level(self, leverage: int, margin_percent: float) -> RiskLevel:
        """
        评估风险等级
        
        Args:
            leverage: 杠杆倍数
            margin_percent: 保证金占账户余额百分比
            
        Returns:
            风险等级
        """
        # 简单风险评估逻辑
        risk_score = (leverage / 125) * 100 * (margin_percent / 100)
        
        if risk_score < 10:
            return RiskLevel.LOW
        elif risk_score < 25:
            return RiskLevel.MEDIUM
        elif risk_score < 50:
            return RiskLevel.HIGH
        else:
            return RiskLevel.EXTREME
    
    def calculate_position_size(
        self,
        margin_usdt: float,
        leverage: int,
        entry_price: Decimal,
        quantity: Decimal
    ) -> Decimal:
        """
        计算仓位大小（名义价值）
        
        Args:
            margin_usdt: 保证金金额
            leverage: 杠杆倍数
            entry_price: 入场价格
            quantity: 数量
            
        Returns:
            仓位名义价值
        """
        position_size = entry_price * quantity
        
        # 验证仓位大小
        if position_size > Decimal(self.limits["max_position_size"]):
            raise RiskError(
                f"仓位大小 {position_size} USDT 超过最大限制 {self.limits['max_position_size']} USDT"
            )
        
        return position_size
    
    def calculate_max_loss(
        self,
        position_size: Decimal,
        sl_pct: float,
        leverage: int
    ) -> Decimal:
        """
        计算最大损失
        
        Args:
            position_size: 仓位名义价值
            sl_pct: 止损百分比
            leverage: 杠杆倍数
            
        Returns:
            最大损失金额
        """
        # 实际损失 = 仓位价值 * 止损百分比 / 杠杆
        max_loss = position_size * Decimal(sl_pct) / Decimal(100) / Decimal(leverage)
        return max_loss
    
    def calculate_required_margin(
        self,
        position_size: Decimal,
        leverage: int
    ) -> Decimal:
        """
        计算所需保证金
        
        Args:
            position_size: 仓位名义价值
            leverage: 杠杆倍数
            
        Returns:
            所需保证金
        """
        return position_size / Decimal(leverage)
    
    def update_trade_stats(
        self,
        pnl: Decimal,
        is_win: bool,
        position_opened: bool = True,
        position_closed: bool = False
    ) -> None:
        """
        更新交易统计
        
        Args:
            pnl: 盈亏金额
            is_win: 是否盈利
            position_opened: 是否开仓
            position_closed: 是否平仓
        """
        # 更新总盈亏
        self.trade_stats["total_pnl"] += pnl
        
        # 更新最大回撤
        if pnl < 0 and abs(pnl) > self.trade_stats["max_drawdown"]:
            self.trade_stats["max_drawdown"] = abs(pnl)
        
        # 更新胜率
        total_trades = self.trade_stats["daily_trades"]
        if total_trades > 0:
            current_win_rate = self.trade_stats["win_rate"]
            new_win_rate = ((current_win_rate * total_trades) + (1 if is_win else 0)) / (total_trades + 1)
            self.trade_stats["win_rate"] = new_win_rate
        
        # 更新交易计数
        if position_opened:
            self.trade_stats["daily_trades"] += 1
            self.trade_stats["concurrent_positions"] += 1
        
        if position_closed:
            self.trade_stats["concurrent_positions"] = max(
                0, self.trade_stats["concurrent_positions"] - 1
            )
    
    def get_risk_report(self) -> Dict[str, Any]:
        """
        获取风险报告
        
        Returns:
            风险报告
        """
        return {
            "limits": self.limits,
            "trade_stats": {
                "daily_trades": self.trade_stats["daily_trades"],
                "concurrent_positions": self.trade_stats["concurrent_positions"],
                "total_pnl": float(self.trade_stats["total_pnl"]),
                "max_drawdown": float(self.trade_stats["max_drawdown"]),
                "win_rate": self.trade_stats["win_rate"] * 100,
            },
            "risk_assessment": self._get_risk_assessment(),
        }
    
    def _get_risk_assessment(self) -> Dict[str, Any]:
        """获取风险评估"""
        daily_trades = self.trade_stats["daily_trades"]
        concurrent_positions = self.trade_stats["concurrent_positions"]
        max_drawdown = float(self.trade_stats["max_drawdown"])
        
        warnings = []
        
        # 检查交易频率
        if daily_trades > self.limits["max_daily_trades"] * 0.8:
            warnings.append("交易频率接近每日上限")
        
        # 检查持仓数量
        if concurrent_positions > self.limits["max_concurrent_positions"] * 0.8:
            warnings.append("并发持仓数量接近上限")
        
        # 检查回撤
        if max_drawdown > self.limits["max_drawdown_percent"]:
            warnings.append(f"最大回撤 {max_drawdown}% 超过限制 {self.limits['max_drawdown_percent']}%")
        
        return {
            "warnings": warnings,
            "is_healthy": len(warnings) == 0,
        }
    
    def reset_daily_stats(self) -> None:
        """重置每日统计"""
        self.trade_stats["daily_trades"] = 0
        self.trade_stats["total_pnl"] = Decimal("0")
        self.trade_stats["max_drawdown"] = Decimal("0")
        logger.info("每日交易统计已重置")


# 全局风险管理器实例
_risk_manager: Optional[RiskManager] = None


def get_risk_manager() -> RiskManager:
    """获取风险管理器实例（单例模式）"""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager