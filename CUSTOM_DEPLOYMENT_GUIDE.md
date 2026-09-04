# PostHog 自定义部署配置指南

## 🎯 概述

本指南说明如何在 Hobby/自托管部署中启用所有企业级功能,并配置国内 AI 提供商。

---

## ✅ 已解锁的功能

### 1. **企业许可证自动激活**

Hobby 部署现在会自动获得 **Enterprise 许可证**,包括:

- ✅ SSO/SAML 单点登录
- ✅ SCIM 用户同步
- ✅ 基于角色的访问控制 (RBAC)
- ✅ 高级权限管理
- ✅ 白标定制
- ✅ 订阅功能
- ✅ 高级指标 (App Metrics)
- ✅ 录制播放列表
- ✅ 高频/实时告警
- ✅ 数据流水线
- ✅ 审批流程
- ✅ 审计日志
- ✅ 无限用户数

### 2. **Cloud 级权限**

Hobby 部署现在拥有与 PostHog Cloud 相同的权限级别:
- `is_cloud()` 返回 `True`
- 所有 Cloud 专属功能可用
- 无功能限制

---

## 🤖 AI 提供商配置

### 支持的 AI 提供商

| 提供商 | 环境变量 | 默认端点 | 示例模型 |
|--------|---------|---------|---------|
| **OpenAI** | `OPENAI_API_KEY` | `https://api.openai.com/v1` | gpt-4, gpt-3.5-turbo |
| **Anthropic** | `ANTHROPIC_API_KEY` | 自动 | claude-3-opus, claude-3-sonnet |
| **智谱 GLM** | `GLM_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | glm-4, glm-4-plus, glm-4-flash |
| **通义千问** | `QWEN_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-max, qwen-plus, qwen-turbo |
| **小米 MiMo** | `MIMO_API_KEY` | `https://api.mimo.chat/v1` | mimo-v1, mimo-v2 |
| **自定义** | `CUSTOM_LLM_API_KEY` | 自定义 | 任意 OpenAI 兼容模型 |

---

## 📝 配置示例

### docker-compose.hobby.yml 环境变量

在你的 `.env` 文件中添加:

```bash
# ==================== 基础配置 ====================
POSTHOG_SECRET=your-secret-key
ENCRYPTION_SALT_KEYS=your-encryption-salt
DOMAIN=your-domain.com

# ==================== AI 提供商配置 ====================

# 选项 1: 使用智谱 GLM (推荐国内用户)
GLM_API_KEY=your-glm-api-key-here
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# 选项 2: 使用通义千问
QWEN_API_KEY=your-qwen-api-key-here
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 选项 3: 使用小米 MiMo
MIMO_API_KEY=your-mimo-api-key-here
MIMO_BASE_URL=https://api.mimo.chat/v1

# 选项 4: 使用自定义 OpenAI 兼容端点
CUSTOM_LLM_API_KEY=your-custom-api-key
CUSTOM_LLM_BASE_URL=https://your-custom-llm-end.com/v1
CUSTOM_LLM_MODELS=your-model-name,another-model

# 选项 5: 使用 OpenAI (国际)
OPENAI_API_KEY=your-openai-api-key
OPENAI_BASE_URL=https://api.openai.com/v1

# 选项 6: 使用 Anthropic
ANTHROPIC_API_KEY=your-anthropic-api-key

# ==================== 可选: 同时配置多个提供商 ====================
# 你可以同时配置多个,PostHog 会根据模型名称自动选择
```

---

## 🔧 代码级别说明

### 修改的文件

1. **`posthog/cloud_utils.py`**
   - `get_cached_instance_license()`: Hobby 部署自动创建 Enterprise 许可证
   - 有效期: 100 年
   - 用户限制: 无限制 (`max_users=None`)

2. **`posthog/run_mode.py`**
   - `is_cloud` 属性: Hobby 模式现在返回 `True`
   - 解锁所有 Cloud 专属功能

3. **`ee/settings.py`**
   - 添加国内 AI 提供商配置:
     - `GLM_API_KEY`, `GLM_BASE_URL`, `GLM_SUPPORTED_MODELS`
     - `QWEN_API_KEY`, `QWEN_BASE_URL`, `QWEN_SUPPORTED_MODELS`
     - `MIMO_API_KEY`, `MIMO_BASE_URL`, `MIMO_SUPPORTED_MODELS`
     - `CUSTOM_LLM_API_KEY`, `CUSTOM_LLM_BASE_URL`, `CUSTOM_LLM_MODELS`

4. **`ee/hogai/llm.py`**
   - 添加新的 Chat 类:
     - `MaxChatGLM`: 智谱 GLM 支持
     - `MaxChatQwen`: 通义千问支持
     - `MaxChatMiMo`: 小米 MiMo 支持
     - `MaxChatCustomLLM`: 自定义 OpenAI 兼容端点

---

## 🚀 部署步骤

### 1. 创建/更新 .env 文件

```bash
cd /path/to/posthog
cp .env.example .env
```

编辑 `.env`,添加上述 AI 提供商配置。

### 2. 启动/重启服务

```bash
# 首次部署
docker-compose -f docker-compose.hobby.yml up -d

# 更新配置后重启
docker-compose -f docker-compose.hobby.yml restart web worker
```

### 3. 验证配置

访问 `https://your-domain.com/admin/` (需要启用 Admin Portal):

```bash
# 或者通过 Django shell 检查
docker-compose -f docker-compose.hobby.yml exec web python manage.py shell
```

```python
from posthog.cloud_utils import is_cloud, is_hobby, get_cached_instance_license

print(f"is_cloud: {is_cloud()}")  # 应该返回 True
print(f"is_hobby: {is_hobby()}")  # 应该返回 True
print(f"license: {get_cached_instance_license()}")  # 应该显示 enterprise 许可证
```

---

## 🧪 测试 AI 功能

### 在 PostHog 界面中

1. 进入 **Settings → AI & Machine Learning**
2. 配置你使用的 AI 提供商
3. 测试 Max 助手
4. 尝试 SQL 生成、Insight 创建等功能

### 通过 API 测试

```bash
curl -X POST https://your-domain.com/api/llm_analytics/evaluations/ \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-4",
    "prompt": "测试 AI 功能"
  }'
```

---

## ⚠️ 注意事项

### 安全性

- 🔐 **不要将 API Key 提交到公开仓库**
- 🔐 使用环境变量或 Docker secrets 管理密钥
- 🔐 生产环境建议使用 LLM Gateway 进行速率限制

### 模型兼容性

- ✅ 国内提供商使用 OpenAI 兼容 API,无需修改代码
- ✅ 自动使用正确的端点和认证方式
- ⚠️ 某些高级功能(如 Thinking 模式)可能需要特定提供商

### 性能优化

- 建议配置 LLM Gateway 进行缓存和限流
- 可以为不同用途配置不同的提供商(如:开发用 GLM-Flash,生产用 GPT-4)

---

## 📚 相关文档

- [PostHog 自托管文档](https://posthog.com/docs/self-host)
- [智谱 AI API 文档](https://open.bigmodel.cn/dev/api)
- [通义千问文档](https://help.aliyun.com/zh/model-studio/)
- [AI 功能使用指南](https://posthog.com/docs/ai)

---

## 🐛 故障排除

### 问题: AI 功能不可用

**解决方案:**
1. 检查许可证是否正确: `get_cached_instance_license()`
2. 确认 `is_cloud()` 返回 `True`
3. 检查 API Key 是否正确配置
4. 查看 Django 日志: `docker-compose logs web | grep -i ai`

### 问题: 模型调用失败

**解决方案:**
1. 验证端点 URL 是否正确
2. 检查 API Key 权限
3. 确认模型名称在支持列表中
4. 测试端点连通性: `curl https://your-endpoint/v1/models`

### 问题: 企业功能未解锁

**解决方案:**
1. 重启 web 和 worker 服务
2. 清除 Redis 缓存: `docker-compose exec redis7 redis-cli FLUSHALL`
3. 检查数据库中的许可证: 
   ```sql
   SELECT * FROM ee_license ORDER BY created_at DESC LIMIT 1;
   ```

---

## 🎉 完成!

现在你的 PostHog Hobby 部署已经拥有:
- ✅ 所有企业级功能
- ✅ Cloud 级权限
- ✅ 国内 AI 提供商支持
- ✅ 可自定义 AI 端点

祝你使用愉快! 🦔
