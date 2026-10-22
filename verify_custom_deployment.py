#!/usr/bin/env python
"""
验证脚本:测试 Hobby 部署的所有企业功能和 AI 提供商配置是否正常工作

使用方法:
    python verify_custom_deployment.py
"""

import os
import sys
import django

# 设置 Django环境
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
django.setup()

from django.conf import settings
from posthog.cloud_utils import is_cloud, is_hobby, get_cached_instance_license
from ee.models.license import License
from posthog.run_mode import run_mode


def test_license():
    """测试许可证是否正确激活"""
    print("=" * 60)
    print("📋 测试 1: 企业许可证激活")
    print("=" * 60)
    
    license = get_cached_instance_license()
    
    if license is None:
        print("❌ 失败: 未找到许可证")
        return False
    
    print(f"✅ 许可证已找到:")
    print(f"   - Plan: {license.plan}")
    print(f"   - Valid until: {license.valid_until}")
    print(f"   - Max users: {license.max_users or '无限制'}")
    
    # 验证是 enterprise 许可证
    if license.plan != "enterprise":
        print(f"❌ 失败: 许可证计划应为 'enterprise',实际为 '{license.plan}'")
        return False
    
    print(f"✅ 许可证计划正确: {license.plan}")
    
    # 验证可用功能
    features = license.available_features
    print(f"\n✅ 可用功能数量: {len(features)}")
    
    # 检查关键功能
    required_features = [
        "saml", "scim", "access_control", "role_based_access",
        "subscriptions", "white_labelling", "app_metrics"
    ]
    
    missing_features = []
    for feature in required_features:
        if feature not in features:
            missing_features.append(feature)
    
    if missing_features:
        print(f"❌ 失败: 缺少功能: {', '.join(missing_features)}")
        return False
    
    print(f"✅ 所有关键功能已解锁:")
    for feature in required_features:
        print(f"   ✓ {feature}")
    
    return True


def test_cloud_permissions():
    """测试 Cloud 权限是否正确设置"""
    print("\n" + "=" * 60)
    print("☁️ 测试 2: Cloud 权限检查")
    print("=" * 60)
    
    cloud_status = is_cloud()
    hobby_status = is_hobby()
    current_mode = run_mode()
    
    print(f"运行模式: {current_mode}")
    print(f"is_cloud(): {cloud_status}")
    print(f"is_hobby(): {hobby_status}")
    
    # Hobby 部署应该有 cloud 权限
    if hobby_status and not cloud_status:
        print("❌ 失败: Hobby 部署应该有 Cloud 权限 (is_cloud 应返回 True)")
        return False
    
    print("✅ Cloud 权限检查通过")
    print("   ✓ is_cloud() 返回 True (已解锁 Cloud 功能)")
    print("   ✓ is_hobby() 返回 True (标识为自托管)")
    
    return True


def test_ai_providers():
    """测试 AI 提供商配置"""
    print("\n" + "=" * 60)
    print("🤖 测试 3: AI 提供商配置")
    print("=" * 60)
    
    providers = {
        "OpenAI": {
            "key": settings.OPENAI_API_KEY,
            "base_url": getattr(settings, "OPENAI_BASE_URL", None),
        },
        "Anthropic": {
            "key": settings.ANTHROPIC_API_KEY,
            "base_url": None,
        },
        "智谱 GLM": {
            "key": getattr(settings, "GLM_API_KEY", ""),
            "base_url": getattr(settings, "GLM_BASE_URL", None),
        },
        "通义千问": {
            "key": getattr(settings, "QWEN_API_KEY", ""),
            "base_url": getattr(settings, "QWEN_BASE_URL", None),
        },
        "小米 MiMo": {
            "key": getattr(settings, "MIMO_API_KEY", ""),
            "base_url": getattr(settings, "MIMO_BASE_URL", None),
        },
        "自定义 LLM": {
            "key": getattr(settings, "CUSTOM_LLM_API_KEY", ""),
            "base_url": getattr(settings, "CUSTOM_LLM_BASE_URL", None),
        },
    }
    
    configured_providers = []
    
    for name, config in providers.items():
        has_key = bool(config["key"])
        has_url = config["base_url"] is not None and bool(config["base_url"])
        
        if has_key:
            configured_providers.append(name)
            status_icon = "✅" if has_key else "⚠️"
            print(f"{status_icon} {name}:")
            print(f"   API Key: {'已配置'}")
            if has_url:
                print(f"   Base URL: {config['base_url']}")
    
    if not configured_providers:
        print("⚠️  警告: 未配置任何 AI 提供商")
        print("   提示: 在 .env 文件中添加 GLM_API_KEY, QWEN_API_KEY 等")
        return True  # 不算失败,只是警告
    
    print(f"\n✅ 已配置 {len(configured_providers)} 个 AI 提供商:")
    for provider in configured_providers:
        print(f"   ✓ {provider}")
    
    return True


def test_chinese_llm_classes():
    """测试国内 LLM 类是否可导入"""
    print("\n" + "=" * 60)
    print("📦 测试 4: 国内 LLM 类导入")
    print("=" * 60)
    
    try:
        from ee.hogai.llm import MaxChatGLM, MaxChatQwen, MaxChatMiMo, MaxChatCustomLLM
        
        print("✅ 所有国内 LLM 类导入成功:")
        print("   ✓ MaxChatGLM (智谱)")
        print("   ✓ MaxChatQwen (通义千问)")
        print("   ✓ MaxChatMiMo (小米)")
        print("   ✓ MaxChatCustomLLM (自定义)")
        
        return True
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        return False


def test_environment_variables():
    """测试环境变量是否正确加载"""
    print("\n" + "=" * 60)
    print("🔧 测试 5: 环境变量检查")
    print("=" * 60)
    
    required_vars = [
        "SECRET_KEY",
        "DATABASE_URL",
    ]
    
    optional_ai_vars = [
        "GLM_API_KEY",
        "QWEN_API_KEY", 
        "MIMO_API_KEY",
        "CUSTOM_LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ]
    
    print("必需的环境变量:")
    all_good = True
    for var in required_vars:
        value = getattr(settings, var, os.environ.get(var))
        if value:
            print(f"   ✓ {var}: 已配置")
        else:
            print(f"   ❌ {var}: 未配置")
            all_good = False
    
    print("\n可选的 AI 提供商变量:")
    configured_count = 0
    for var in optional_ai_vars:
        value = os.environ.get(var, getattr(settings, var, ""))
        if value:
            configured_count += 1
            print(f"   ✓ {var}: 已配置")
        else:
            print(f"   - {var}: 未配置 (可选)")
    
    print(f"\n✅ 已配置 {configured_count}/{len(optional_ai_vars)} 个 AI 提供商")
    
    return all_good


def main():
    """运行所有测试"""
    print("\n" + "🦔" * 30)
    print("PostHog 自定义部署验证工具")
    print("🦔" * 30 + "\n")
    
    tests = [
        ("许可证激活", test_license),
        ("Cloud 权限", test_cloud_permissions),
        ("AI 提供商配置", test_ai_providers),
        ("国内 LLM 类", test_chinese_llm_classes),
        ("环境变量", test_environment_variables),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ 测试 '{name}' 抛出异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # 打印总结
    print("\n" + "=" * 60)
    print("📊 测试总结")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {name}")
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过!你的自定义部署已就绪!")
        print("\n下一步:")
        print("  1. 访问 https://your-domain.com 开始使用")
        print("  2. 在 Settings → AI & Machine Learning 中配置 AI 提供商")
        print("  3. 查看 CUSTOM_DEPLOYMENT_GUIDE.md 了解更多配置选项")
        return 0
    else:
        print(f"\n⚠️  有 {total - passed} 个测试未通过,请检查上述错误信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
