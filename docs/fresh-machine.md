# 全新电脑：只有 QDLS + DSH，怎么连起来

> 前提：Windows 10/11 + NVIDIA 显卡。DSH 已装好并能正常开窗口。
> 全程 **不需要手写任何配置文件**，只有第 4 步是 GUI 操作。

## 第 1 步：装 QDLS

解压发布包 → 双击 **`setup.vbs`** → 安装向导：

1. 选安装目录（默认 `%USERPROFILE%\QDLS`，可浏览改）；
2. 勾选组件（依赖 / SVC 模型 / Demucs，第一次全勾）；
3. 看进度条等它下完（约 5.5 GB）；
4. 完成页会创建桌面 **QDLS** 快捷方式。

> 机器上没有 Python 也没关系，向导会自动下载内置运行时（约 11 MB）。

## 第 2 步：启动 QDLS

双击桌面 **QDLS**。等 30 秒（模型加载），控制台「本机网关」变绿。

> **关闭窗口不会退出。** 程序会缩到右下角系统托盘继续跑，AstrBot 照常可用。
> 想彻底退出（释放显存），右键托盘图标 → 「退出 QDLS」。
> 双击托盘图标可以重新打开控制台。

## 第 3 步：生成 DSH 凭据（点一下）

控制台左侧 **「接入」** →

- 点 **「写入 DSH 凭据」**；
- 提示会显示文件路径 + token 尾号（文件在 `QDLS\qdls.token`）；
- 这一步会在「API Token」列表里登记一条 `DeepSeek Harness`，随时可撤销。

**不需要复制任何 key，也不需要打开记事本。**

## 第 4 步：让 DSH 加载插件（GUI）

在 DSH 里：

1. 打开 **插件管理 / Plugins**（在设置齿轮里；若找不到见下方“找不到入口”）；
2. 选 **安装 bundle**，目录填：

```
<你的安装目录>\dsh-bundle
```

3. 安装完成后 **重启 DSH**（关掉主窗口再打开）。

## 第 5 步：验证

新会话里，工具列表应出现：

```
mcp__qdls__qdls_sing
mcp__qdls__qdls_voices
mcp__qdls__qdls_status
```

直接说“用 qdls 看下状态”即可；第一次调用会返回类似
`QDLS：空闲，队列 0 首，音色 3 个`。

## 如果第 4 步找不到插件管理入口

两种情况：

1. **DSH 的 `ui-settings` 被禁用**：编辑
   `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml`，
   删掉 `- id: ui-settings` 那段（或把 `enabled: false` 改成 `true`），重启 DSH。
2. **界面里确实没有插件管理**：在 DSH 会话里对 agent 说
   **「用 plugin_manager 安装 <安装目录>\dsh-bundle」**，
   agent 会调用 `plugin_manager action=install_bundle`，效果相同。

## 如果第 5 步报错

先跑自检（会打印 python、token 尾号，并直接调一次 API）：

```powershell
powershell -ExecutionPolicy Bypass -File <安装目录>\tools\test_mcp.ps1
```

| 自检结果 | 含义 |
| --- | --- |
| `tools/call : QDLS：空闲…` | 服务器端没问题，问题在 DSH 加载 → 看插件日志 |
| `401 Unauthorized` | 凭据失效：控制台「接入」重新「写入 DSH 凭据」→ 重启 DSH |
| 没有任何输出 | python 路径或 `qdls_mcp.py` 路径不对 → 跑 `tools\install_dsh_mcp.ps1` 重写 bundle |

## 补充：如果安装目录不是默认路径

安装到 `D:\Apps\QDLS` 之后，务必再跑一次：

```powershell
powershell -ExecutionPolicy Bypass -File D:\Apps\QDLS\tools\install_dsh_mcp.ps1
```

它会把 bundle 里的 `command / args / cwd` 改成本机真实路径，否则 DSH 找不到 python 或脚本。

## 一页速查

```
1. setup.vbs            → 装 QDLS（自动装 python / 模型）
2. 桌面 QDLS            → 启动，等网关变绿
3. 接入 → 写入 DSH 凭据 → 生成 qdls.token
4. DSH → 插件管理 → 安装 bundle: <安装目录>\dsh-bundle
5. 重启 DSH             → 工具出现 mcp__qdls__*
```
