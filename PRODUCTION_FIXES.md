# 生产环境问题修复总结

**日期：** 2025-11-05
**环境：** 生产环境 (fanxinggemini.zeabur.app)

---

## 📋 问题概览

在部署工具调用功能后，发现了 2 个运行时问题：

1. **应用关闭时的 CancelledError 异常**
2. **无效函数名导致的 400 错误**

---

## 🔧 问题 1: CancelledError 异常

### 错误信息

```python
asyncio.exceptions.CancelledError
  File "/app/src/credential_manager.py", line 108, in _background_worker
    await asyncio.wait_for(self._shutdown_event.wait(), timeout=60.0)
```

### 原因分析

应用关闭时，FastAPI 的 lifespan 会调用 `credential_manager.close()`，设置 shutdown event 并等待后台任务完成。但后台任务中的 `wait()` 操作被取消，导致 `CancelledError` 异常未被捕获。

### 修复方案

**1. 在 `close()` 方法中捕获 CancelledError**

```python
async def close(self):
    if self._write_worker_task:
        try:
            await asyncio.wait_for(self._write_worker_task, timeout=5.0)
        except asyncio.TimeoutError:
            # ... 原有处理 ...
        except asyncio.CancelledError:
            # 任务被取消是正常的关闭流程
            log.debug("Background worker task was cancelled during shutdown")
```

**2. 在 `_background_worker()` 中增强异常处理**

```python
async def _background_worker(self):
    try:
        while not self._shutdown_event.is_set():
            try:
                # ... 工作逻辑 ...
            except asyncio.CancelledError:
                # 任务被取消，正常退出
                log.debug("Background worker cancelled, exiting gracefully")
                break
    except asyncio.CancelledError:
        # 外层捕获取消，确保干净退出
        log.debug("Background worker received cancellation")
    finally:
        log.debug("Background worker exited")
        self._write_worker_running = False
```

### 效果

✅ 应用关闭时不再显示错误堆栈
✅ 后台任务能够优雅退出
✅ 日志更清晰（DEBUG 级别）

---

## 🔧 问题 2: 无效函数名导致 400 错误

### 错误信息

```json
{
  "error": {
    "code": 400,
    "message": "The GenerateContentRequest proto is invalid:\n  * tools[0].function_declarations[0].name: [FIELD_INVALID] Invalid function name. Must start with a letter or an underscore. Must be a-z, A-Z, 0-9, or contain underscores, dots and dashes, with a maximum length of 64.",
    "status": "INVALID_ARGUMENT"
  }
}
```

### 原因分析

用户提供的工具函数名不符合 Gemini API 规范，但我们的代码没有提前验证，导致请求被 Google API 拒绝。

### Gemini API 函数名规则

- ✅ 必须以字母或下划线开头
- ✅ 只能包含 `a-z`, `A-Z`, `0-9`, 下划线, 点, 短横线
- ✅ 最大长度 64 个字符

### 修复方案

**1. 添加验证函数**

```python
def _validate_function_name(name: str) -> bool:
    """验证函数名是否符合 Gemini API 规范"""
    import re

    if not name or len(name) > 64:
        return False

    # 检查首字符必须是字母或下划线
    if not (name[0].isalpha() or name[0] == '_'):
        return False

    # 检查其他字符
    pattern = r'^[a-zA-Z_][a-zA-Z0-9_.\-]*$'
    return bool(re.match(pattern, name))
```

**2. 在转换时验证**

```python
def convert_openai_tools_to_gemini(openai_tools: List) -> List[Dict[str, Any]]:
    # ...
    function_name = function.get("name")
    if not function_name:
        raise ValueError("Function name is required")

    if not _validate_function_name(function_name):
        raise ValueError(
            f"Invalid function name '{function_name}'. "
            f"Function name must start with a letter or underscore, "
            f"contain only a-z, A-Z, 0-9, underscores, dots and dashes, "
            f"and be at most 64 characters long."
        )
```

**3. 添加测试用例**

测试了 8 个无效名称和 7 个有效名称：

**无效示例：**
- ❌ `123start` - 以数字开头
- ❌ `-start` - 以短横线开头
- ❌ `has space` - 包含空格
- ❌ `has@symbol` - 包含非法字符

**有效示例：**
- ✅ `get_weather` - 标准命名
- ✅ `GetWeather` - 驼峰命名
- ✅ `_private_function` - 下划线开头
- ✅ `function.with.dots` - 包含点
- ✅ `function-with-dashes` - 包含短横线

### 效果

✅ 在请求发送前就能发现错误
✅ 提供清晰的错误消息
✅ 避免浪费 API 调用
✅ 改善用户体验

---

## 📊 测试结果

### 所有测试通过 ✅

```
测试 1:  ✅ 工具定义转换
测试 2:  ✅ tool_choice 转换（4 种模式）
测试 3:  ✅ 工具调用提取
测试 4:  ✅ 完整请求转换
测试 5:  ✅ 响应转换（包含工具调用）
测试 6:  ✅ 多轮对话（包含工具结果）
测试 7:  ✅ tool 消息缺少 name 字段
测试 8:  ✅ 无效的 tool_call arguments
测试 9:  ✅ 部分 tool_calls 失败
测试 10: ✅ 无效的函数名验证
```

**总计：10/10 测试用例全部通过** 🎉

---

## 📦 变更文件

### 修复 1: CancelledError 异常
- `src/credential_manager.py`
  - 修改 `close()` 方法
  - 修改 `_background_worker()` 方法
  - +30 行, -16 行

### 修复 2: 函数名验证
- `src/openai_transfer.py`
  - 新增 `_validate_function_name()` 函数
  - 修改 `convert_openai_tools_to_gemini()` 函数
  - +68 行, -1 行

- `test_tool_calling.py`
  - 新增 `test_invalid_function_names()` 测试
  - +59 行

**总计：+157 行, -17 行**

---

## 🚀 部署验证

### 启动测试

✅ 应用正常启动
```
[2025-11-05 05:28:19] [INFO] 启动 GCLI2API
[2025-11-05 05:28:19] [INFO] 控制面板: http://127.0.0.1:7861
[2025-11-05 05:28:19] [INFO] 凭证管理器初始化成功
```

✅ 服务正常运行
```
[2025-11-05 05:36:46] [INFO] Serving desktop control panel
[2025-11-05 05:36:48] [INFO] POST /auth/login 1.1 200
```

### 关闭测试

✅ 应用优雅关闭
- 不再显示 CancelledError 堆栈
- 只有 DEBUG 级别的日志

### 功能测试

✅ 工具调用功能正常
- 有效的函数名正常工作
- 无效的函数名返回清晰错误

---

## 📝 最佳实践总结

### 1. 异步任务关闭处理

**❌ 错误做法：**
```python
async def close(self):
    await self._task  # 可能抛出 CancelledError
```

**✅ 正确做法：**
```python
async def close(self):
    try:
        await asyncio.wait_for(self._task, timeout=5.0)
    except asyncio.TimeoutError:
        self._task.cancel()
    except asyncio.CancelledError:
        # 正常的关闭流程
        pass
```

### 2. 后台工作线程

**✅ 多层异常处理：**
```python
async def worker(self):
    try:
        while not shutdown:
            try:
                # 工作逻辑
            except asyncio.CancelledError:
                break  # 优雅退出
    except asyncio.CancelledError:
        pass  # 外层捕获
    finally:
        # 清理资源
        self.cleanup()
```

### 3. 输入验证

**✅ 提前验证，快速失败：**
```python
def convert(data):
    # 先验证输入
    if not validate(data):
        raise ValueError("Clear error message")

    # 再处理
    return process(data)
```

---

## 🎯 影响评估

### 向后兼容性

✅ **100% 向后兼容**
- 现有功能不受影响
- 只增加了验证，没有改变行为

### 性能影响

✅ **影响可忽略**
- 函数名验证：O(n)，n 为名称长度
- 只在工具定义转换时执行一次
- 正则匹配开销很小

### 用户体验

✅ **显著改善**
- 更清晰的错误消息
- 更早发现问题
- 减少无效的 API 调用

---

## 📚 相关文档

- **技术文档：** `TOOL_CALLING_ANALYSIS.md`
- **使用示例：** `TOOL_CALLING_EXAMPLES.md`
- **代码审查：** `CODE_REVIEW.md`
- **测试代码：** `test_tool_calling.py`

---

## ✅ 检查清单

- [x] 问题已定位
- [x] 修复已实现
- [x] 测试已通过
- [x] 文档已更新
- [x] 代码已推送
- [x] 生产验证通过

---

**修复完成时间：** 2025-11-05
**状态：** ✅ 已部署到生产环境
**测试状态：** 10/10 全部通过
