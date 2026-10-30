# 安全说明

QDLS 是**单用户本地应用**：控制台与网关只监听 `127.0.0.1`，默认不暴露公网。
所有“远端”场景都依赖 SSH 隧道或组网 VPN，鉴权在那一层。

## 已经做的防护

| 面 | 措施 |
| --- | --- |
| API 鉴权 | `/api/v1/*` 需要 `Bearer token`；无 token 或错误 token → 401 |
| Token 存储 | `access.json` 里**只存 sha256**，不存明文；比较用 `hmac.compare_digest` |
| Token 展示 | `/api/access/state` 会剔除 hash 字段，页面上看不到凭据材料 |
| 配对码 | 6 位、10 分钟、一次性；每分钟最多 10 次尝试（防暴力猜码） |
| CSRF / DNS rebinding | 所有 `/api/*` 校验 `Origin` 与 `Sec-Fetch-Site`，跨站直接 403 |
| 点击劫持 | `X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer` |
| 任意代码执行 | `POST /api/settings` 校验 `paths.python` 必须是 `python*.exe`，`paths.repo` 必须含 `preprocess/tools`，`paths.pretrained` 必须含 `model-svc.pt` |
| 参数注入 | 数值参数（pitch/cfg/steps/seed/clip）强制 `int/float` 校验 |
| Token 泄漏面 | `?token=` 仅允许用于 `/audio`（给 `<audio>` 标签用），其他接口必须走 Header |
| 路径遍历 | 音频下载只按内部 job id / 历史索引取文件，不接受外部路径 |

验证记录：

```
GET  /api/status  Origin: https://evil.example     → 403 cross-origin blocked
POST /api/settings {paths.python: .../cmd.exe}     → 400 必须是 python*.exe
POST /api/settings {paths.repo: C:/Windows}        → 400 不是有效的 SoulX-Singer 目录
GET  /api/v1/health?token=abc                      → 401（query token 仅限 audio）
```

## 仍然存在的风险（知情选择）

1. **SSRF**：`/convert` 接受歌名或 URL，理论上可让引擎去请求内网地址。缓解：网关只听 loopback，且调用方是你自己的 bot；**不要把网关暴露到公网**。
2. **本机恶意进程**：能访问 `127.0.0.1:6768` 的本机程序不受 token 保护（面板按“本机可信”设计）。多用户共用一台机器时请隔离账户。
3. **配置里的 token**：AstrBot 插件的 token 是明文存在插件配置里的，这是 AstrBot 的通用做法；注意备份/截图时别泄露。
4. **模型与依赖供应链**：模型来自 ModelScope、依赖来自 PyPI 镜像，均未做签名校验；只从官方源下载。
5. **隧道**：SSH 隧道使用 `StrictHostKeyChecking=no` + 自带 known_hosts，首次连接存在中间人风险；高安全场景建议预先写入服务器指纹。
6. **无 TLS**：loopback 不加 TLS 是合理的；若你把它挂到反向代理上，务必配 HTTPS + 额外鉴权。
7. **`access.json` 文件权限**：默认继承目录权限，同机其他用户理论上可读 hash（但无明文 token）。

## 给使用者的建议

- 只监听 `127.0.0.1`；需要跨机就用 SSH 隧道或 Tailscale，不要端口映射；
- token 泄漏 → 「接入」页撤销，插件重新配对；
- 不要用管理员身份运行面板（没必要，且扩大影响面）；
- 定期看一眼「已接入」列表，陌生 bot 立即撤销。

## 后续可以加

- 面板自身的本地 CSRF token（目前靠 Origin 校验）；
- `/convert` 的 URL 白名单（只允许网易云/QQ 音乐域名），彻底关掉 SSRF；
- `access.json` 收紧 ACL 到当前用户；
- 模型下载的 sha256 校验清单。
