"""
验证设置脚本
确保重构后的项目可以正常工作
"""
import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def verify_imports() -> bool:
    """验证模块导入"""
    print("验证模块导入...")
    
    # 不依赖外部库的模块
    basic_modules = [
        ("utils.config", "get_config"),
        ("utils.logger", "setup_global_logging"),
        ("utils.retry", "RetryError"),
    ]
    
    all_imported = True
    for module_path, item_name in basic_modules:
        try:
            module = __import__(module_path, fromlist=[item_name])
            if hasattr(module, item_name):
                print(f"  ✅ {module_path}.{item_name}")
            else:
                print(f"  ❌ {module_path}.{item_name} (未找到)")
                all_imported = False
        except ImportError as e:
            print(f"  ❌ {module_path} (导入失败: {e})")
            all_imported = False
    
    return all_imported


def verify_file_structure() -> bool:
    """验证文件结构"""
    print("\n验证文件结构...")
    
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
        "run.sh",
        "requirements.txt",
        ".env.example",
        "DEPLOY.md",
    ]
    
    all_exist = True
    for file_path in required_files:
        full_path = project_root / file_path
        if full_path.exists():
            print(f"  ✅ {file_path}")
        else:
            print(f"  ❌ {file_path} (缺失)")
            all_exist = False
    
    return all_exist


def verify_config_example() -> bool:
    """验证配置示例文件"""
    print("\n验证配置示例文件...")
    
    env_example = project_root / ".env.example"
    if not env_example.exists():
        print("  ❌ .env.example 文件缺失")
        return False
    
    try:
        content = env_example.read_text()
        required_vars = [
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_ALLOWED_USER_IDS",
        ]
        
        missing = []
        for var in required_vars:
            if f"{var}=" not in content:
                missing.append(var)
        
        if missing:
            print(f"  ❌ .env.example 缺少必需变量: {', '.join(missing)}")
            return False
        
        print("  ✅ .env.example 文件完整")
        return True
        
    except Exception as e:
        print(f"  ❌ 读取 .env.example 失败: {e}")
        return False


def verify_requirements() -> bool:
    """验证依赖文件"""
    print("\n验证依赖文件...")
    
    requirements_file = project_root / "requirements.txt"
    if not requirements_file.exists():
        print("  ❌ requirements.txt 文件缺失")
        return False
    
    try:
        content = requirements_file.read_text()
        required_packages = [
            "binance-futures-connector",
            "python-telegram-bot",
            "python-dotenv",
        ]
        
        missing = []
        for pkg in required_packages:
            if pkg not in content:
                missing.append(pkg)
        
        if missing:
            print(f"  ❌ requirements.txt 缺少必需包: {', '.join(missing)}")
            return False
        
        print("  ✅ requirements.txt 文件完整")
        return True
        
    except Exception as e:
        print(f"  ❌ 读取 requirements.txt 失败: {e}")
        return False


def verify_run_script() -> bool:
    """验证运行脚本"""
    print("\n验证运行脚本...")
    
    run_script = project_root / "run.sh"
    if not run_script.exists():
        print("  ❌ run.sh 文件缺失")
        return False
    
    try:
        content = run_script.read_text()
        if "python main.py" not in content:
            print("  ❌ run.sh 未指向 main.py")
            return False
        
        print("  ✅ run.sh 脚本正确")
        return True
        
    except Exception as e:
        print(f"  ❌ 读取 run.sh 失败: {e}")
        return False


def verify_backward_compatibility() -> bool:
    """验证向后兼容性"""
    print("\n验证向后兼容性...")
    
    # 检查原有的 trader.py 文件
    old_trader = project_root / "trader.py"
    if not old_trader.exists():
        print("  ❌ trader.py 文件缺失 (向后兼容性)")
        return False
    
    # 检查兼容性模块
    compat_module = project_root / "compatibility.py"
    if not compat_module.exists():
        print("  ❌ compatibility.py 文件缺失")
        return False
    
    print("  ✅ 向后兼容性保持")
    return True


def main() -> None:
    """主验证函数"""
    print("=" * 60)
    print("Binance交易机器人 - 重构验证")
    print("=" * 60)
    
    checks = [
        ("文件结构", verify_file_structure),
        ("模块导入", verify_imports),
        ("配置示例", verify_config_example),
        ("依赖文件", verify_requirements),
        ("运行脚本", verify_run_script),
        ("向后兼容", verify_backward_compatibility),
    ]
    
    results = []
    for check_name, check_func in checks:
        print(f"\n{check_name}:")
        try:
            result = check_func()
            results.append((check_name, result))
        except Exception as e:
            print(f"  ❌ 验证失败: {e}")
            results.append((check_name, False))
    
    print("\n" + "=" * 60)
    print("验证结果:")
    print("=" * 60)
    
    all_passed = True
    for check_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{check_name:15} {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 所有验证通过！")
        print("\n下一步操作:")
        print("1. 安装依赖: pip install -r requirements.txt")
        print("2. 配置环境: cp .env.example .env 并填写配置")
        print("3. 环境检查: python init.py")
        print("4. 启动机器人: ./run.sh")
        print("\n如需测试基本功能，运行: python test_basic.py")
    else:
        print("❌ 部分验证失败，请修复问题")
        print("\n常见问题:")
        print("1. 确保所有必需文件都存在")
        print("2. 检查 .env.example 文件格式")
        print("3. 验证 requirements.txt 内容")
        print("4. 确认 run.sh 指向 main.py")
    
    print("=" * 60)


if __name__ == "__main__":
    main()