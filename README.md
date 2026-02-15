# Qwen API Server

> OpenAI/Anthropic 兼容的 Qwen AI API 代理服务，支持多账号管理、智能负载均衡和高级请求调度

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-00a393)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Vercel](https://img.shields.io/badge/Vercel-Ready-black)](https://vercel.com)

---

## 📋 目录

- [🎯 项目简介](#-项目简介)
- [✨ 功能特性](#-功能特性)
- [🚀 快速开始](#-快速开始)
- [📦 安装指南](#-安装指南)
- [💻 使用说明](#-使用说明)
- [🏗️ 项目结构](#-项目结构)
- [⚙️ 配置说明](#-配置说明)
- [🔌 API 文档](#-api-文档)
- [☁️ 部署指南](#-部署指南)
- [❓ 常见问题](#-常见问题)
- [📜 许可证](#-许可证)

---

## 🎯 项目简介

Qwen API Server 是一个高性能的 API 代理服务，将 Qwen AI 的能力以 OpenAI 和 Anthropic 兼容的格式暴露出来。项目采用先进的 **Track-and-Stop 算法**（基于 Thompson Sampling 的多臂赌博机算法）实现智能账号选择和负载均衡，确保请求被分配到性能最优的账号。

### 核心能力

| 能力 | 描述 |
|------|------|
| **OpenAI 兼容** | 支持 `/v1/chat/completions`、`/v1/models` 等标准端点 |
| **Anthropic 兼容** | 支持 `/v1/messages`、Files API、Batch API 等 |
| **多账号管理** | 智能账号池，自动故障转移和性能追踪 |
| **请求调度** | FIFO 公平调度器，防止后端过载 |
| **函数调用** | 支持 Nous XML 格式的函数调用 |
| **多媒体支持** | 图像生成、视频生成、TTS、语音识别 |

### 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | FastAPI 0.104+ |
| 验證 | Pydantic v2 |
| 並發 | asyncio, aiohttp |
| 算法 | Thompson Sampling (Track-and-Stop) |
| 部署 | Vercel Serverless |

---

## ✨ 功能特性

### 核心功能
- ✅ **OpenAI 兼容 API** - 完全兼容 OpenAI API 格式的聊天补全接口
- ✅ **Anthropic 兼容 API** - 支持 Claude 风格的 Messages API
- ✅ **智能账号选择** - 基于 Thompson Sampling 的最优账号选择算法
- ✅ **公平请求调度** - FIFO 调度器，防止单个用户占用过多资源
- ✅ **流式响应** - Server-Sent Events 实时返回生成内容
- ✅ **函数调用** - 支持 XML 格式的函数调用和工具使用

### 高级功能
- 🔧 **多模态支持** - 支持图像、视频、音频的理解和生成
- 🔧 **深度研究** - 支持 Qwen 深度研究模式
- 🔧 **Artifacts** - 支持代码生成和 Web 开发模式
- 🔧 **Batch API** - 支持批量请求处理
- 🔧 **文件管理** - 支持文件上传和管理（Anthropic 兼容）

### 部署特性
- 🚀 **Vercel Serverless** - 一键部署到 Vercel
- 🚀 **本地开发** - 完整的本地开发环境支持
- 🚀 **独立部署** - 支持编译为独立可执行文件

---

## 🚀 快速开始

### 环境要求
- Python >= 3.11
- pip >= 23.0
- Git >= 2.40

### 30 秒快速体验

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/qwen-server.git
cd qwen-server

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置账号（必须）
# 创建 qwen_accounts.py 文件，填入你的 Qwen 账号信息
# 然后编辑 qwen_accounts.py，填入你的真实账号信息
# ⚠️ 注意：qwen_accounts.py 包含敏感信息，请勿提交到 Git！

# 4. 启动服务
python qwen_server.py
```

### 验证安装

```bash
# 测试服务状态
curl http://localhost:1325/v1/models

# 测试聊天接口
curl -X POST http://localhost:1325/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-coder-plus",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 📦 安装指南

### 方式一：本地安装

```bash
# 1. 克隆仓库
git clone https://github.com/yourusername/qwen-server.git
cd qwen-server

# 2. 创建虚拟环境（推荐）
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置账号
# 创建 qwen_accounts.py 文件，格式如下：
ACCOUNTS = [
    {"email": "user1@example.com", "password": "password1"},
    {"email": "user2@example.com", "password": "password2"},
]

# 5. 启动服务
python qwen_server.py
```

### 方式二：Vercel 部署

```bash
# 1. 安装 Vercel CLI
npm i -g vercel

# 2. 登录 Vercel
vercel login

# 3. 部署
vercel --prod
```

**注意事项：**
- 在 Vercel Dashboard 中设置环境变量
- 账号配置通过环境变量或 Vercel Secrets 管理

---

## 💻 使用说明

### OpenAI 兼容接口

```python
import openai

# 配置客户端
client = openai.OpenAI(
    base_url="http://localhost:1325/v1",
    api_key="dummy-key"  # 任意值即可
)

# 非流式聊天
response = client.chat.completions.create(
    model="qwen3-coder-plus",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ]
)
print(response.choices[0].message.content)

# 流式聊天
stream = client.chat.completions.create(
    model="qwen3-coder-plus",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### 函数调用

```python
# 定义工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称"
                    }
                },
                "required": ["city"]
            }
        }
    }
]

# 调用
response = client.chat.completions.create(
    model="qwen3-coder-plus",
    messages=[{"role": "user", "content": "北京今天天气怎么样？"}],
    tools=tools
)

# 处理工具调用
if response.choices[0].finish_reason == "tool_calls":
    tool_call = response.choices[0].message.tool_calls[0]
    print(f"Function: {tool_call.function.name}")
    print(f"Arguments: {tool_call.function.arguments}")
```

### 图像生成

```python
response = client.images.generate(
    model="wanx2.1-t2i-turbo",
    prompt="一只可爱的猫咪在草地上玩耍",
    n=1,
    size="1024x1024"
)
print(response.data[0].url)
```

### Anthropic 兼容接口

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:1325",
    api_key="dummy-key"
)

response = client.messages.create(
    model="claude-3-sonnet-20240229",  # 会自动映射到 qwen 模型
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Hello, Claude!"}
    ]
)
print(response.content[0].text)
```

---

## 🏗️ 项目结构

```
qwen-server/
├── 📄 qwen_server.py          # FastAPI 应用 + 调度器（~4.7K 行）
├── 📄 qwen_client.py          # Qwen API 客户端 + 账号池（~6.9K 行）
├── 📄 qwen_util.py            # 模型定义、处理器、转换器（~3.4K 行）
├── 📄 qwen_accounts.py        # 账号配置（需自行创建）
├── 📁 api/
│   └── 📄 index.py            # Vercel serverless 入口
└── 📁 data/                   # 数据目录（运行时创建）
    ├── 📁 checkpoints/        # 流式断点续传
    ├── 📁 large_texts/        # 大文本缓存
    ├── 📁 anthropic_files/    # 文件上传存储
    ├── 📁 tts/                # 语音合成输出
    ├── 📁 generated_images/   # 生成图像
    └── 📁 generated_videos/   # 生成视频
```

### 核心目录说明

| 文件/目录 | 说明 |
|-----------|------|
| `qwen_server.py` | HTTP 服务层，包含 FairRequestScheduler 调度器 |
| `qwen_client.py` | 外部 API 通信层，包含 AsyncAccountPool 账号池 |
| `qwen_util.py` | 工具函数、Pydantic 模型、格式转换器 |
| `api/index.py` | Vercel serverless 专用入口，包含路径重定向逻辑 |
| `data/` | 运行时数据存储（Vercel 环境下重定向到 `/tmp/data`） |

---

## ⚙️ 配置说明

### 环境变量

创建 `.env` 文件或在系统中设置以下环境变量：

```bash
# 调度器配置
SCHED_CHAT_CONCURRENT=50        # 聊天请求最大并发
SCHED_CHAT_QUEUE=500            # 聊天请求队列长度
SCHED_CHAT_TIMEOUT=120          # 聊天请求超时（秒）
SCHED_MEDIA_CONCURRENT=10       # 媒体请求最大并发
SCHED_AUX_CONCURRENT=20         # 辅助请求最大并发

# 功能开关
SPECIAL_CODE_MODE=false         # 代码解释器特殊处理模式

# Vercel 环境
DATA_DIR=/tmp/data              # 数据目录（Vercel 必须）
```

### 账号配置（重要）

⚠️ **安全警告**：账号配置文件包含敏感信息，**请勿将其提交到 Git 仓库**！

**步骤 1：编辑 `qwen_accounts.py`**
```python
ACCOUNTS = [
    {
        "email": "your-real-email@example.com",
        "password": "your-real-password",
    },
    # 可以添加更多账号，系统会自动进行负载均衡
]
```

**步骤 2：验证 .gitignore**
确保 `qwen_accounts.py` 已在 `.gitignore` 中：
```bash
# 检查 .gitignore 是否包含 qwen_accounts.py
grep qwen_accounts .gitignore
```

**配置说明：**
- 支持多账号自动轮询和负载均衡
- 账号会根据性能自动排序
- 失败账号会自动进入冷却期
- 支持热重载（修改后无需重启服务）

**多账号优势：**
- 提高并发处理能力
- 自动故障转移
- 基于 Thompson Sampling 的智能调度

### 配置优先级

1. 环境变量（最高优先级）
2. `.env` 文件
3. 代码默认值（最低优先级）

---

## 🔌 API 文档

### OpenAI 兼容端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/v1/models` | 获取可用模型列表 |
| POST | `/v1/chat/completions` | 聊天补全 |
| POST | `/v1/images/generations` | 图像生成 |
| POST | `/v1/audio/speech` | 文本转语音 |
| POST | `/v1/audio/transcriptions` | 语音转文本 |
| POST | `/v1/embeddings` | 文本嵌入 |

### Anthropic 兼容端点

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/v1/messages` | Messages API |
| POST | `/v1/messages/batches` | 批量消息处理 |
| GET | `/v1/models` | 获取模型列表 |

### 偫康检查

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/health` | 服务健康状态 |
| GET | `/v1/metrics` | 调度器和请求指标 |

### 请求示例

#### 聊天补全

```http
POST /v1/chat/completions
Content-Type: application/json

{
  "model": "qwen3-coder-plus",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2048
}
```

**响应示例：**

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "qwen3-coder-plus",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 9,
    "completion_tokens": 9,
    "total_tokens": 18
  }
}
```

---

## ☁️ 部署指南

### Vercel Serverless

**配置说明：**
- 运行时：Python 3.11
- 内存：1024 MB
- 超时：300 秒
- 最大包大小：50 MB

**部署步骤：**

1. Fork 本项目到你的 GitHub 账号
2. 在 Vercel Dashboard 导入项目
3. 配置环境变量（账号信息等）
4. 部署

**注意事项：**
- `api/index.py` 会自动将 `data/` 路径重定向到 `/tmp/data/`
- 账号配置通过环境变量或 Secrets 管理
- 免费版有执行时间限制，长请求可能超时

### 本地生产部署

```bash
# 使用 Gunicorn + Uvicorn
gunicorn qwen_server:app -w 4 -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:1325 \
  --access-logfile - \
  --error-logfile -

# 或使用 Docker
docker build -t qwen-server .
docker run -d -p 1325:1325 --env-file .env qwen-server
```

---

## ❓ 常见问题

<details>
<summary><b>Q1: 如何添加多个 Qwen 账号？</b></summary>

**解决方案：**

创建 `qwen_accounts.py` 文件：

```python
ACCOUNTS = [
    {"email": "user1@example.com", "password": "pass1"},
    {"email": "user2@example.com", "password": "pass2"},
    {"email": "user3@example.com", "password": "pass3"},
]
```

系统会自动使用 Track-and-Stop 算法选择最优账号。
</details>

<details>
<summary><b>Q2: Vercel 部署后提示账号错误？</b></summary>

**可能原因：**
1. 账号配置未正确设置到环境变量
2. `qwen_accounts.py` 未包含在部署包中

**解决方案：**

方案一：使用环境变量（推荐）
```bash
# 在 Vercel Dashboard 设置环境变量
QWEN_ACCOUNTS=[{"email":"xxx","password":"xxx"}]
```

方案二：修改代码从环境变量读取账号

</details>

<details>
<summary><b>Q3: 如何处理长文本输入？</b></summary>

**解决方案：**

系统会自动将超长文本转换为文件上传处理。无需手动干预。

如需调整阈值，修改 `qwen_client.py` 中的 `LARGE_TEXT_THRESHOLD` 配置。
</details>

<details>
<summary><b>Q4: 函数调用没有响应？</b></summary>

**可能原因：**
1. 模型不支持函数调用
2. 函数描述不够清晰

**解决方案：**
- 使用 `qwen3-coder-plus` 模型
- 确保函数 `description` 详细描述功能
- 检查参数 `required` 字段是否正确设置
</details>

<details>
<summary><b>Q5: 如何调试请求问题？</b></summary>

**解决方案：**

1. 启用调试日志：
```bash
export DEBUG=true
python qwen_server.py
```

2. 查看指标端点：
```bash
curl http://localhost:1325/v1/metrics
```

3. 检查调度器状态：
```bash
curl http://localhost:1325/health
```
</details>

---

## 📜 许可证

本项目采用 [MIT 许可证](LICENSE)。

```
MIT License

Copyright (c) 2026 nichengfuben@outlook.com

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```

---

## 🤝 贡献指南

我们欢迎所有形式的贡献！

### 如何贡献

1. **Fork 本仓库**
2. **创建功能分支** (`git checkout -b feature/AmazingFeature`)
3. **提交更改** (`git commit -m 'Add some AmazingFeature'`)
4. **推送到分支** (`git push origin feature/AmazingFeature`)
5. **创建 Pull Request**

### 注意事项

- 编辑 `qwen_server.py`、`qwen_client.py` 或 `qwen_util.py` 代替
- 遵循现有代码风格（类型提示、异步函数等）
- 确保所有修改都通过类型检查

### 报告问题

如果你发现了 Bug 或有功能建议，请在 [Issues](../../issues) 中提交。

---

<p align="center">
  如果这个项目对你有帮助，请给一个 ⭐️ Star！
</p>

<p align="center">
  <a href="https://github.com/yourusername/qwen-server">GitHub</a> •
  <a href="#-目录">文档</a> •
  <a href="#-常见问题">FAQ</a>
</p>