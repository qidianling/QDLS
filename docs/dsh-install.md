# 在 DSH 里安装 QDLS（图文步骤）

## 先说结论：DSH 没有「MCP 服务器」设置页

DSH 的 MCP 不是直接填地址的独立页面，而是**打包成插件（bundle）**再安装：

```
一个 bundle 目录
├─ package.json        声明 dsh.bundle.patch
├─ cordis.patch.yml    插入 @deepseek-ai/dsh-mcp-client，写 command/args/env
├─ locale/*.json       插件标题与描述
└─ icon.svg            图标
        ↓ 通过「插件管理」安装
插件行（带配置表单：QDLS_URL / QDLS_TOKEN ...）
        ↓ 启用
工具：mcp__qdls__qdls_sing / qdls_voices / qdls_status
```

所以你要找的「可视化配置 API 链接」，位置是**装好之后的插件行 → 配置**，不是某个 MCP 页面。

## 三步安装

### 1. 打开插件管理

Harness 窗口里：右上角 **设置（齿轮）→ 插件 / Plugins**。
（有的版本叫 “Plugin Manager”“扩展”。）

### 2. 安装本地 bundle

点 **安装 bundle / Install bundle**，目标目录填：

```
<QDLS>\dsh-bundle
```

安装会写入当前 profile（`~/.dsh/profiles/desktop`），**对所有会话生效、重启后仍在**。

> 如果界面里没有这个按钮：直接在会话里对 agent 说
> 「用 plugin_manager 安装 <QDLS>\dsh-bundle」，
> 它会调用 `plugin_manager action=install_bundle target=<绝对路径>`。

### 3. 启用并验证

- 装完该插件默认启用，插件行会显示图标 + 标题「QDLS 翻唱引擎」；
- 新开一个会话，输入 `/tools`（或看工具列表）应出现：

```
mcp__qdls__qdls_sing
mcp__qdls__qdls_voices
mcp__qdls__qdls_status
```

## 配置（可视化改地址）

插件行 → **配置**，字段来自 `cordis.patch.yml`：

| 字段 | 当前值 | 说明 |
| --- | --- | --- |
| `serverName` | `qdls` | 决定工具前缀 `mcp__qdls__*` |
| `transport` | `stdio` | 本地进程方式 |
| `command` | `<PYTHON>/python.exe` | 换成内置运行时就是 `QDLS/runtime/python.exe` |
| `args` | `<QDLS>/qdls_mcp.py` | MCP 服务器脚本 |
| `env.QDLS_URL` | `http://127.0.0.1:6768` | **API 地址**（注意不是 6767） |
| `env.QDLS_TOKEN` | `qdls_...` | 控制台「接入」页可撤销重发 |
| `failOnStartupError` | `true` | 连不上直接报错，便于发现 |

改完保存即可，不需要重新安装。

## 预览

打开 `docs/dsh-plugin-preview.html`（本地文件），可以看到插件行的样子：
图标、标题「QDLS 翻唱引擎」、MCP 徽标、三个工具名，以及配置表单里的字段。

## 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 装完没有工具 | 会话没重开；或 `failOnStartupError` 报错，看插件日志 |
| `401 invalid token` | token 被撤销，重新配对后改 `env.QDLS_TOKEN` |
| 连不上 | `QDLS_URL` 填错端口（应为 6768），或 QDLS 控制台没开 |
| 想换 Python | 改 `command` 指向 `runtime\python.exe` 或系统 python |

## 为什么不是 `mcpServers` JSON

DSH 的会话协议里确实有 `mcpServers`，但那是 ACP 客户端（编辑器/IDE）给 Harness 传参用的。
**在 Harness 自己的界面里**，官方路径就是 bundle + 插件管理，本文按这个来。## 1. 为什么配置页没有“填 key”的输入框

两个原因：

1. 我们装的是**配置型 bundle**（只插入官方 @deepseek-ai/dsh-mcp-client，把参数写死在 cordis.patch.yml 里），它没有自己的 Config schema，所以插件页只显示标题/描述/开关，不渲染字段表单；
2. 你这台机器的 ui-settings 插件在 profile 里被设成了 enabled: false，设置/插件管理界面本身就不在。

**所以现在的设计是：不需要填 key。**

## 2. 一次点击，key 自动就位

QDLS 控制台 →「接入」→ **「写入 DSH 凭据」**：

- 生成一个 token（名为 DeepSeek Harness，记录在案、可撤销）；
- 写进 <QDLS>\qdls.token（或 %LOCALAPPDATA%\QDLS\dsh.token）；
- MCP 服务器启动时**自动读取**这个文件，环境变量里不再需要 token。

之后只要**重启 DSH**，插件就会用新凭据连上，全程不用碰配置文件。

## 3. 启用并验证

- 装完该插件默认启用，插件行会显示图标 + 标题「QDLS 翻唱引擎」；
- 新开一个会话，输入 `/tools`（或看工具列表）应出现：

```
mcp__qdls__qdls_sing
mcp__qdls__qdls_voices
mcp__qdls__qdls_status
```

## 配置（可视化改地址）

插件行 → **配置**，字段来自 `cordis.patch.yml`：

| 字段 | 当前值 | 说明 |
| --- | --- | --- |
| `serverName` | `qdls` | 决定工具前缀 `mcp__qdls__*` |
| `transport` | `stdio` | 本地进程方式 |
| `command` | `<PYTHON>/python.exe` | 换成内置运行时就是 `QDLS/runtime/python.exe` |
| `args` | `<QDLS>/qdls_mcp.py` | MCP 服务器脚本 |
| `env.QDLS_URL` | `http://127.0.0.1:6768` | **API 地址**（注意不是 6767） |
| `env.QDLS_TOKEN` | `qdls_...` | 控制台「接入」页可撤销重发 |
| `failOnStartupError` | `true` | 连不上直接报错，便于发现 |

改完保存即可，不需要重新安装。

## 预览

打开 `docs/dsh-plugin-preview.html`（本地文件），可以看到插件行的样子：
图标、标题「QDLS 翻唱引擎」、MCP 徽标、三个工具名，以及配置表单里的字段。

## 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 装完没有工具 | 会话没重开；或 `failOnStartupError` 报错，看插件日志 |
| `401 invalid token` | token 被撤销，重新配对后改 `env.QDLS_TOKEN` |
| 连不上 | `QDLS_URL` 填错端口（应为 6768），或 QDLS 控制台没开 |
| 想换 Python | 改 `command` 指向 `runtime\python.exe` 或系统 python |

## 为什么不是 `mcpServers` JSON

DSH 的会话协议里确实有 `mcpServers`，但那是 ACP 客户端（编辑器/IDE）给 Harness 传参用的。
**在 Harness 自己的界面里**，官方路径就是 bundle + 插件管理，本文按这个来。## 1. 手动拿 token

QDLS 控制台 →「接入」：
- 名称填 DSH → 点 **「直接生成 Token」**；
- token **只显示这一次**，点「复制」保存好（服务端只存 sha256，丢了只能重新生成）。

> 这是纯手动步骤：控制台不会主动连 DSH，也不会自动写入任何配置。

## 2. 手动写进 bundle

改 dsh-bundle\cordis.patch.yml 的 QDLS_TOKEN（或者用脚本）：

`powershell
powershell -ExecutionPolicy Bypass -File tools\install_dsh_mcp.ps1 -Token qdls_xxx
`

脚本只写文件、不联网、不兑换配对码。

## 3. 启用并验证

- 装完该插件默认启用，插件行会显示图标 + 标题「QDLS 翻唱引擎」；
- 新开一个会话，输入 `/tools`（或看工具列表）应出现：

```
mcp__qdls__qdls_sing
mcp__qdls__qdls_voices
mcp__qdls__qdls_status
```

## 配置（可视化改地址）

插件行 → **配置**，字段来自 `cordis.patch.yml`：

| 字段 | 当前值 | 说明 |
| --- | --- | --- |
| `serverName` | `qdls` | 决定工具前缀 `mcp__qdls__*` |
| `transport` | `stdio` | 本地进程方式 |
| `command` | `<PYTHON>/python.exe` | 换成内置运行时就是 `QDLS/runtime/python.exe` |
| `args` | `<QDLS>/qdls_mcp.py` | MCP 服务器脚本 |
| `env.QDLS_URL` | `http://127.0.0.1:6768` | **API 地址**（注意不是 6767） |
| `env.QDLS_TOKEN` | `qdls_...` | 控制台「接入」页可撤销重发 |
| `failOnStartupError` | `true` | 连不上直接报错，便于发现 |

改完保存即可，不需要重新安装。

## 预览

打开 `docs/dsh-plugin-preview.html`（本地文件），可以看到插件行的样子：
图标、标题「QDLS 翻唱引擎」、MCP 徽标、三个工具名，以及配置表单里的字段。

## 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 装完没有工具 | 会话没重开；或 `failOnStartupError` 报错，看插件日志 |
| `401 invalid token` | token 被撤销，重新配对后改 `env.QDLS_TOKEN` |
| 连不上 | `QDLS_URL` 填错端口（应为 6768），或 QDLS 控制台没开 |
| 想换 Python | 改 `command` 指向 `runtime\python.exe` 或系统 python |

## 为什么不是 `mcpServers` JSON

DSH 的会话协议里确实有 `mcpServers`，但那是 ACP 客户端（编辑器/IDE）给 Harness 传参用的。
**在 Harness 自己的界面里**，官方路径就是 bundle + 插件管理，本文按这个来。
