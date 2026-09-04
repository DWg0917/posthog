#!/usr/bin/env python
"""
超轻量验证脚本 - 不依赖任何第三方库
只检查文件内容,不导入模块
"""

import sys
import os
import re

def check_file_contains(filepath, patterns, description):
    """检查文件是否包含指定模式"""
    print(f"\n检查: {description}")
    print(f"  文件: {filepath}")
    
    if not os.path.exists(filepath):
        print(f"  ❌ 文件不存在")
        return False
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        all_ok = True
        for pattern_name, pattern in patterns.items():
            if re.search(pattern, content):
                print(f"  ✅ {pattern_name}")
            else:
                print(f"  ❌ 未找到: {pattern_name}")
                all_ok = False
        
        return all_ok
    except Exception as e:
        print(f"  ❌ 读取失败: {e}")
        return False

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("=" * 70)
    print("🚀 PostHog 自定义部署验证 (轻量版)")
    print("=" * 70)
    
    results = []
    
    # 测试 1: run_mode.py - is_cloud 属性
    results.append(check_file_contains(
        os.path.join(base_dir, 'posthog', 'run_mode.py'),
        {
            'HOBBY 模式': r'is_hobby',
            'is_cloud 返回 HOBBY': r'RunMode\.HOBBY',
        },
        "测试 1: RunMode.is_cloud 是否包含 HOBBY"
    ))
    
    # 测试 2: cloud_utils.py - 许可证自动激活
    results.append(check_file_contains(
        os.path.join(base_dir, 'posthog', 'cloud_utils.py'),
        {
            'is_hobby 检查': r'is_hobby\(\)',
            'enterprise plan': r'plan=["\']enterprise["\']',
            'max_users=None': r'max_users=None',
        },
        "测试 2: cloud_utils.py 许可证自动激活"
    ))
    
    # 测试 3: ee/settings.py - 国内 AI 配置
    results.append(check_file_contains(
        os.path.join(base_dir, 'ee', 'settings.py'),
        {
            'GLM_API_KEY': r'GLM_API_KEY',
            'GLM_BASE_URL': r'GLM_BASE_URL',
            'QWEN_API_KEY': r'QWEN_API_KEY',
            'QWEN_BASE_URL': r'QWEN_BASE_URL',
            'MIMO_API_KEY': r'MIMO_API_KEY',
            'MIMO_BASE_URL': r'MIMO_BASE_URL',
            'CUSTOM_LLM_API_KEY': r'CUSTOM_LLM_API_KEY',
            'CUSTOM_LLM_BASE_URL': r'CUSTOM_LLM_BASE_URL',
        },
        "测试 3: ee/settings.py 国内 AI 配置"
    ))
    
    # 测试 4: ee/hogai/llm.py - 国内 LLM 类
    results.append(check_file_contains(
        os.path.join(base_dir, 'ee', 'hogai', 'llm.py'),
        {
            'MaxChatGLM 类': r'class MaxChatGLM',
            'MaxChatQwen 类': r'class MaxChatQwen',
            'MaxChatMiMo 类': r'class MaxChatMiMo',
            'MaxChatCustomLLM 类': r'class MaxChatCustomLLM',
            'GLM endpoint': r'GLM_BASE_URL',
            'Qwen endpoint': r'QWEN_BASE_URL',
            'MiMo endpoint': r'MIMO_BASE_URL',
        },
        "测试 4: ee/hogai/llm.py 国内 LLM 类"
    ))
    
    # 测试 5: docker-compose.hobby.yml - AI 环境变量
    results.append(check_file_contains(
        os.path.join(base_dir, 'docker-compose.hobby.yml'),
        {
            'capture-ai 服务': r'capture-ai:',
            'worker GLM_API_KEY': r'GLM_API_KEY',
            'worker QWEN_API_KEY': r'QWEN_API_KEY',
            'worker MIMO_API_KEY': r'MIMO_API_KEY',
            'worker CUSTOM_LLM_API_KEY': r'CUSTOM_LLM_API_KEY',
        },
        "测试 5: docker-compose.hobby.yml AI 环境变量"
    ))
    
    # 汇总
    print("\n" + "=" * 70)
    print("📊 测试结果汇总")
    print("=" * 70)
    
    test_names = [
        "RunMode.is_cloud",
        "cloud_utils.py 许可证",
        "ee/settings.py AI配置",
        "ee/hogai/llm.py LLM类",
        "docker-compose AI环境变量"
    ]
    
    for i, (name, passed) in enumerate(zip(test_names, results), 1):
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {i}. {name}: {status}")
    
    total = len(results)
    passed = sum(1 for p in results if p)
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过!代码改动已就绪。")
        print("\n下一步:")
        print("  1. 配置 .env 文件 (参考 .env.custom.example)")
        print("  2. 使用独立 project 启动测试:")
        print("     docker-compose -f docker-compose.hobby.yml -p posthog-custom up -d")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
        print("   请检查上面标记为 ❌ 的项")
        return 1

if __name__ == "__main__":
    sys.exit(main())
