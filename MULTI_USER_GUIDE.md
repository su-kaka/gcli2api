# gcli2api 多用户隔离使用指南

## 概述

gcli2api 现在支持多用户隔离功能，允许管理员创建多个独立的用户账户，每个用户拥有独立的凭证池和使用统计。

## 功能特性

### 1. 用户隔离
- 每个用户拥有独立的 Google 凭证池
- 用户之间的凭证完全隔离，互不干扰
- 每个用户独立的凭证轮换逻辑
- 独立的使用统计和监控

### 2. 管理功能
- **用户管理**: 创建、删除、启用/禁用用户
- **凭证管理**: 用户可以上传和管理自己的凭证
- **使用统计**: 查看每个用户的调用次数和凭证状态
- **安全认证**: 基于密钥的认证系统

## 使用方法

### 管理员功能 (/admin)

访问管理员控制台: `http://127.0.0.1:8000/admin`

#### 1. 登录
使用管理员密码（即 API_PASSWORD）登录

#### 2. 创建用户
- 输入用户名和描述（可选）
- 点击"创建用户"
- **重要**: 保存生成的用户密钥（格式: `musr_xxxxx...`），此密钥只显示一次

#### 3. 管理用户
- **查看统计**: 查看用户的凭证数量、总调用次数等
- **禁用/启用**: 临时禁用或重新启用用户
- **删除用户**: 删除用户及其所有凭证（不可恢复）

### 用户功能 (/user)

访问用户管理面板: `http://127.0.0.1:8000/user`

#### 1. 登录
使用管理员分配的用户密钥登录

#### 2. 上传凭证
- 输入凭证名称（自定义，便于识别）
- 选择凭证 JSON 文件
- 点击"上传凭证"

#### 3. 管理凭证
- 查看所有已上传的凭证
- 查看凭证状态（正常/已禁用）
- 删除不需要的凭证

### API 调用

用户使用自己的密钥调用 API，系统会自动使用该用户的凭证池。

#### OpenAI 格式 API

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer musr_your_user_key_here" \
  -d '{
    "model": "gemini-2.0-flash-exp",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

#### Gemini 格式 API

```bash
# 使用 URL 参数
curl "http://127.0.0.1:8000/v1beta/models/gemini-2.0-flash-exp:generateContent?key=musr_your_user_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello!"}]}]
  }'

# 或使用 x-goog-api-key 头部
curl "http://127.0.0.1:8000/v1beta/models/gemini-2.0-flash-exp:generateContent" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: musr_your_user_key_here" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello!"}]}]
  }'
```

## API 端点说明

### 管理员 API

所有管理员 API 需要使用管理员密码认证（`Authorization: Bearer <admin_password>`）

- `POST /admin/users` - 创建新用户
- `GET /admin/users` - 列出所有用户
- `GET /admin/users/{username}` - 获取用户详细信息
- `PATCH /admin/users/{username}` - 更新用户信息（禁用/启用）
- `DELETE /admin/users/{username}` - 删除用户
- `GET /admin/users/{username}/stats` - 获取用户使用统计

### 用户 API

所有用户 API 需要使用用户密钥认证（`Authorization: Bearer <user_key>`）

- `GET /user/info` - 获取当前用户信息
- `GET /user/credentials` - 列出当前用户的所有凭证
- `POST /user/credentials/upload` - 上传凭证文件
- `DELETE /user/credentials/{credential_name}` - 删除凭证

## 技术实现

### 凭证命名规则

用户凭证在系统中存储时会自动添加前缀：
```
user_{username}_{credential_name}
```

例如：用户 `alice` 上传了名为 `cred1` 的凭证，实际存储为 `user_alice_cred1`

### 凭证隔离机制

1. **独立的凭证管理器**: 每个用户拥有独立的 `UserCredentialManager` 实例
2. **凭证过滤**: 自动过滤出属于特定用户的凭证（基于前缀匹配）
3. **独立轮换**: 每个用户的凭证池独立轮换，互不影响
4. **使用统计**: 记录每个用户的 API 调用次数和最后活跃时间

### 凭证数据结构

每个凭证在存储时会自动添加 `user_id` 字段：

```json
{
  "user_id": "alice",
  "type": "authorized_user",
  "client_id": "...",
  "client_secret": "...",
  "refresh_token": "...",
  ...
}
```

## 兼容性说明

- **向后兼容**: 原有的全局凭证管理系统仍然可用
- **混合使用**: 可以同时使用全局凭证和多用户凭证
- **认证优先级**:
  1. API Key 认证
  2. 多用户密钥认证（`musr_` 前缀）
  3. 管理员密码认证

## 安全建议

1. **妥善保管用户密钥**: 用户密钥只在创建时显示一次，请妥善保管
2. **定期审查用户**: 定期检查用户列表，删除不再使用的账户
3. **监控使用情况**: 通过统计功能监控异常使用行为
4. **及时禁用**: 发现异常时及时禁用相关用户

## 常见问题

### Q: 用户密钥丢失了怎么办？
A: 用户密钥无法找回，只能删除旧用户并创建新用户。

### Q: 可以修改用户密钥吗？
A: 目前不支持修改用户密钥，需要重新创建用户。

### Q: 用户凭证数量有限制吗？
A: 没有硬性限制，但建议每个用户不超过 20 个凭证以保证性能。

### Q: 如何迁移现有凭证到多用户系统？
A:
1. 创建新用户
2. 使用用户管理界面上传凭证
3. 或使用 API 批量导入凭证

### Q: 多用户模式会影响性能吗？
A: 性能影响很小。每个用户的凭证管理器独立缓存，不会相互影响。

## 示例场景

### 场景1: 团队使用

一个团队有 3 个成员，管理员为每个成员创建账户：

1. 管理员创建用户: `alice`, `bob`, `charlie`
2. 每个成员获得自己的用户密钥
3. 每个成员上传自己的 Google 凭证
4. 团队成员使用各自的密钥调用 API
5. 管理员可以查看每个成员的使用统计

### 场景2: 多项目隔离

一个组织有多个项目，需要分别管理凭证：

1. 为每个项目创建用户: `project_a`, `project_b`, `project_c`
2. 每个项目上传独立的凭证池
3. 项目之间的凭证完全隔离，互不影响
4. 可以单独禁用某个项目而不影响其他项目

## 更新日志

- **v1.0.0** (当前版本)
  - 首次发布多用户隔离功能
  - 支持用户创建、管理和删除
  - 支持独立的凭证池和轮换
  - 提供 Web 管理界面
  - 支持使用统计和监控

## 反馈与支持

如有问题或建议，请提交 Issue 到项目仓库。
