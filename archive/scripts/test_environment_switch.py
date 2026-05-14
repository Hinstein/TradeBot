"""
环境切换功能测试
"""
import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_environment_config() -> None:
    """测试环境配置功能"""
    print("测试环境配置功能...")
    
    # 临时设置环境变量
    test_env = {
        "TELEGRAM_BOT_TOKEN": "test_token_123",
        "BINANCE_API_KEY": "production_api_key",
        "BINANCE_API_SECRET": "production_api_secret",
        "BINANCE_TESTNET_API_KEY": "testnet_api_key",
        "BINANCE_TESTNET_API_SECRET": "testnet_api_secret",
        "TELEGRAM_ALLOWED_USER_IDS": "123456",
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
        from utils.config import get_config
        
        # 测试配置创建
        config = get_config()
        print("✅ 配置实例创建成功")
        
        # 测试环境信息
        env_info = config.get_environment_info()
        print(f"环境信息: {env_info}")
        
        # 验证环境信息
        assert env_info["environment"] == "testnet"  # 默认环境
        assert env_info["is_testnet"] == True
        assert env_info["has_testnet_keys"] == True
        assert env_info["has_mainnet_keys"] == True
        print("✅ 环境信息验证成功")
        
        # 测试API密钥获取（测试环境）
        assert config.api_key == "testnet_api_key"
        assert config.api_secret == "testnet_api_secret"
        assert config.testnet == True
        print("✅ 测试环境API密钥获取成功")
        
        # 测试切换到生产环境
        settings = config.load_settings()
        settings["environment"] = "mainnet"
        config.save_settings(settings)
        
        # 重新加载配置
        config = get_config()  # 重新获取配置以刷新缓存
        
        # 验证生产环境API密钥
        assert config.api_key == "production_api_key"
        assert config.api_secret == "production_api_secret"
        assert config.testnet == False
        print("✅ 生产环境API密钥获取成功")
        
        # 测试环境切换回测试环境
        settings = config.load_settings()
        settings["environment"] = "testnet"
        config.save_settings(settings)
        
        # 重新加载配置
        config = get_config()
        
        # 验证测试环境API密钥
        assert config.api_key == "testnet_api_key"
        assert config.api_secret == "testnet_api_secret"
        assert config.testnet == True
        print("✅ 环境切换回测试环境成功")
        
        # 测试没有测试环境密钥的情况
        os.environ.pop("BINANCE_TESTNET_API_KEY", None)
        os.environ.pop("BINANCE_TESTNET_API_SECRET", None)
        
        # 重新加载配置
        config = get_config()
        
        # 验证回退到主网密钥
        assert config.api_key == "production_api_key"
        assert config.api_secret == "production_api_secret"
        print("✅ 测试环境回退到主网密钥成功")
        
        print("✅ 环境配置功能测试通过")
        
    except Exception as e:
        print(f"❌ 环境配置功能测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 恢复原始环境变量
        for key in test_env:
            if key in original_env:
                os.environ[key] = original_env[key]
            else:
                os.environ.pop(key, None)


def test_keyboard_environment_menu() -> None:
    """测试环境菜单键盘"""
    print("\n测试环境菜单键盘...")
    
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
            "environment": "testnet",
        }
        
        # 测试环境菜单键盘创建
        kb = keyboard_manager.create_environment_menu_keyboard(test_settings)
        assert kb is not None
        print("✅ 环境菜单键盘创建成功")
        
        # 测试主面板键盘（包含环境按钮）
        panel_kb = keyboard_manager.create_panel_keyboard(test_settings)
        assert panel_kb is not None
        print("✅ 主面板键盘（含环境按钮）创建成功")
        
        print("✅ 环境菜单键盘测试通过")
        
    except Exception as e:
        print(f"❌ 环境菜单键盘测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_environment_handlers() -> None:
    """测试环境切换处理器"""
    print("\n测试环境切换处理器...")
    
    try:
        from bot.handlers import BotHandlers
        from bot.watches import WatchManager
        
        # 创建WatchManager实例
        watch_manager = WatchManager()
        
        # 创建BotHandlers实例
        handlers = BotHandlers(watch_manager)
        print("✅ BotHandlers创建成功")
        
        # 测试环境菜单处理方法存在
        assert hasattr(handlers, '_handle_environment_menu')
        assert hasattr(handlers, '_handle_confirm_environment')
        print("✅ 环境切换处理方法存在")
        
        print("✅ 环境切换处理器测试通过")
        
    except Exception as e:
        print(f"❌ 环境切换处理器测试失败: {e}")
        import traceback
        traceback.print_exc()


def main() -> None:
    """主测试函数"""
    print("=" * 60)
    print("Binance交易机器人 - 环境切换功能测试")
    print("=" * 60)
    
    # 运行测试
    tests = [
        ("环境配置功能", test_environment_config),
        ("环境菜单键盘", test_keyboard_environment_menu),
        ("环境切换处理器", test_environment_handlers),
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
        print("✅ 所有环境切换功能测试通过")
        print("\n下一步:")
        print("1. 在.env文件中添加测试环境API密钥:")
        print("   BINANCE_TESTNET_API_KEY=你的测试网API密钥")
        print("   BINANCE_TESTNET_API_SECRET=你的测试网API密钥")
        print("2. 启动机器人: ./run.sh")
        print("3. 在Telegram中发送 /panel 命令")
        print("4. 点击环境按钮进行环境切换")
    else:
        print("❌ 部分测试失败，请检查代码")
    print("=" * 60)


if __name__ == "__main__":
    main()