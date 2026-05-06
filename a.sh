#!/bin/bash
# Toast 通知移到右下角，避开发送按钮
if [ ! -f "static/js/app.js" ]; then
  echo "[错误] 找不到 static/js/app.js"
  exit 1
fi

sed -i.bak "s/position:'fixed',top:20/position:'fixed',bottom:100/" static/js/app.js
rm -f static/js/app.js.bak

echo "✅ Toast 通知位置已移至右下角 (bottom:100px)，不再遮挡发送按钮"
echo "请重启 Flask 并强制刷新 (Ctrl+Shift+R)"