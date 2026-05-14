"""
初始化脚本
验证环境并检查依赖
"""
import os
import sys
import ssl
import socket
from pathlib import Path


def check_python_version() -> bool:
    """检查Python版本"""
    required = (3, 10)
    current = sys.version_info[:2]
    
    if current < required:
        print(f"❌ Python版本过低: {current[0]}.{current[1]}")
        print(f"   需要Python {required[0]}.{required[1]}或更高版本")
        return False
    
    print(f"✅ Python版本: {current[0]}.{current[1]}")
    return True


def check_ssl_certificates() -> bool:
    """检查SSL证书"""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(('fapi.binance.com', 443), timeout=8) as s:
            with ctx.wrap_socket(s, server_hostname='fapi.binance.com') as ss:
                cert = ss.getpeercert()
                issuer = dict(x[0] for x in cert['issuer']).get('commonName', '')
                
                if 'GeoTrust' in issuer or 'DigiCert' in issuer:
                    print(f"✅ SSL证书验证通过: {issuer}")
                    return True
                else:
                    print(f"⚠️  未知证书颁发机构: {issuer}")
                    return True  # 仍然返回True，只是警告
                    
    except ssl.SSLCertVerificationError as e:
        print(f"❌ SSL证书验证失败: {e}")
        print("   可能被杀毒软件MITM，请检查AVG、Avast、Kaspersky等")
        return False
    except Exception as e:
        print(f"❌ SSL连接失败: {e}")
        return False


def check_network_connectivity() -> bool:
    """检查网络连接"""
    endpoints = [
        ('api.binance.com', 443),
        ('api.telegram.org', 443),
    ]
    
    all_ok = True
    for host, port in endpoints:
        try:
            with socket.create_connection((host, port), timeout=5):
                print(f"✅ 网络连接: {host}:{port}")
        except Exception as e:
            print(f"❌ 网络连接失败: {host}:{port} - {e}")
            all_ok = False
    
    return all_ok


def check_env_file() -> bool:
    """检查环境文件"""
    env_file = Path(__file__).parent / ".env"
    
    if not env_file.exists():
        print("❌ 未找到.env文件")
        print("   请复制.env.example为.env并填写配置")
        return False
    
    # 检查必需的环境变量
    required_vars = [
        "TELEGRAM_BOT_TOKEN",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
    ]
    
    missing = []
    with open(env_file, 'r') as f:
        content = f.read()
    
    for var in required_vars:
        if f"{var}=" not in content:
            missing.append(var)
    
    if missing:
        print(f"❌ .env文件中缺少必需变量: {', '.join(missing)}")
        return False
    
    print("✅ .env文件检查通过")
    return True


def check_dependencies() -> bool:
    """检查依赖"""
    try:
        import binance
        import telegram
        import dotenv
        
        print("✅ 依赖检查通过")
        return True
    except ImportError as e:
        print(f"❌ 依赖缺失: {e}")
        print("   请运行: pip install -r requirements.txt")
        return False


def check_venv() -> bool:
    """检查虚拟环境"""
    venv_path = Path(__file__).parent / "venv"
    
    if not venv_path.exists():
        print("⚠️  未找到虚拟环境")
        print("   建议创建虚拟环境: python -m venv venv")
        return True  # 不是致命错误
    
    # 检查是否在虚拟环境中
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("✅ 在虚拟环境中运行")
    else:
        print("⚠️  未在虚拟环境中运行")
        print("   建议激活虚拟环境: source venv/bin/activate")
    
    return True


def main() -> None:
    """主函数"""
    print("=" * 60)
    print("Binance交易机器人 - 环境检查")
    print("=" * 60)
    
    checks = [
        ("Python版本", check_python_version),
        ("虚拟环境", check_venv),
        ("依赖", check_dependencies),
        ("环境文件", check_env_file),
        ("SSL证书", check_ssl_certificates),
        ("网络连接", check_network_connectivity),
    ]
    
    results = []
    for name, check_func in checks:
        print(f"\n检查: {name}")
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ 检查失败: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("检查结果:")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name:20} {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 所有检查通过，可以启动机器人")
        print("   启动命令: ./run.sh")
    else:
        print("❌ 部分检查失败，请修复问题后再启动")
        print("   参考DEPLOY.md获取帮助")
    
    print("=" * 60)


if __name__ == "__main__":
    main()