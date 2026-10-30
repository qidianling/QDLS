# 在 DeepSeek Harness（DSH）里用 QDLS

DSH 通过 **MCP** 接外部工具，QDLS 自带一个 MCP 服务器 `qdls_mcp.py`（纯标准库、stdio），
所以不需要给 DSH 写专属插件，任何 MCP 客户端都能用。

## 1. 拿 token

QDLS 控制台 →「接入」→「生成配对码」，把码换成 token（码是 32 位随机串）：

```bash
curl -s -X POST http://127.0.0.1:6768/api/v1/pair/claim \
     -H "Content-Type: application/json" \
     -d "{\"code\":\"<32位配对码>\",\"bot_name\":\"DSH\",\"platform\":\"dsh\"}"
# {"ok":true,"token":"qdls_...","gateway":"http://127.0.0.1:6768"}
```

## 2. 注册 MCP 服务器

在 DSH 的 MCP 配置里加一条（等价于 ACP `session/new` 的 `mcpServers` 条目）：

```json
{
  "mcpServers": {
    "qdls": {
      "command": "C:\\DS\\py311\\python.exe",
      "args": ["C:\\DS\\QDLS\\qdls_mcp.py"],
      "env": {
        "QDLS_URL": "http://127.0.0.1:6768",
        "QDLS_TOKEN": "qdls_xxx"
      }
    }
  }
}
```

> `command` 换成你机器上的 python；用内置运行时就是 `QDLS\runtime\python.exe`。
> 没有图形配置入口时，把它写进 DSH 会话参数的 `mcpServers` 数组即可。

## 3. 可用的工具

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `qdls_sing` | `song`（歌名/链接）、`voice`（可选） | 生成的 mp3 路径 |
| `qdls_voices` | — | 可用音色 id 列表 |
| `qdls_status` | — | 是否忙碌、队列长度 |

DSH 里直接说“用 qdls 唱首雨爱”即可；工具会把成品写到 `%TEMP%\qdls\`，
路径返回给 agent，后续可以再交给别的工具播放/发送。

## 4. 端口说明（容易踩）

| 端口 | 用途 |
| --- | --- |
| **6768** | 面板 + REST API（MCP、AstrBot 插件都连这个） |
| 6767 | 引擎网关（Gradio，给旧插件/本地点歌台用） |

MCP 与 `astrbot_plugin_qdls` 都只需要 **6768**。

## 5. 跨机（DSH 不在本机时）

把 **6768** 反向转发到运行 DSH 的机器：

```bash
ssh -N -R 127.0.0.1:6768:127.0.0.1:6768 user@host
```

然后 MCP 的 `QDLS_URL` 填 `http://127.0.0.1:6768`（在 host 上就是本机回环）。
或者用 Tailscale 直接填对方内网 IP。

## 6. 排错

| 现象 | 原因 |
| --- | --- |
| `301/404 Not Found` | 连到 6767 了，API 在 6768 |
| `401 invalid token` | token 被撤销或没配 |
| `403 配对码无效` | 码过期（10 分钟）或已用过 |
| 工具没出现 | MCP 配置没生效，重启 DSH 会话 |
