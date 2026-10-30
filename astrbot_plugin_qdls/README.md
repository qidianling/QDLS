# astrbot_plugin_qdls

把 [QDLS](../README.md) 本地翻唱引擎接到 AstrBot 上的薄客户端插件。

## 它做什么

| 工具 | 作用 |
| --- | --- |
| `qdls_sing` | 点歌：把歌名/链接提交给 QDLS，渲染完自动发语音 |
| `qdls_voices` | 列出可用音色 id |
| `qdls_status` | 查看引擎是否忙碌、队列长度 |

插件只负责“提交 + 轮询 + 发语音”，不参与推理；算力永远在本地的 QDLS 上。

## 安装

1. 把本目录复制到 AstrBot 的 `data/plugins/` 下；
2. 在 QDLS 控制台打开「接入」页 → 「生成配对码」；
3. 到 AstrBot 插件配置里填：

```yaml
gateway_url: http://127.0.0.1:6767   # 跨机填隧道/Tailscale 地址
token: <用配对码换到的 token>
default_voice: taffy
poll_interval: 3
timeout: 300
```

4. 重载插件，QQ 里说“唱首雨爱”即可。

> 配对码是一次性的：每台 AstrBot 用一次，换机器要重新生成。
> 换 token 后旧的在「接入」页撤销即可。

## 跨机部署

插件在云端 AstrBot、QDLS 在你家 PC 时，需要让云端能访问到 PC 的 6767：

- SSH 反向隧道（QDLS 控制台「设置 → 云端接入」可托管）；
- 或 Tailscale/ZeroTier 组网后直接填内网 IP。

协议与鉴权细节见 [docs/api.md](../docs/api.md)。

## 许可

与 QDLS 一致：**禁止商用**，详见 ../LICENSE。
