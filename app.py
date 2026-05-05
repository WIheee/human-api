# -*- coding: utf-8 -*-
"""
Human-API 后端主服务 - 终极兼容版
兼容 OpenAI / Anthropic / Gemini 等主流 API 格式
通配路由兜底，什么都接得住
"""

import json
import time
import logging
import os
import uuid
from datetime import datetime
from threading import Lock

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_socketio import SocketIO, emit

import config as cfg

# ==================== 日志配置 ====================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, f"human-api-{datetime.now():%Y-%m-%d}.log"),
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("human-api")

# ==================== Flask 应用初始化 ====================
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = os.urandom(24).hex()

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ==================== 会话存储 ====================
sessions = {}
sessions_lock = Lock()

pending_queue = []
pending_lock = Lock()


def get_pending_count():
    with pending_lock:
        return len(pending_queue)


def add_to_pending(session_id):
    with pending_lock:
        if session_id not in pending_queue:
            pending_queue.append(session_id)


def remove_from_pending(session_id):
    with pending_lock:
        if session_id in pending_queue:
            pending_queue.remove(session_id)


# ==================== 辅助函数 ====================
def generate_session_id():
    return f"sess-{uuid.uuid4().hex[:12]}"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def build_openai_response(session_id, model, content, stream=False):
    if stream:
        return {
            "id": f"chatcmpl-{session_id}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None
            }]
        }
    return {
        "id": f"chatcmpl-{session_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }


def build_claude_response(session_id, model, content):
    """Anthropic Claude 格式"""
    return {
        "id": f"msg_{session_id}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0}
    }


def build_gemini_response(session_id, content):
    """Google Gemini 格式"""
    return {
        "candidates": [{
            "content": {
                "parts": [{"text": content}],
                "role": "model"
            },
            "finishReason": "STOP",
            "index": 0
        }],
        "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}
    }


def serialize_session(session):
    return {
        "id": session["id"],
        "model": session["model"],
        "messages": session["messages"],
        "status": session["status"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "message_count": len(session["messages"]),
    }


# ==================== 鉴权中间层 ====================
def check_api_key():
    required_key = cfg.get("api_key", "")
    if not required_key:
        return None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        if auth_header[7:].strip() == required_key:
            return None

    # Anthropic 风格: x-api-key
    api_key_header = request.headers.get("x-api-key", "")
    if api_key_header == required_key:
        return None

    body_key = None
    if request.is_json:
        body_key = (request.json or {}).get("api_key")

    query_key = request.args.get("api_key")
    query_key_alt = request.args.get("key")  # Gemini 风格

    if body_key == required_key or query_key == required_key or query_key_alt == required_key:
        return None

    return jsonify({"error": {"message": "Invalid API key", "type": "auth_error", "code": "invalid_key"}}), 401


# ==================== 请求体兼容解析 ====================
def normalize_messages(data):
    """
    兼容各种 API 格式的消息提取
    OpenAI:  {"messages": [...]}
    Claude:  {"messages": [...]}  或 {"system": "...", "messages": [...]}
    Gemini:  {"contents": [{"parts":[{"text":"..."}],"role":"user"}]}
    通义:    {"input": {"messages": [...]}}
    """
    messages = []

    # OpenAI / Claude 标准格式
    if "messages" in data:
        messages = data.get("messages", [])

    # Gemini 格式
    elif "contents" in data:
        raw = data.get("contents", [])
        for item in raw:
            role = item.get("role", "user")
            if role == "model":
                role = "assistant"
            parts = item.get("parts", [])
            text = " ".join(p.get("text", "") for p in parts if "text" in p)
            if text:
                messages.append({"role": role, "content": text})

    # 千问格式
    elif "input" in data and "messages" in data["input"]:
        messages = data["input"]["messages"]

    # 裸消息格式: {"prompt": "..."}
    elif "prompt" in data:
        messages = [{"role": "user", "content": data["prompt"]}]

    # 纯文本格式: {"text": "..."}
    elif "text" in data:
        messages = [{"role": "user", "content": data["text"]}]

    # 系统提示词补充
    system_prompt = data.get("system", "") or data.get("system_prompt", "") or data.get("instructions", "")
    if system_prompt and messages:
        if messages[0].get("role") != "system":
            messages.insert(0, {"role": "system", "content": system_prompt})

    return messages


def normalize_model(data):
    """提取模型名"""
    return data.get("model") or data.get("model_id") or data.get("model_name") or "gpt-3.5-turbo"


def normalize_stream(data):
    """识别流式请求"""
    return data.get("stream", False) or data.get("streaming", False)


# ==================== WebUI 页面路由 ====================
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ==================== OpenAI 标准 API ====================
@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat_completions():
    if request.method == "OPTIONS":
        return "", 200

    auth_err = check_api_key()
    if auth_err:
        return auth_err

    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": {"message": "无效的 JSON 格式", "type": "invalid_request"}}), 400

    model = normalize_model(data)
    messages = normalize_messages(data)
    stream = normalize_stream(data)

    if not messages:
        return jsonify({"error": {"message": "messages 不能为空", "type": "invalid_request"}}), 400

    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_query = msg.get("content", "")
            break

    session_id = generate_session_id()
    event = __import__("threading").Event()

    with sessions_lock:
        sessions[session_id] = {
            "id": session_id,
            "model": model,
            "messages": list(messages),
            "status": "waiting",
            "pending_message": messages[-1] if messages else {},
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "request_event": event,
            "reply_content": None,
        }

    add_to_pending(session_id)
    logger.info(f"[新请求] 会话 {session_id} | 模型: {model} | 提问: {user_query[:80]}...")

    socketio.emit("new_request", {
        "session": serialize_session(sessions[session_id]),
        "query_preview": user_query[:200],
    })

    timeout = cfg.get("timeout", 120)
    replied = event.wait(timeout=timeout)

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": {"message": "会话异常", "type": "server_error"}}), 500

        if replied and session["reply_content"]:
            answer = session["reply_content"]
            session["status"] = "replied"
            session["messages"].append({"role": "assistant", "content": answer})
            session["updated_at"] = now_iso()
        else:
            answer = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
            session["status"] = "timeout"
            session["messages"].append({"role": "assistant", "content": answer})
            session["updated_at"] = now_iso()
            logger.warning(f"[超时] 会话 {session_id} 超时未回复")

    remove_from_pending(session_id)
    socketio.emit("session_updated", serialize_session(session))
    logger.info(f"[回复] 会话 {session_id} | 回复: {answer[:80]}...")

    # 根据请求头判断返回格式
    is_claude = "anthropic" in request.headers.get("User-Agent", "").lower() or request.headers.get("x-api-key")
    is_gemini = "google" in request.path.lower()

    if stream:
        def generate():
            chunk_start = {
                "id": f"chatcmpl-{session_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(chunk_start)}\n\n"
            yield f"data: {json.dumps(build_openai_response(session_id, model, answer, stream=True))}\n\n"
            end_chunk = {
                "id": f"chatcmpl-{session_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(end_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return Response(generate(), mimetype="text/event-stream")

    if is_claude:
        return jsonify(build_claude_response(session_id, model, answer))
    elif is_gemini:
        return jsonify(build_gemini_response(session_id, answer))
    else:
        return jsonify(build_openai_response(session_id, model, answer))


# ==================== Anthropic Claude 兼容 ====================
@app.route("/v1/messages", methods=["POST", "OPTIONS"])
def claude_messages():
    """Anthropic Messages API 兼容"""
    if request.method == "OPTIONS":
        return "", 200
    return chat_completions()


# ==================== Google Gemini 兼容 ====================
@app.route("/v1beta/models/<model_name>:generateContent", methods=["POST", "OPTIONS"])
@app.route("/v1/models/<model_name>:generateContent", methods=["POST", "OPTIONS"])
def gemini_generate(model_name):
    """Google Gemini API 兼容"""
    if request.method == "OPTIONS":
        return "", 200
    return chat_completions()


# ==================== 模型列表（多格式） ====================
@app.route("/v1/models", methods=["GET"])
@app.route("/models", methods=["GET"])
def list_models():
    models_data = [
        # OpenAI
        {"id": "gpt-4o", "object": "model"}, {"id": "gpt-4-turbo", "object": "model"},
        {"id": "gpt-4", "object": "model"}, {"id": "gpt-3.5-turbo", "object": "model"},
        {"id": "o1", "object": "model"}, {"id": "o1-mini", "object": "model"},
        # Anthropic
        {"id": "claude-3.5-sonnet", "object": "model"}, {"id": "claude-3-opus", "object": "model"},
        {"id": "claude-3-sonnet", "object": "model"}, {"id": "claude-3-haiku", "object": "model"},
        {"id": "claude-3.7-sonnet", "object": "model"},
        # Google
        {"id": "gemini-2.0-pro", "object": "model"}, {"id": "gemini-2.0-flash", "object": "model"},
        {"id": "gemini-1.5-pro", "object": "model"}, {"id": "gemini-1.5-flash", "object": "model"},
        # DeepSeek
        {"id": "deepseek-chat", "object": "model"}, {"id": "deepseek-r1", "object": "model"},
        {"id": "deepseek-v3", "object": "model"},
        # 通义千问
        {"id": "qwen-max", "object": "model"}, {"id": "qwen-plus", "object": "model"},
        {"id": "qwen-turbo", "object": "model"},
        # Kimi
        {"id": "moonshot-v1-128k", "object": "model"}, {"id": "moonshot-v1-32k", "object": "model"},
        # GLM
        {"id": "glm-4-plus", "object": "model"}, {"id": "glm-4-flash", "object": "model"},
        # Llama
        {"id": "llama-3.3-70b", "object": "model"}, {"id": "llama-3.1-8b", "object": "model"},
    ]
    return jsonify({"object": "list", "data": models_data})


# ==================== 万能通配路由：兼容全世界 ====================
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
def catch_all(path):
    """
    终极兜底路由
    任何打错的路径、奇怪的请求都能被这里接住
    """
    # 1. OPTIONS 预检请求全部放行
    if request.method == 'OPTIONS':
        return '', 200

    # 2. 静态文件处理：如果是 GET 且路径可能指向静态资源
    if request.method == 'GET':
        # 尝试从 static 目录返回文件
        try:
            return send_from_directory('static', path)
        except:
            pass
        # 返回 index.html
        return send_from_directory('static', 'index.html')

    # 3. POST/PUT/PATCH 全部当成聊天请求处理
    if request.method in ('POST', 'PUT', 'PATCH'):
        api_type = detect_api_type(request)
        logger.info(f"[兼容路由] 路径: /{path} | 识别为: {api_type} | IP: {request.remote_addr}")

        auth_err = check_api_key()
        if auth_err:
            return auth_err

        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            return jsonify({"error": {"message": "无效的 JSON 格式", "type": "invalid_request"}}), 400

        # 解析消息
        messages = normalize_messages(data)
        model = normalize_model(data)
        stream = normalize_stream(data)

        if not messages:
            # 最后一次救赎：尝试从 raw data 或 form data 解析
            if request.form:
                text = request.form.get('text') or request.form.get('prompt') or request.form.get('message') or request.form.get('content')
                if text:
                    messages = [{"role": "user", "content": text}]
                    model = model or "gpt-3.5-turbo"
            elif isinstance(data, str) and data.strip():
                messages = [{"role": "user", "content": data.strip()}]
                model = model or "gpt-3.5-turbo"

        if not messages:
            return jsonify({
                "error": {
                    "message": "请求体为空或无法解析。请包含 messages/content/text/prompt 字段",
                    "type": "invalid_request",
                    "supported_formats": ["OpenAI", "Anthropic", "Gemini", "Qwen", "text/plain"]
                }
            }), 400

        return chat_completions()

    # 4. 其他 HTTP 方法
    return jsonify({
        "error": {
            "message": f"不支持的请求方法: {request.method}。本接口仅支持 GET/POST/PUT/DELETE",
            "type": "method_not_allowed"
        }
    }), 405


def detect_api_type(request):
    """根据路径和请求头猜测 API 类型"""
    path = request.path.lower()

    if "anthropic" in path or "claude" in path:
        return "Anthropic"
    if "google" in path or "gemini" in path or "vertex" in path:
        return "Google Gemini"
    if "deepseek" in path:
        return "DeepSeek"
    if "qwen" in path or "tongyi" in path or "dashscope" in path:
        return "Qwen (通义千问)"
    if "moonshot" in path or "kimi" in path:
        return "Kimi (月之暗面)"
    if "glm" in path or "zhipu" in path:
        return "GLM (智谱)"
    if "openai" in path or "chat" in path or "completion" in path:
        return "OpenAI"

    ua = request.headers.get("User-Agent", "").lower()
    if "anthropic" in ua or "claude" in ua:
        return "Anthropic (UA)"
    if "google" in ua or "gemini" in ua:
        return "Gemini (UA)"

    return "OpenAI (默认)"


# ==================== 管理后台 API ====================
@app.route("/api/admin/sessions", methods=["GET"])
def admin_list_sessions():
    with sessions_lock:
        result = [serialize_session(s) for s in sessions.values()]
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"sessions": result, "total": len(result), "pending": get_pending_count()})


@app.route("/api/admin/sessions/<session_id>", methods=["GET"])
def admin_get_session(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify(serialize_session(session))


@app.route("/api/admin/sessions/<session_id>/messages", methods=["GET"])
def admin_get_messages(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify({"messages": session["messages"], "session_id": session_id})


@app.route("/api/admin/reply", methods=["POST"])
def admin_reply():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "请求体为空"}), 400
    except Exception:
        return jsonify({"error": "无效的 JSON 格式"}), 400

    session_id = data.get("session_id", "")
    content = data.get("content", "").strip()

    if not session_id:
        return jsonify({"error": "session_id 不能为空"}), 400
    if not content:
        return jsonify({"error": "回复内容不能为空"}), 400

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        if session["status"] != "waiting":
            return jsonify({"error": f"该会话状态为 {session['status']}，无法回复"}), 400

        session["reply_content"] = content
        session["status"] = "replied"
        session["updated_at"] = now_iso()
        session["request_event"].set()

    remove_from_pending(session_id)
    logger.info(f"[WebUI回复] 会话 {session_id} | 回复: {content[:80]}...")

    socketio.emit("session_updated", serialize_session(session))

    return jsonify({"success": True, "session_id": session_id})


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    with sessions_lock:
        total = len(sessions)
        waiting = sum(1 for s in sessions.values() if s["status"] == "waiting")
        replied = sum(1 for s in sessions.values() if s["status"] == "replied")
        timeout = sum(1 for s in sessions.values() if s["status"] == "timeout")
    return jsonify({
        "total_sessions": total, "waiting": waiting,
        "replied": replied, "timeout": timeout,
        "pending_queue": get_pending_count(),
    })


@app.route("/api/admin/config", methods=["GET"])
def admin_get_config():
    all_cfg = cfg.get_all()
    if all_cfg.get("api_key"):
        all_cfg["api_key"] = "***"
    return jsonify(all_cfg)


@app.route("/api/admin/config", methods=["POST"])
def admin_update_config():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "请求体为空"}), 400
    except Exception:
        return jsonify({"error": "无效的 JSON 格式"}), 400

    allowed_keys = {"api_key", "timeout", "timeout_reply", "host", "port"}
    updates = {k: v for k, v in data.items() if k in allowed_keys and v is not None}

    if "timeout" in updates:
        try:
            updates["timeout"] = int(updates["timeout"])
            if updates["timeout"] < 10:
                return jsonify({"error": "超时时间不能小于 10 秒"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "超时时间必须是整数"}), 400

    if "port" in updates:
        try:
            updates["port"] = int(updates["port"])
        except (ValueError, TypeError):
            return jsonify({"error": "端口必须是整数"}), 400

    cfg.update_config(updates)
    logger.info(f"[配置更新] {list(updates.keys())}")
    return jsonify({"success": True, "updated": list(updates.keys())})


@app.route("/api/admin/clear", methods=["POST"])
def admin_clear_sessions():
    with sessions_lock:
        sessions.clear()
    with pending_lock:
        pending_queue.clear()
    logger.info("[清空] 所有会话已清空")
    return jsonify({"success": True})


# ==================== 健康检查 ====================
@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "alive",
        "sessions": len(sessions),
        "pending": get_pending_count(),
        "timestamp": now_iso(),
        "message": "Human-API 运行正常，兼容 OpenAI / Anthropic / Gemini 等主流格式"
    })


# ==================== WebSocket 事件 ====================
@socketio.on("connect")
def handle_connect():
    logger.info(f"[WS] 客户端连接: {request.sid}")
    with sessions_lock:
        all_sessions = [serialize_session(s) for s in sessions.values()]
    emit("init_data", {
        "sessions": all_sessions,
        "pending_count": get_pending_count(),
        "config": cfg.get_all(),
    })


@socketio.on("disconnect")
def handle_disconnect():
    logger.info(f"[WS] 客户端断开: {request.sid}")


@socketio.on("request_sessions")
def handle_request_sessions():
    with sessions_lock:
        all_sessions = [serialize_session(s) for s in sessions.values()]
    emit("sessions_list", {"sessions": all_sessions})


@socketio.on("request_messages")
def handle_request_messages(data):
    session_id = data.get("session_id", "")
    with sessions_lock:
        session = sessions.get(session_id)
        if session:
            emit("messages_data", {"session_id": session_id, "messages": session["messages"]})
        else:
            emit("error", {"message": "会话不存在"})


# ==================== 入口 ====================
if __name__ == "__main__":
    host = cfg.get("host", "0.0.0.0")
    port = cfg.get("port", 5000)

    print("=" * 60)
    print("  Human Server")
    print("  https://github.com/WIheee/human-api")
    print("  by WIhee")
    print("=" * 60)
    print(f"  WebUI:     http://127.0.0.1:{port}")
    print(f"  API:       http://{host}:{port}/")
    print(f"  超时:      {cfg.get('timeout')}秒")
    api_key = cfg.get("api_key", "")
    print(f"  鉴权:      {'已启用' if api_key else '未启用'}")
    print("=" * 60)

    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)