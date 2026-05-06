#!/bin/bash
# ==============================================
# Human-API WebSocket + 密钥持久化 (纯 Shell)
# ==============================================
set -e

if [ ! -f "app.py" ] || [ ! -f "static/index.html" ]; then
  echo "[错误] 请在包含 app.py 和 static/index.html 的目录运行"
  exit 1
fi

# ---- 1. 修复 SECRET_KEY (sed 删除旧行，插入新逻辑) ----
echo "[1/3] 修复 app.py 密钥持久化..."

# 使用 sed 找到包含 app.config["SECRET_KEY"] = os.urandom 的行并替换为持久化代码块
# MacOS 与 GNU sed 略有差异，这里使用兼容写法
sed -i.bak '
/app\.config\["SECRET_KEY"\] = os\.urandom(24)\.hex()/ {
    c\
# 持久化密钥：首次运行时生成并保存到 data/secret_key，重启不失效\
key_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")\
os.makedirs(key_dir, exist_ok=True)\
key_file = os.path.join(key_dir, "secret_key")\
if not os.path.exists(key_file):\
    with open(key_file, "w") as f:\
        f.write(os.urandom(24).hex())\
with open(key_file, "r") as f:\
    app.config["SECRET_KEY"] = f.read().strip()
}
' app.py

# 删除备份文件 (Mac 下 sed -i 必须给后缀)
rm -f app.py.bak

echo "  ✅ SECRET_KEY 持久化完成"

# ---- 2. 启用 WebSocket 优先 ----
echo "[2/3] 切换前端为 WebSocket 优先..."
sed -i "s/transports:\['polling'\]/transports:['websocket', 'polling']/" static/index.html
echo "  ✅ 前端连接优先级已更新"

# ---- 3. 结束提示 ----
echo "============================================"
echo "  升级完成！"
echo "  - Flask SECRET_KEY 已持久化 (重启不会丢失)"
echo "  - 前端使用 WebSocket 实时推送"
echo "============================================"
echo "请重启服务: python app.py"