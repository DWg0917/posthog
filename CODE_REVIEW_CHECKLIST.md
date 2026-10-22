# PostHog 自定义部署 - 代码审查清单

## 🔍 推送前检查 (已完成 ✅)

### 1. 核心功能验证

- [x] `posthog/run_mode.py` - HOBBY 模式返回 is_cloud=true
- [x] `posthog/cloud_utils.py` - 自动创建 enterprise 许可证
- [x] `ee/settings.py` - 添加国内 AI 环境变量
- [x] `ee/hogai/llm.py` - 实现 4 个国内 LLM 类
- [x] `docker-compose.hobby.yml` - AI 环境变量 + capture-ai 服务

### 2. 快速验证结果

```
✅ RunMode.is_cloud: 通过
✅ cloud_utils.py 许可证: 通过
✅ ee/settings.py AI配置: 通过
✅ ee/hogai/llm.py LLM类: 通过
✅ docker-compose AI环境变量: 通过
```

---

## 📝 云服务器验证步骤

### 步骤 1: 下载代码

```bash
# 克隆你的 fork
git clone https://github.com/DWg0917/posthog.git
cd posthog
git checkout feat/custom-posthog
```

### 步骤 2: 检查改动的文件

```bash
# 查看改动统计
git diff main..feat/custom-posthog --stat

# 应该看到:
# docker-compose.hobby.yml  | 修改
# ee/hogai/llm.py          | 修改
# ee/settings.py           | 修改
# posthog/cloud_utils.py   | 修改
# posthog/run_mode.py      | 修改
```

### 步骤 3: 运行验证脚本

```bash
python quick_verify.py
```

**预期输出**: 5/5 全部通过

### 步骤 4: 人工审查关键文件

#### 4.1 检查 run_mode.py

```bash
# 查看 is_cloud 属性
grep -A 5 "def is_cloud" posthog/run_mode.py
```

**应该看到**:
```python
def is_cloud(self) -> bool:
    return self.is_deployed_cloud or self is RunMode.E2E or self is RunMode.HOBBY
```

#### 4.2 检查 cloud_utils.py

```bash
# 查看许可证创建逻辑
grep -A 10 "is_hobby()" posthog/cloud_utils.py
```

**应该看到**:
```python
if not license and (is_dev_mode() or is_hobby()):
    license = License.objects.create(
        key=f"{dev_uuid}::{settings.LICENSE_SECRET_KEY}",
        plan="enterprise",
        valid_until=timezone.now() + timedelta(weeks=52 * 100),
        max_users=None,
    )
```

#### 4.3 检查 AI 配置

```bash
# 查看 ee/settings.py
grep -E "GLM|QWEN|MIMO|CUSTOM_LLM" ee/settings.py | head -20
```

**应该看到 12+ 行配置** (API_KEY, BASE_URL, SUPPORTED_MODELS)

#### 4.4 检查 LLM 类实现

```bash
# 查看新增的 LLM 类
grep "^class MaxChat" ee/hogai/llm.py
```

**应该看到**:
```
class MaxChatGLM
class MaxChatQwen
class MaxChatMiMo
class MaxChatCustomLLM
```

### 步骤 5: 检查 Python 语法

```bash
# 检查语法错误
python -m py_compile posthog/run_mode.py
python -m py_compile posthog/cloud_utils.py
python -m py_compile ee/settings.py
python -m py_compile ee/hogai/llm.py

# 如果没有输出,说明语法正确
```

### 步骤 6: 检查 Docker Compose

```bash
# 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('docker-compose.hobby.yml'))"

# 检查 AI 环境变量
grep -E "GLM_API_KEY|QWEN_API_KEY|MIMO_API_KEY|CUSTOM_LLM" docker-compose.hobby.yml | wc -l
# 应该输出 12+ (每个服务都有配置)

# 检查 capture-ai 服务
grep "capture-ai:" docker-compose.hobby.yml
```

---

## ⚠️ 常见问题

### Q1: 代码改动会不会影响 PostHog Cloud?

**不会**。改动有明确的隔离:

- `is_cloud` 属性对 HOBBY 模式返回 true,但对 CLOUD_US/CLOUD_EU 没有影响
- 许可证创建只在 `is_dev_mode() or is_hobby()` 时触发
- AI 环境变量是**新增**的,不影响现有配置

### Q2: 国内 AI 提供商的实现是否完整?

**完整**。实现方式:

1. 使用 OpenAI 兼容 API 标准
2. 继承 `ChatOpenAI` 基类 (LangChain 提供)
3. 在 `model_post_init` 中自动设置正确的 endpoint
4. 与现有 `MaxChatOpenAI`, `MaxChatAnthropic` 实现模式一致

### Q3: 需要迁移数据库吗?

**不需要**。这次改动:
- 没有新增模型 (models)
- 没有修改现有表结构
- 只是应用层逻辑改动

### Q4: 性能会受影响吗?

**不会**。改动都是:
- 配置级别 (环境变量)
- 逻辑判断 (is_cloud 返回值)
- 不增加额外的数据库查询或网络请求

---

## 🎯 确认清单

在云服务器上完成以下检查后,就可以构建镜像:

- [ ] 代码下载成功 (`git checkout feat/custom-posthog`)
- [ ] 验证脚本通过 (`python quick_verify.py` → 5/5)
- [ ] 4 个核心文件改动符合预期
- [ ] Python 语法检查通过 (无报错)
- [ ] Docker Compose YAML 语法正确
- [ ] 审查了关键代码逻辑

---

## 🚀 构建镜像 (验证通过后)

```bash
# 1. 安装 Docker (如果还没有)
curl -fsSL https://get.docker.com | sh

# 2. 登录 Docker Hub
docker login

# 3. 构建镜像
docker build \
    -t your-username/posthog-custom:latest \
    -f Dockerfile \
    --target posthog \
    --build-arg COMMIT_HASH=$(git rev-parse HEAD) \
    .

# 4. 推送镜像
docker push your-username/posthog-custom:latest
```

---

## 📚 相关文档

- [DEPLOYMENT_STEPS.md](./DEPLOYMENT_STEPS.md) - 完整部署指南
- [CUSTOM_DEPLOYMENT_GUIDE.md](./CUSTOM_DEPLOYMENT_GUIDE.md) - 配置指南
- [CHANGES_SUMMARY.md](./CHANGES_SUMMARY.md) - 改动总结
