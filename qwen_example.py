import requests
import json
import time
import base64
import sys

# API配置
API_BASE_URL = "http://127.0.0.1:1325"  
API_KEY = "sk-qwen-server"  

def send_chat_request(model, messages, timeout=600.0, max_retries=3):
    """
    发送聊天请求到API
    """
    url = f"{API_BASE_URL}/v1/chat/completions"  
    
    # 构建头部
    headers = {
        "Content-Type": "application/json",
    }
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    
    data = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    
    print(f"发送请求到: {url}")
    print(f"使用模型: {model}")
    print(f"消息数量: {len(messages)}")
    
    # 重试机制
    for attempt in range(max_retries):
        try:
            print(f"第 {attempt + 1} 次尝试...")
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=timeout,
                proxies=None  # 禁用代理
            )
            
            print(f"响应状态码: {response.status_code}")
            
            # 检查响应状态
            if response.status_code != 200:
                print(f"响应文本: {response.text[:500]}...")
                response.raise_for_status()
            
            # 解析响应
            try:
                result = response.json()
                print("成功解析JSON响应")
            except json.JSONDecodeError as e:
                print(f"JSON解析错误: {e}")
                print(f"原始响应: {response.text[:500]}...")
                raise
            
            # 检查响应结构
            if "error" in result:
                error_msg = result["error"].get("message", "未知错误")
                print(f"API返回错误: {error_msg}")
                raise Exception(f"API错误: {error_msg}")
            
            if "choices" in result and len(result["choices"]) > 0:
                if "message" in result["choices"][0]:
                    content = result["choices"][0]["message"].get("content", "")
                    print(f"获取到内容，长度: {len(content)} 字符")
                    return result, content
                else:
                    print(f"响应格式异常，choices[0]中没有message: {result['choices'][0]}")
            else:
                print(f"响应中没有choices或choices为空: {result.keys()}")
                raise ValueError("响应中没有有效的内容")
                
        except requests.exceptions.Timeout:
            print(f"请求超时，尝试次数: {attempt + 1}/{max_retries}")
            if attempt == max_retries - 1:
                raise Exception(f"请求超时，已达到最大重试次数 {max_retries}")
            time.sleep(1)  # 等待1秒后重试
            
        except requests.exceptions.RequestException as e:
            print(f"请求错误: {e}")
            if attempt == max_retries - 1:
                raise Exception(f"请求失败: {e}")
            time.sleep(1)
            
        except Exception as e:
            print(f"其他错误: {e}")
            if attempt == max_retries - 1:
                raise Exception(f"最终失败: {e}")
            time.sleep(1)
    
    return None, None

def test_connection():
    """测试服务器连接"""
    print("测试服务器连接...")
    try:
        url = f"{API_BASE_URL}/health"
        print(f"尝试连接: {url}")
        response = requests.get(url, timeout=10)
        print(f"响应状态: {response.status_code}")
        if response.status_code == 200:
            print(f"成功连接到: {url}")
            print(f"响应内容: {response.text[:200]}...")
            return True
    except Exception as e:
        print(f"连接 {url} 失败: {e}")
        print("尝试直接访问聊天端点...")
        test_data = {
            "model": "qwen3-coder-plus",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False
        }
        response = requests.post(
            f"{API_BASE_URL}/v1/chat/completions",
            json=test_data,
            timeout=10
        )
        if response.status_code < 500:  # 不是服务器错误
            print(f"聊天端点响应状态: {response.status_code}")
            return True
            
        return False
        
    except Exception as e:
        print(f"连接测试失败: {e}")
        return False

def test_text_model():
    """
    测试文本模型 - 使用更短的文本
    """
    print("\n" + "=" * 60)
    print("测试文本模型 (qwen3-coder-plus)")
    print("=" * 60)
    
    try:
        # 构造消息 - 使用中等长度的文本
        test_text = "你好" * 10  # 20字符，避免触发大文本转换
        messages = [
            {"role": "user", "content": test_text}
        ]
        
        print(f"测试文本长度: {len(test_text)} 字符")
        print("正在发送文本请求...")
        start_time = time.time()
        
        # 发送请求
        result, response_content = send_chat_request("qwen3-coder-plus", messages)
        
        end_time = time.time()
        print(f"请求耗时: {end_time - start_time:.2f}秒")
        
        if response_content:
            print(f"回答长度: {len(response_content)} 字符")
            if len(response_content) > 200:
                print(f"回答内容 (前200字符): {response_content[:200]}...")
            else:
                print(f"回答内容: {response_content}")
        else:
            print("未获取到回答内容")
        
        # 显示使用情况
        if result and "usage" in result:
            usage = result["usage"]
            print(f"\n=== Token 使用 ===")
            print(f"提示词 tokens: {usage.get('prompt_tokens', 0)}")
            print(f"完成 tokens: {usage.get('completion_tokens', 0)}")
            print(f"总计 tokens: {usage.get('total_tokens', 0)}")
        elif result:
            print(f"响应中没有usage字段，响应结构: {list(result.keys())}")
            
    except Exception as e:
        print(f"文本模型测试错误: {e}")
        import traceback
        traceback.print_exc()

def test_vision_model(image_path="img_test.jpg"):
    """
    测试视觉模型
    """
    print("\n" + "=" * 60)
    print("测试视觉模型 (qwen3-vl-plus)")
    print("=" * 60)
    
    try:
        # 检查图片文件是否存在
        import os
        if not os.path.exists(image_path):
            print(f"警告: 图片文件 '{image_path}' 未找到！")
            print("使用一个简单的文本测试代替")
            
            messages = [
                {"role": "user", "content": "请描述一张日落的图片，用文字表达"}
            ]
            
            print("正在发送替代文本请求...")
            start_time = time.time()
            
            result, response_content = send_chat_request("qwen3-vl-plus", messages)
            
        else:
            # 读取图片并编码为 base64
            with open(image_path, "rb") as img_file:
                img_data = img_file.read()
                img_base64 = base64.b64encode(img_data).decode('utf-8')

            print(f"图片大小: {len(img_data):,} bytes")
            print(f"Base64 字符串长度: {len(img_base64):,} 字符")
            
            # 注意：base64字符串很长，可能触发大文本转换
            if len(img_base64) > 1000000:  # 1MB
                print("警告：图片较大，可能被转换为文件上传")
            
            # 构建消息
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请详细描述这张图片"},
                        {
                            "type": "image_url", 
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        }
                    ]
                }
            ]
            
            print("正在发送图片请求...")
            start_time = time.time()
            
            # 发送请求
            result, response_content = send_chat_request("qwen3-vl-plus", messages)
        
        end_time = time.time()
        print(f"请求耗时: {end_time - start_time:.2f}秒")
        
        if response_content:
            print(f"回答长度: {len(response_content)} 字符")
            print("\n=== 图片描述 ===")
            print(response_content)
        else:
            print("未获取到回答内容")
        
        # 显示使用情况
        if result and "usage" in result:
            usage = result["usage"]
            print(f"\n=== Token 使用 ===")
            print(f"提示词 tokens: {usage.get('prompt_tokens', 0)}")
            print(f"完成 tokens: {usage.get('completion_tokens', 0)}")
            print(f"总计 tokens: {usage.get('total_tokens', 0)}")
            
    except Exception as e:
        print(f"视觉模型测试错误: {e}")
        import traceback
        traceback.print_exc()


def main():
    """
    主函数
    """
    print("开始测试 API 接口")
    print(f"服务器地址: {API_BASE_URL}")
    print("=" * 60)
    
    try:
        # 首先测试连接
        if not test_connection():
            print("服务器连接失败，请检查服务器状态")
            return
        
        # 测试文本模型
        test_text_model()
        
        # 测试视觉模型
        test_vision_model()
        
        print("\n" + "=" * 60)
        print("所有测试完成！")
        
    except KeyboardInterrupt:
        print("\n测试被用户中断")
    except Exception as e:
        print(f"程序运行错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
