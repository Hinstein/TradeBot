"""
基本功能测试
不依赖外部库的测试
"""
import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_config_module() -> None:
    """测试配置模块"""
    print("测试配置模块...")
    
    # 临时设置环境变量
    test_env = {
        "TELEGRAM_BOT_TOKEN": "test_token_123",
        "BINANCE_API_KEY": "test_api_key_456",
        "BINANCE_API_SECRET": "test_api_secret_789",
        "TELEGRAM_ALLOWED_USER_IDS": "123456,789012",
        "BINANCE_TESTNET": "true",
        "DEFAULT_SIDE": "long",
        "DEFAULT_LEVERAGE": "5",
        "DEFAULT_MARGIN_USDT": "50",
        "DEFAULT_TP_PCT": "5",
        "DEFAULT_SL_PCT": "2",
    }
    
    # 保存原始环境变量
    original_env = {}
    for key in test_env:
        if key in os.environ:
            original_env[key] = os.environ[key]
        os.environ[key] = test_env[key]
    
    try:
        from utils.config import get_config, ConfigError
        
        # 测试配置创建
        config = get_config()
        print("✅ 配置实例创建成功")
        
        # 测试属性访问
        assert config.bot_token == "test_token_123"
        assert config.api_key == "test_api_key_456"
        assert config.api_secret == "test_api_secret_789"
        assert config.testnet == True
        assert config.allowed_user_ids == {123456, 789012}
        print("✅ 配置属性访问成功")
        
        # 测试设置加载
        settings = config.load_settings()
        expected_keys = {"side", "leverage", "margin", "tp", "sl", "armed", "tp_mode", "tp1_pct", "tp2_pct"}
        assert all(key in settings for key in expected_keys)
        print("✅ 设置加载成功")
        
        # 测试设置保存
        test_settings = settings.copy()
        test_settings["leverage"] = 10
        config.save_settings(test_settings)
        
        # 重新加载验证
        reloaded_settings = config.load_settings()
        assert reloaded_settings["leverage"] == 10
        print("✅ 设置保存和重新加载成功")
        
        # 测试无效设置验证
        try:
            invalid_settings = test_settings.copy()
            invalid_settings["leverage"] = 200  # 超出范围
            config.save_settings(invalid_settings)
            print("❌ 无效设置验证失败")
        except ConfigError:
            print("✅ 无效设置验证成功")
        
        print("✅ 配置模块测试通过")
        
    except Exception as e:
        print(f"❌ 配置模块测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 恢复原始环境变量
        for key in test_env:
            if key in original_env:
                os.environ[key] = original_env[key]
            else:
                os.environ.pop(key, None)


def test_logger_module() -> None:
    """测试日志模块"""
    print("\n测试日志模块...")
    
    try:
        from utils.logger import setup_global_logging, get_bot_logger, get_trader_logger
        
        # 设置日志
        setup_global_logging(log_level="INFO")
        print("✅ 日志设置成功")
        
        # 测试日志记录器
        bot_logger = get_bot_logger()
        trader_logger = get_trader_logger()
        
        assert bot_logger.name == "bot"
        assert trader_logger.name == "trader"
        print("✅ 日志记录器创建成功")
        
        print("✅ 日志模块测试通过")
        
    except Exception as e:
        print(f"❌ 日志模块测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_risk_module() -> None:
    """测试风险模块"""
    print("\n测试风险模块...")
    
    try:
        from trader.risk import get_risk_manager
        
        risk_manager = get_risk_manager()
        print("✅ 风险管理器创建成功")
        
        # 测试交易参数验证
        is_valid, errors = risk_manager.validate_trade_params(
            leverage=5,
            margin_usdt=50.0,
            tp_pct=5.0,
            sl_pct=2.0,
            side="long",
            account_balance=1000.0
        )
        
        assert is_valid == True
        assert len(errors) == 0
        print("✅ 交易参数验证成功")
        
        # 测试无效参数
        is_valid, errors = risk_manager.validate_trade_params(
            leverage=200,  # 超出范围
            margin_usdt=50.0,
            tp_pct=5.0,
            sl_pct=2.0,
            side="long",
            account_balance=1000.0
        )
        
        assert is_valid == False
        assert len(errors) > 0
        print("✅ 无效参数验证成功")
        
        # 测试分批止盈参数验证
        is_valid, errors = risk_manager.validate_split_tp_params(
            tp1_pct=3.0,
            tp2_pct=5.0,
            sl_pct=2.0
        )
        
        assert is_valid == True
        print("✅ 分批止盈参数验证成功")
        
        # 测试无效分批止盈参数
        is_valid, errors = risk_manager.validate_split_tp_params(
            tp1_pct=5.0,  # TP1 >= TP2
            tp2_pct=3.0,
            sl_pct=2.0
        )
        
        assert is_valid == False
        print("✅ 无效分批止盈参数验证成功")
        
        # 测试风险报告
        report = risk_manager.get_risk_report()
        assert "limits" in report
        assert "trade_stats" in report
        assert "risk_assessment" in report
        print("✅ 风险报告生成成功")
        
        print("✅ 风险模块测试通过")
        
    except Exception as e:
        print(f"❌ 风险模块测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_panel_module() -> None:
    """测试面板模块"""
    print("\n测试面板模块...")
    
    try:
        from bot.panels import get_panel_manager
        
        panel_manager = get_panel_manager()
        print("✅ 面板管理器创建成功")
        
        # 测试设置
        test_settings = {
            "side": "long",
            "leverage": 5,
            "margin": 50.0,
            "tp": 5.0,
            "sl": 2.0,
            "armed": False,
            "tp_mode": "split",
            "tp1_pct": 3.0,
            "tp2_pct": 5.0,
        }
        
        # 测试面板文本生成
        panel_text = panel_manager.generate_panel_text(test_settings)
        assert len(panel_text) > 0
        assert "交易配置" in panel_text
        assert "LONG" in panel_text
        print("✅ 面板文本生成成功")
        
        # 测试持仓文本生成
        test_positions = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.01",
                "entryPrice": "50000.0",
                "markPrice": "51000.0",
                "unRealizedProfit": "100.0",
            }
        ]
        
        test_watches = {
            "BTCUSDT": {"phase": 1}
        }
        
        positions_text = panel_manager.generate_positions_text(test_positions, test_watches)
        assert len(positions_text) > 0
        assert "持仓" in positions_text
        print("✅ 持仓文本生成成功")
        
        # 测试自定义输入验证
        is_valid, error_msg = panel_manager.validate_custom_input(
            "leverage", 10, test_settings
        )
        assert is_valid == True
        print("✅ 自定义输入验证成功")
        
        # 测试无效自定义输入
        is_valid, error_msg = panel_manager.validate_custom_input(
            "leverage", 200, test_settings  # 超出范围
        )
        assert is_valid == False
        print("✅ 无效自定义输入验证成功")
        
        print("✅ 面板模块测试通过")
        
    except Exception as e:
        print(f"❌ 面板模块测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_keyboard_module() -> None:
    """测试键盘模块"""
    print("\n测试键盘模块...")
    
    try:
        from bot.keyboards import get_keyboard_manager
        
        keyboard_manager = get_keyboard_manager()
        print("✅ 键盘管理器创建成功")
        
        # 测试设置
        test_settings = {
            "side": "long",
            "leverage": 5,
            "margin": 50.0,
            "tp": 5.0,
            "sl": 2.0,
            "armed": False,
            "tp_mode": "single",
            "tp1_pct": 3.0,
            "tp2_pct": 5.0,
        }
        
        # 测试选项列表获取
        leverage_choices = keyboard_manager.get_choice_list("leverage")
        assert len(leverage_choices) > 0
        print("✅ 选项列表获取成功")
        
        # 测试提示文本获取
        hint = keyboard_manager.get_custom_input_hint("leverage")
        assert len(hint) > 0
        print("✅ 提示文本获取成功")
        
        print("✅ 键盘模块测试通过")
        
    except Exception as e:
        print(f"❌ 键盘模块测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_file_structure() -> None:
    """测试文件结构"""
    print("\n测试文件结构...")
    
    required_dirs = ["bot", "trader", "utils"]
    for dir_name in required_dirs:
        dir_path = project_root / dir_name
        if dir_path.exists() and dir_path.is_dir():
            print(f"✅ 目录: {dir_name}")
        else:
            print(f"❌ 目录缺失: {dir_name}")
            return False
    
    required_files = [
        "main.py",
        "bot/__init__.py",
        "bot/handlers.py",
        "bot/keyboards.py",
        "bot/panels.py",
        "bot/watches.py",
        "trader/__init__.py",
        "trader/client.py",
        "trader/orders.py",
        "trader/risk.py",
        "utils/config.py",
        "utils/logger.py",
        "utils/retry.py",
        "compatibility.py",
        "init.py",
    ]
    
    all_files_exist = True
    for file_path in required_files:
        full_path = project_root / file_path
        if full_path.exists():
            print(f"✅ 文件: {file_path}")
        else:
            print(f"❌ 文件缺失: {file_path}")
            all_files_exist = False
    
    return all_files_exist


def main() -> None:
    """主测试函数"""
    print("=" * 60)
    print("Binance交易机器人 - 基本功能测试")
    print("=" * 60)
    
    # 测试文件结构
    if not test_file_structure():
        print("\n❌ 文件结构不完整，测试终止")
        return
    
    # 运行模块测试
    tests = [
        ("配置模块", test_config_module),
        ("日志模块", test_logger_module),
        ("风险模块", test_risk_module),
        ("面板模块", test_panel_module),
        ("键盘模块", test_keyboard_module),
    ]
    
    all_passed = True
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            test_func()
        except Exception as e:
            print(f"❌ 测试异常: {e}")
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 所有基本功能测试通过")
        print("\n下一步:")
        print("1. 安装依赖: pip install -r requirements.txt")
        print("2. 运行环境检查: python init.py")
        print("3. 启动机器人: ./run.sh")
    else:
        print("❌ 部分测试失败，请检查代码")
    print("=" * 60)


if __name__ == "__main__":
    main()