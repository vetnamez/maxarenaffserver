from flask import Flask, request, jsonify
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
import logging.handlers
import config
import os
import json
import re
import hmac
import hashlib
import time
from datetime import datetime
import reqv_to_bot as reqv

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
# Создаем папку для логов
LOG_DIR = 'logs'
os.makedirs(LOG_DIR, exist_ok=True)

# Ротация логов: 10 файлов по 5 МБ каждый
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, 'app.log'),
    maxBytes=5 * 1024 * 1024,
    backupCount=10,
    encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== ПРОКСИ-НАСТРОЙКИ (для Nginx) ====================
# Доверяем заголовкам от обратного прокси
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_prefix=1)

# ==================== КОНФИГУРАЦИЯ ====================
LOGS_DIR = 'chat_logs'
#os.makedirs(LOGS_DIR, exist_ok=True)

# Простой in-memory кэш для идемпотентности (в продакшене лучше Redis!)
# Формат: {message_id: timestamp}
_processed_messages = {}
IDEMPOTENCY_TTL = 3600  # секунд


def cleanup_old_ids():
    """Удаляет старые записи из кэша идемпотентности."""
    now = time.time()
    old_ids = [mid for mid, ts in _processed_messages.items() if now - ts > IDEMPOTENCY_TTL]
    for mid in old_ids:
        del _processed_messages[mid]
    if old_ids:
        logger.debug(f"Cleaned up {len(old_ids)} old message IDs")


def sanitize_filename(name):
    """Оставляет в имени файла только безопасные символы (цифры и буквы)."""
    if name is None:
        return "unknown"
    # Разрешаем только цифры, буквы, дефис и подчеркивание
    return re.sub(r'[^\w\-]', '', str(name))



def is_message_processed(message_id):
    """Проверяет, обрабатывалось ли уже это сообщение (идемпотентность)."""
    if not message_id:
        return False
    cleanup_old_ids()
    if message_id in _processed_messages:
        return True
    _processed_messages[message_id] = time.time()
    return False


def get_response_text(filename, default_text):
    """Читает текст ответа из файла. Если файла нет, возвращает default_text."""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='cp1251') as f:
                return f.read().strip()
    except Exception as e:
        logger.error(f"Error reading file {filename}: {e}")
    return default_text


def save_message_to_log(filename, data):
    """Сохраняет входящее сообщение в файл с именем chat_id."""
    try:
        if not os.path.exists(LOGS_DIR):
            os.makedirs(LOGS_DIR)

        safe_filename = sanitize_filename(filename)
        file_path = os.path.join(LOGS_DIR, f"{safe_filename}.txt")  # .jsonl - стандарт для логов

        with open(file_path, 'a', encoding='cp1251') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    except Exception as e:
        logger.exception(f"Error saving log for {filename}: {e}")


def verify_signature(payload, header_signature, secret):
    """
    Проверяет HMAC-подпись вебхука.
    Возвращает True, если подпись верна.
    """
    if not secret or not header_signature:
        return False

    # Ожидаем формат: sha256=hexdigest или просто hexdigest
    if '=' in header_signature:
        header_signature = header_signature.split('=')[1]

    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, header_signature)

def create_message_from_json(json_file):
    with open(json_file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload

# ==================== ВЕБХУК ЛОГИКА ====================

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Основной webhook endpoint для MaxBot."""

    # GET - health check для балансировщика
    if request.method == 'GET':
        return jsonify({"status": "webhook_active"}), 200

    # === 1. Проверка подписи (БЕЗОПАСНОСТЬ) ===
    if config.SECRET_KEY:
        signature = request.headers.get('X-Hub-Signature-256') or request.headers.get('X-Hub-Signature')
        #if not verify_signature(request.data, signature, config.SECRET_KEY):
           # logger.warning(f"Invalid signature from {request.remote_addr}")
          #  return jsonify({"error": "Forbidden"}), 403

    # === 2. Валидация входных данных ===
    if not request.is_json:
        logger.warning("Received non-JSON request")
        return jsonify({"error": "Content-Type must be application/json"}), 400

    try:
        data = request.get_json()
    except Exception as e:
        logger.error(f"Failed to parse JSON: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    # === 3. Идемпотентность (защита от дублей) ===
    message_id = data.get('message', {}).get('body', {}).get('mid')
    if message_id and is_message_processed(message_id):
        logger.info(f"Duplicate message {message_id}, skipping")
        return jsonify({"status": "duplicate_ignored"}), 200  # 200, чтобы отправитель не повторял

    # === 4. Быстрое логирование (минимум времени) ===
    try:
        message = data.get('message', {})
        chat_id = message.get('recipient', {}).get('chat_id')
        sender = message.get('sender', {}).get('name', 'Unknown')
        text = message.get('body', {}).get('text', '')
        update_type = data.get('update_type')

        logger.info(f"Webhook [{update_type}] from {sender} (chat:{chat_id}): '{text[:100]}...'")

        # Сохраняем в файл (асинхронно в идеале, но пока синхронно)
        if message_id:
            save_message_to_log(message_id, message)

    except Exception as e:
        logger.exception("Error during logging phase:"+ str(e))
        # Не прерываем обработку, если упало логирование

    # === 5. Формирование ответа (БЫСТРО!) ===
    # Вся тяжелая логика должна быть вынесена в очередь задач!
    try:
        if update_type == "bot_started":
            #resp_text = get_response_text('welcome.txt', "👋 Добро пожаловать!")
            #reqv.send_message(chat_id, config.BOT_TOKEN, reqv.load_payload('welcome_buttons.json'))
            #resp_text = create_message_from_json('welcome_buttons.json')
            response = reqv.load_payload('welcome_buttons.json')
        elif update_type == "message_created":
            # Простой шаблон - в реальности здесь должна быть отправка в очередь
            resp_text = f"✅ Получено: {text}, ℹ️ chat_id: {chat_id}"
            response = {
                "text": resp_text,
            }
        else:
            resp_text = get_response_text('default.txt', "🤔")
            response = {
                "text": resp_text,
            }

        return jsonify(response), 200

    except Exception as e:
        logger.exception("Error generating response")
        # Возвращаем минимальный ответ, чтобы не ломать протокол
        return jsonify({"text": "⚠️ Произошла ошибка, попробуйте позже"}), 200


@app.route('/health', methods=['GET'])
def health_check():
    """Эндпоинт для проверки работоспособности (для Nginx/мониторинга)."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "processed_cache_size": len(_processed_messages)
    }), 200


# ==================== ЗАПУСК ====================

def run_production():
    """Запуск через Waitress для продакшена."""
    host = config.HOST  # Только localhost! SSL терминирует Nginx
    port = config.PORT
    threads = config.WAITRESS_THREADS

    logger.info(f"Starting Waitress server on {host}:{port} with {threads} threads")
    logger.info("⚠️  SSL should be handled by Nginx reverse proxy")

    # Waitress не поддерживает SSL напрямую - используем HTTP за Nginx
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        channel_timeout=30,  # Таймаут канала (сек)
        connection_limit=100,  # Макс соединений
        recv_bytes=10485760,  # Макс размер тела запроса (10 MB)
    )


if __name__ == '__main__':
    # Для отладки можно запускать напрямую, но в продакшене - только через run_production()
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--dev':
        logger.warning("⚠️  Running in DEVELOPMENT mode with app.run()")
        app.run(host='0.0.0.0', port=80, debug=True)
    else:
        run_production()