# AMD 显卡方案：WSL2 + ROCm

> QDLS 的推理后端是 PyTorch CUDA。AMD 卡不能直接用 CUDA，官方可行路线是
> **在 WSL2 里用 ROCm 版 PyTorch 跑网关**，Windows 这边只跑控制台。

## 适用条件（先自查，不满足就别折腾）

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 11 22H2+（Win10 需 21H2+ 并手动装 WSL2） |
| 显卡 | AMD 独显 RDNA2/RDNA3：RX 6600/6700/6800/6900、RX 7600/7700/7800/7900 |
| 显存 | ≥ 8 GB 推荐（6 GB 能跑，片段要压到 15 秒） |
| 内存 | 16 GB 以上（WSL 会占一部分） |
| 磁盘 | 50 GB 空闲（ROCm 很大） |
| 不支持 | 核显（Vega 集显 / Radeon Graphics）、RX 5000 系、4 GB 卡 |

## 步骤

### 1. Windows 侧：装 WSL2

以**管理员**身份打开 PowerShell：

```powershell
wsl --install -d Ubuntu-22.04
```

重启，首次进入 Ubuntu 时设置用户名密码。

> 也可以用同目录的 `install_wsl.ps1`，它会检查/开启虚拟化并执行上面的命令。

### 2. WSL 内：装 ROCm

进入 Ubuntu 终端，把 `setup_rocm.sh` 复制进去执行：

```bash
bash /mnt/c/path/to/QDLS/tools/amd/setup_rocm.sh
```

脚本会做：加 AMD 源 → `amdgpu-install --usecase=rocm` → 把当前用户加入 `render/video` 组 →
装 Python 3.11 与 venv → 建虚拟环境 `~/qdls-venv`。

退出重登一次，让组权限生效，然后验证：

```bash
rocm-smi          # 应能看到你的显卡
rocminfo | grep gfx
```

### 3. 装 PyTorch（ROCm 版）

```bash
source ~/qdls-venv/bin/activate
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/rocm6.2
pip install -r /mnt/c/DS/QDLS/requirements.txt   # 会自动跳过已装的 torch
```

如果 `rocminfo` 报的 gfx 型号不被 ROCm 6.2 支持（比如 gfx1103 核显），可以试：

```bash
export HSA_OVERRIDE_GFX_VERSION=10.3.0
```

### 4. 在 WSL 里跑网关

```bash
cd /mnt/c/DS/QDLS
source ~/qdls-venv/bin/activate
python -u qdls/gateway.py --port 6767 --host 127.0.0.1
```

WSL2 会把 `127.0.0.1:6767` 转发到 Windows，所以 **Windows 上的 QDLS 控制台不用改任何东西**，
它连 `127.0.0.1:6767` 就能用。

### 5. 让控制台不要自己拉网关

打开 `settings.json`，把 `paths.python` 改成 WSL 里的启动脚本，或者简单点：
控制台「设置 → 端口」保持不变，但**不要点“启动”**（网关已经在 WSL 里跑着了）。
面板的看门狗发现端口已占用会跳过启动，只托管隧道。

## 预期性能

| 显卡 | 30 秒片段耗时（40 步） |
| --- | --- |
| RX 7900 XTX | 约 40~60 秒 |
| RX 7800 XT / 6800 XT | 约 60~90 秒 |
| RX 7600 / 6600 | 约 90~150 秒 |

约为同档 NVIDIA 的 60%~80%。

## 常见问题

**`rocm-smi` 找不到设备**：多见于核显或未加组权限，重登一次再试。

**OOM**：8 GB 卡把 `settings.json` 里 `clip_max` 改成 20，`fp16` 设 true。

**WSL 里看不到 /mnt/c**：`sudo mount -t drvfs C: /mnt/c`。

**速度极慢且有 CPU 回退**：说明某个算子在 ROCm 上没实现，检查 `rocminfo` 的 gfx 与 torch 版本是否匹配。

## ZLUDA 呢？

不推荐。它把 CUDA 翻译到 HIP，但 **cuDNN 覆盖不全**（我们的 Demucs/Whisper/扩散模型大量用卷积），
而且需要特定 patched torch，版本被锁死。只适合愿意自己修环境的实验者，不作为支持路径。
