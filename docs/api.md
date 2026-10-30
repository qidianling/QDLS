# QDLS 对接协议（给插件作者）

QDLS 控制台（默认 `http://127.0.0.1:6768`）同时是一个 REST API。所有 `/api/v1/*`
需要 `Authorization: Bearer <token>`；配对接口除外。

## 1. 配对算法

```
控制台                          插件（AstrBot）
  │  生成 32 位 URL-safe 码（10 分钟、一次性）
  │  code ───────────────────────▶ POST /api/v1/pair/claim
  │                                   {code, bot_name, platform}
  │◀──────────────────────────────  {token, gateway}
  │  记录 token 的 sha256 + bot 名
  │                                  后续请求带 Bearer token
```

要点：

- 码用 `secrets.randbelow(10**6)` 生成，补零 6 位；
- 服务端只存 **token 的 sha256**，不存明文；比较用 `hmac.compare_digest`（防时序攻击）；
- 码用过即置 `used=true`，过期（10 分钟）自动清理；
- 每个 token 记录 `created / last_seen / calls`，控制台可随时撤销；
- 撤销后插件所有请求 401，需要重新配对。

## 2. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/pair/claim` | `{code, bot_name, platform}` → `{ok, token, gateway}`；**无需 token** |
| GET | `/api/v1/health` | `{ok, queue, busy, voices[]}` |
| GET | `/api/v1/voices` | `{voices:[{id,name}]}` |
| POST | `/api/v1/register` | `{bot_name, platform}`，更新“已接入”列表 |
| POST | `/api/v1/sing` | `{song, voice?, requester?}` → `{job_id}` |
| GET | `/api/v1/jobs/{id}` | `{job:{state, stage, song, elapsed, error}}` |
| GET | `/api/v1/jobs/{id}/audio` | 返回 mp3（`Content-Disposition: attachment`） |
| POST | `/api/v1/jobs/{id}/cancel` | 排队中的任务可取消 |

`state` 取值：`queued → running → done`，异常为 `error`，取消为 `cancelled`。

## 3. 任务模型

- 单 GPU 串行：同一时刻只跑一首，其余进队列（`queue` 字段可查）；
- 提交即返回 `job_id`，客户端轮询 `GET /jobs/{id}`（建议 2~3 秒）；
- 完成后才可下载音频，未完成返回 `409`；
- 引擎内部阶段（分离/音高/转换/润色）会写入 `job_state.json`，控制台展示的就是它。

## 4. 一个最小客户端

```python
import httpx, time

base = "http://127.0.0.1:6767"
h = {"Authorization": "Bearer " + token}

job = httpx.post(base + "/api/v1/sing", json={"song": "雨爱 杨丞琳"}, headers=h).json()
while True:
    time.sleep(3)
    st = httpx.get(base + f"/api/v1/jobs/{job['job_id']}", headers=h).json()["job"]
    if st["state"] == "done":
        break
    if st["state"] in ("error", "cancelled"):
        raise RuntimeError(st["error"])

audio = httpx.get(base + f"/api/v1/jobs/{job['job_id']}/audio", headers=h).content
open("out.mp3", "wb").write(audio)
```

## 5. 错误码

| 码 | 含义 |
| --- | --- |
| 400 | 参数缺失（如 `song` 为空） |
| 401 | token 无效或已撤销 |
| 403 | 配对码无效/过期/已用 |
| 404 | 任务不存在 |
| 409 | 音频还没生成完 |

## 6. 安全边界

- API 只监听 `127.0.0.1`，跨机请走 SSH 隧道或 Tailscale，不要直接暴露公网；
- token 无过期时间，但可撤销；泄漏后立即在「接入」页撤销并重新配对；
- 配对码只在控制台本地生成，不经过网络。
