# 随声译 MVP

面向 Android 优先的中文口述到英文翻译与语音播报工具。前端同时支持手机浏览器/PWA 与 Capacitor Android 壳，界面先保证流程可用，视觉细节后续再统一优化。

## 当前能力

- 创建、重命名、删除最多 10 个会话；原文、译文、上下文摘要保存在当前设备的 IndexedDB，并由服务端保留配额和翻译请求状态。
- 手机录音、可调 1–10 秒静音阈值、停顿后 3 秒可取消倒计时；录音结束后由用户确认才上传。
- 火山引擎 ASR → 阿里云 DashScope Qwen Flash 上下文翻译 → 火山引擎 TTS。
- 播报语速 0.5–2.0×，可选择配置的预置音色；音色克隆需勾选已获本人授权。
- 单对话 24 小时音频/150 万 token、设备总计 240 小时/1500 万 token 的硬上限与处理中请求预留。
- 服务端不把原始录音或 TTS 文件写盘：ASR 通过 5 分钟签名 HTTPS 地址暂存内存；合成音频只在服务进程内存保留 30 分钟。服务进程重启会使临时播报失效。

## 服务端配置

从 `server/.env.example` 复制为 `server/.env`，填写至少 32 字符随机 `APP_SECRET_KEY`、私有分享密码、`LLM_API_KEY`、火山引擎 `VOLCENGINE_API_KEY`、ASR/TTS 资源包、TTS 默认音色、`PUBLIC_BASE_URL` 和允许的前端源。ASR 供应商必须能通过公网 HTTPS 拉取 5 分钟临时音频 URL，因此本机 localhost 不能完成真实 ASR。

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`PRESET_VOICES_JSON` 接收 JSON 数组，每项包含 `id`、`name`、`description`、`provider_voice_id`。示例：

```json
[{"id":"warm-female","name":"温和女声","description":"清晰自然","provider_voice_id":"替换为账号已开通的音色ID"}]
```

凭据/资源包要在火山引擎和 DashScope 账号中开通并设置预算。若云端返回成功，但 API 响应在客户端超时，前端会查询请求状态；用量仍未知时最多等 10 分钟并按预留 token ceiling 结算。

## 前端运行

```powershell
cd web
npm install
npm run dev
```

前端开发服务器默认 5173。设置 `VITE_API_BASE=http://localhost:8000` 可通过 CORS 连接 API；生产同域反向代理 `/api` 最简单。麦克风需要 HTTPS 或 localhost。

## Android

Android Capacitor 工程在 `web/android`。安装 JDK、Android SDK 并设置 `JAVA_HOME`、`ANDROID_HOME` 后运行：

```powershell
cd web
npm run android:apk
```

当前执行环境没有 JDK/Android SDK，所以已生成 Android 工程壳，但未在此环境产出 APK。PWA 生产构建命令：`npm run build`。
