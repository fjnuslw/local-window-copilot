# 用户无感部署方案

## 1. 目标

最终产品必须做到：

```text
用户下载应用或模型资源包
点击启动
应用自动校验模型
应用自动启动本地推理服务
用户直接使用窗口分析能力
```

用户不需要：

```text
安装 Ollama
配置 .env
手动启动 llama.cpp
手动填写模型路径
手动设置服务端口
理解 Python 环境或命令行参数
```

## 2. 部署路线定版

本项目当前 MVP 部署路线固定为：

```text
Python + Win32 桌面悬浮窗
  + FastAPI 本地后端进程
  + llama.cpp runtime sidecar
  + MiniCPM-V 4.6 GGUF 模型资源包
```

产品化打包优先考虑：

```text
PyInstaller / Nuitka 打包桌面悬浮窗和 FastAPI 后端
隐藏启动本地后端和 llama.cpp
用资源包 manifest 管理模型文件
```

Tauri 保留为后续备选，主要用于系统托盘、自动更新、复杂设置页和 WebView 面板，不作为当前 MVP 的关键依赖。

不把 Ollama 作为用户依赖。

不把 PyTorch / Transformers 作为桌面端运行时。

不要求用户接入云端模型 API。

## 3. 资源包结构

```text
LocalWindowAgent/
  LocalWindowAgent.exe

  resources/
    desktop/
      desktop_floating_window.exe

    backend/
      backend.exe

    runtime/
      llama.cpp/
        llama-server.exe
        llama-mtmd-cli.exe

    models/
      minicpm-v4.6/
        MiniCPM-V-4_6-F16.gguf
        mmproj-model-f16.gguf
        model_manifest.json
```

`model_manifest.json` 示例：

```json
{
  "model_id": "OpenBMB/MiniCPM-V-4.6",
  "variant": "instruct-f16",
  "runtime": "llama.cpp",
  "language_model": "MiniCPM-V-4_6-F16.gguf",
  "vision_projector": "mmproj-model-f16.gguf",
  "ctx_size": 8192,
  "reasoning": "off",
  "required_llamacpp": "b9049+"
}
```

## 4. 启动流程

应用启动时自动执行：

```text
1. 启动桌面悬浮窗主进程。
2. 启动 FastAPI 本地后端进程。
3. FastAPI 检查模型资源包。
4. 校验 GGUF 和 mmproj 文件是否存在。
5. 校验 model_manifest.json。
6. 检查 llama.cpp runtime 是否存在。
7. 自动选择可用运行模式。
8. 启动 llama.cpp 推理进程。
9. 桌面悬浮窗等待 /health 返回 ready。
10. 桌面悬浮窗显示待命状态，用户开始分析当前窗口。
```

用户只看到：

```text
正在准备本地模型
正在加载模型
本地模型已就绪
```

## 5. 模型资源下载方式

推荐提供两种发行包。

### 5.1 完整离线包

```text
应用
+ FastAPI sidecar
+ llama.cpp runtime
+ MiniCPM-V 4.6 GGUF
+ mmproj
```

优点：

```text
下载后即可用
不依赖首次联网
隐私定位最强
```

缺点：

```text
包体积大
更新模型成本高
```

### 5.2 轻量应用包 + 模型资源包

```text
应用安装包
+ 单独模型资源包
```

首次启动时，如果模型不存在：

```text
提示用户下载模型资源包
下载完成后自动校验
自动放入 resources/models/minicpm-v4.6/
自动启用
```

用户体验仍然是“点击式”，不是配置式。

推荐面试项目先做这种方式，因为可以展示：

```text
资源包 manifest
模型文件校验
下载进度
断点续传
本地缓存
```

## 6. 内部配置

用户不编辑 `.env`。

应用自动生成配置：

```text
%APPDATA%/LocalWindowAgent/config.json
```

示例：

```json
{
  "runtime": {
    "type": "llama.cpp",
    "mode": "server",
    "port": 18181
  },
  "model": {
    "id": "OpenBMB/MiniCPM-V-4.6",
    "variant": "instruct-f16",
    "path": "resources/models/minicpm-v4.6"
  },
  "privacy": {
    "save_screenshots": false,
    "save_history": true,
    "sensitive_window_protection": true
  }
}
```

设置页只展示用户能理解的内容：

```text
本地模型状态
资源包是否完整
重新校验模型
重新下载模型
隐私保护开关
历史记录开关
清理缓存
```

## 7. FastAPI Runtime Manager

后端统一通过 `ModelRuntimeManager` 管理推理运行时。

```text
ModelRuntimeManager
  +-- verify_runtime_files()
  +-- verify_model_files()
  +-- load_manifest()
  +-- start_llamacpp_server()
  +-- stop_llamacpp_server()
  +-- health_check()
  +-- analyze_image()
```

MVP 阶段可以先用子进程调用 `llama-mtmd-cli.exe`：

```text
FastAPI
  → subprocess.run(llama-mtmd-cli.exe ...)
  → parse stdout
  → return JSON
```

后续切换为常驻 `llama-server.exe`：

```text
FastAPI
  → HTTP request to 127.0.0.1:18181
  → parse response
  → return JSON
```

这仍然是同一条目标路线，不是切换方案。

目标 server 启动参数：

```text
llama-server.exe
  -m resources/models/minicpm-v4.6/MiniCPM-V-4_6-F16.gguf
  --mmproj resources/models/minicpm-v4.6/mmproj-model-f16.gguf
  -c 8192
  --gpu-layers all
  --reasoning off
  --host 127.0.0.1
  --port 18181
  --no-webui
```

必要说明：

```text
MiniCPM-V 4.6 Instruct 路线需要关闭 thinking/reasoning，以保证 JSON 输出稳定。
llama-server 支持 --reasoning off。
本地 b9846 的 llama-mtmd-cli 不支持 --reasoning / -rea，因此 CLI 只用于早期验证。
```

FastAPI 发送图片前必须处理：

```text
截图缩放：默认最长边 512px
编码格式：JPEG
请求编码：UTF-8 JSON
接口：/v1/chat/completions
```

当前实测：

```text
llama-server 冷启动到 /health ready：约 11.62 秒
512px JPEG 热请求：约 2.40 秒
```

## 8. 用户不可见性要求

必须满足：

```text
不要求用户打开命令行
不要求用户安装第三方模型管理器
不要求用户知道模型文件名
不要求用户手写配置
不弹出推理服务窗口
后台进程由应用启动和退出
应用退出时清理子进程
```

Windows 上启动 sidecar 时：

```text
Start-Process 使用隐藏窗口
或由打包后的桌面主进程统一管理后台子进程
```

## 9. MVP 实现顺序

```text
1. 手动准备 GGUF 和 mmproj 文件。
2. 手动准备 llama.cpp exe。
3. 写 model_manifest.json。
4. 写 ModelRuntimeManager 校验文件。
5. 用 FastAPI 调用 llama-mtmd-cli 跑单张图片。
6. 前端只显示“本地模型已就绪 / 加载失败”。
7. 再做模型资源包下载和校验。
8. 再做后台进程隐藏启动和退出清理。
```

## 10. 一句话原则

```text
开发可以看到 llama.cpp，用户不应该看到 llama.cpp。
工程可以有配置文件，用户不应该编辑配置文件。
项目可以有复杂运行时，用户只应该感到“下载后能用”。
```
