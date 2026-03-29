# Nex - Mac 端安装与运行指南

Nex 是一个跨平台键鼠共享工具，让 Windows 和 Mac 共享一套键盘鼠标。
Windows 作为 Server，Mac 作为 Client。鼠标划到 Windows 屏幕左边缘时切换到 Mac，Mac 屏幕右边缘切回 Windows。

## 给 Mac 上的 Claude Code Agent 的指令

请按以下步骤帮用户安装并运行 Nex。

### 第 1 步：下载 nex.py

从 Windows 的 HTTP server 下载（端口可能是 8080 或 9090，都试一下）：

```bash
curl -o ~/nex.py http://192.168.31.99:9090/nex.py
```

如果失败，试：

```bash
curl -o ~/nex.py http://192.168.31.99:8080/nex.py
```

如果都失败，说明 Windows 端的 HTTP server 没在运行，让用户在 Windows 端重启。

### 第 2 步：安装依赖

```bash
pip3 install pyobjc-framework-Quartz
```

如果报权限错误：

```bash
pip3 install --user pyobjc-framework-Quartz
```

### 第 3 步：授权辅助功能（重要！）

Nex 需要辅助功能权限才能注入键鼠事件。没有这个权限，鼠标和键盘注入会静默失败。

1. 打开 **系统设置 → 隐私与安全性 → 辅助功能**
2. 点击 **+** 按钮
3. 添加你使用的终端应用（Terminal.app 或 iTerm2）
4. 确保开关是 **打开** 的

如果之前添加过但不工作，先移除再重新添加。

### 第 4 步：运行

```bash
python3 ~/nex.py -v
```

程序会自动检测 macOS，以 client 模式运行，连接 Windows server `192.168.31.99:24800`。

如果需要指定不同的 server IP 或端口：

```bash
python3 ~/nex.py --host 192.168.31.99 --port 24800 -v
```

### 预期日志输出

正常启动后应该看到：

```
HH:MM:SS [INFO] Starting client (screen WxH). Connecting to 192.168.31.99:24800
HH:MM:SS [INFO] Connecting to server 192.168.31.99:24800 ...
HH:MM:SS [INFO] Connected to server
```

### 使用方式

- Windows 端鼠标划到 **左边缘** → 控制权切换到 Mac，Mac 上鼠标从左侧出现
- Mac 端鼠标划到 **右边缘** → 控制权切回 Windows
- 按 **ESC** → 紧急切回 Windows（无论鼠标在哪）
- 键盘跟随鼠标，在哪台电脑就输入到哪台

### 故障排查

| 问题 | 解决方案 |
|------|----------|
| 连不上 server | 确认 Windows 端 nex.py 已运行，端口 24800 未被防火墙拦截 |
| 鼠标能动但键盘不行 | 检查辅助功能权限是否已授予终端 |
| 鼠标完全不动 | 检查辅助功能权限，尝试移除后重新添加 |
| pyobjc 安装失败 | 确保 Xcode Command Line Tools 已安装：`xcode-select --install` |
| 连接断开后不重连 | 程序会自动每 3 秒重试，等待即可 |
| 控制权卡在 Mac 回不来 | 按 ESC 强制切回 Windows |

### 停止运行

在终端按 `Ctrl+C` 停止程序。
