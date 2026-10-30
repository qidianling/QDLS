#!/usr/bin/env bash
# QDLS / AMD: install ROCm + a Python 3.11 venv inside WSL2 (Ubuntu 22.04).
# Usage:  bash setup_rocm.sh
set -e

echo "== QDLS / AMD: WSL2 + ROCm 安装 =="

if ! grep -qi ubuntu /etc/os-release; then
  echo "此脚本面向 Ubuntu 22.04（WSL2）。当前系统不是 Ubuntu，请手动安装 ROCm。"
fi

echo "[1/6] 基础依赖"
sudo apt-get update
sudo apt-get install -y wget gnupg2 curl python3.11 python3.11-venv python3.11-dev python3-pip

echo "[2/6] 添加 AMD ROCm 源（6.2）"
cd /tmp
wget -q https://repo.radeon.com/amdgpu-install/6.2/ubuntu/jammy/amdgpu-install_6.2.60202-1_all.deb -O amdgpu-install.deb
sudo apt-get install -y ./amdgpu-install.deb

echo "[3/6] 安装 ROCm（这一步会下载约 3-6 GB）"
sudo amdgpu-install -y --usecase=rocm --no-dkms

echo "[4/6] 用户组"
sudo usermod -aG render,video "$USER"

echo "[5/6] 创建虚拟环境"
python3.11 -m venv "$HOME/qdls-venv"
# shellcheck disable=SC1091
source "$HOME/qdls-venv/bin/activate"
pip install --upgrade pip
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/rocm6.2

echo "[6/6] 检查"
rocm-smi || true
rocminfo | grep -m3 gfx || true

cat <<'EOF'

完成。若 rocm-smi 报权限错误，请退出 WSL 重新进入（让 render/video 组生效）。

下一步：
  source ~/qdls-venv/bin/activate
  pip install -r /mnt/c/DS/QDLS/requirements.txt
  cd /mnt/c/DS/QDLS
  python -u qdls/gateway.py --port 6767 --host 127.0.0.1

如果 rocminfo 显示的 gfx 不被支持，试试：
  export HSA_OVERRIDE_GFX_VERSION=10.3.0
EOF
