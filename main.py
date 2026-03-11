from flask import Flask, request, jsonify
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix
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
LOGS_DIR_INVEST = 'chat_logs/invest'
LOGS_DIR_SOTR = 'chat_logs/sotr'
LOGS_DIR_CHECK = 'chat_logs/check'
LOGS_DIR_ISP = 'chat_logs/isp'
LOGS_DIR_IQ = 'chat_logs/iq'
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


def save_message_to_log(filename, data, dir):
    """Сохраняет входящее сообщение в файл с именем chat_id."""
    try:
        if not os.path.exists(dir):
            os.makedirs(dir)

        safe_filename = sanitize_filename(filename)
        file_path = os.path.join(dir, f"{safe_filename}.txt")  #

        with open(file_path, 'a', encoding='cp1251', errors='ignore') as f:
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

      # 200, чтобы отправитель не повторял

    # === 4. Быстрое логирование (минимум времени) ===
    try:

        update_type = data.get('update_type')



        # Сохраняем в файл (асинхронно в идеале, но пока синхронно)
        if update_type == "message_created":
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            chat_id = message.get('recipient', {}).get('chat_id')
            user_id = message.get('recipient', {}).get('user_id')
            sender = message.get('sender', {}).get('name', 'Unknown')
            text = message.get('body', {}).get('text', '')

            #save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_INVEST)
            save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_INVEST)
        
        elif update_type == "message_callback":
            payload = data.get('callback', {}).get('payload',{})
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            user_id = message.get('recipient', {}).get('user_id')
            chat_id = message.get('recipient', {}).get('chat_id')
            save_message_to_log("payload_" + payload + "_chat_id_" + str(chat_id), data, LOGS_DIR_INVEST)
        
        elif update_type == "bot_started":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"start_{chat_id}", data, LOGS_DIR_INVEST)

        elif update_type == "bot_stopped":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"stop_{chat_id}", data, LOGS_DIR_INVEST)

        else:
            logger.info(f"Received unknown update type {update_type}")

        if update_type and chat_id and data:
            logger.info(f"Webhook [{update_type}] from chat:{chat_id}: '{data}...'")

    except Exception as e:
        logger.exception("Error during logging phase:"+ str(e))
        # Не прерываем обработку, если упало логирование

    # === 5. Формирование ответа (БЫСТРО!) ===
    # Вся тяжелая логика должна быть вынесена в очередь задач!
    try:
        if update_type == "bot_started":
            response = {
  "text": "Добро пожаловать! Пожалуйста, выберите город:",
  "attachments": [
    {
      "type": "inline_keyboard",
      "payload": {
        "buttons": [
          [
            {
              "type": "callback",
              "text": "Таганрог",
              "payload": "CITY_TGN"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Армавир",
              "payload": "CITY_ARM"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Казань",
              "payload": "CITY_KZN"
            }
          ]
        ]
      }
    }
  ]
}
        elif update_type == "message_created":
            # Простой шаблон - в реальности здесь должна быть отправка в очередь
            resp_text = '' 
            response = {
                "text": resp_text,
            }
        elif update_type == "message_callback":
            callback = data.get("callback", {})
            pressed_button = callback.get("payload")
            if pressed_button:
                if pressed_button == "CITY_TGN":
                    resp_text = "Вы выбрали Таганрог!"
                    #print("✅ Отправлен ответ: Таганрог")

                elif pressed_button == "CITY_ARM":
                    resp_text = "Вы выбрали Армавир!"
                    #print("✅ Отправлен ответ: Армавир")

                elif pressed_button == "CITY_KZN":
                    resp_text = "Вы выбрали Казань!"
                    #print("✅ Отправлен ответ: Казань")

                else:
                    resp_text= ''
                    #print(f"⚠ Неизвестный код кнопки: {pressed_button}")
            else:

                resp_text = "Произошла ошибка. Попробуйте ещё раз."

            response = {
                "text": resp_text,
            }
            reqv.delete_message_delete_method(message_id, config.BOT_TOKEN_INVEST)
            reqv.send_message(user_id, response, config.BOT_TOKEN_INVEST)
        else:
            resp_text = get_response_text('default.txt', "🤔")
            response = {
                "text": resp_text,
            }

        return jsonify(response), 200

    except Exception as e:
        logger.exception(f"Error generating response:{e}")
        # Возвращаем минимальный ответ, чтобы не ломать протокол
        return jsonify({"text": "⚠️ Произошла ошибка, попробуйте позже"}), 200

@app.route('/webhook1', methods=['GET', 'POST'])
def webhook1():
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

      # 200, чтобы отправитель не повторял

    # === 4. Быстрое логирование (минимум времени) ===
    try:

        update_type = data.get('update_type')



        # Сохраняем в файл (асинхронно в идеале, но пока синхронно)
        if update_type == "message_created":
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            chat_id = message.get('recipient', {}).get('chat_id')
            user_id = message.get('recipient', {}).get('user_id')
            sender = message.get('sender', {}).get('name', 'Unknown')
            text = message.get('body', {}).get('text', '')

            save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_SOTR)

        elif update_type == "message_callback":
            payload = data.get('callback', {}).get('payload',{})
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            user_id = message.get('recipient', {}).get('user_id')
            chat_id = message.get('recipient', {}).get('chat_id')
            save_message_to_log("payload_" + payload + "_chat_id_" + str(chat_id), data, LOGS_DIR_SOTR)

        elif update_type == "bot_started":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"start_{chat_id}", data, LOGS_DIR_SOTR)

        elif update_type == "bot_stopped":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"stop_{chat_id}", data, LOGS_DIR_SOTR)

        else:
            logger.info(f"Received unknown update type {update_type}")

        if update_type and chat_id and data:
            logger.info(f"Webhook [{update_type}] from chat:{chat_id}: '{data}...'")

    except Exception as e:
        logger.exception("Error during logging phase:"+ str(e))
        # Не прерываем обработку, если упало логирование

    # === 5. Формирование ответа (БЫСТРО!) ===
    # Вся тяжелая логика должна быть вынесена в очередь задач!
    try:
        if update_type == "bot_started":
            response = {
  "text": "Добро пожаловать! Пожалуйста, выберите город:",
  "attachments": [
    {
      "type": "inline_keyboard",
      "payload": {
        "buttons": [
          [
            {
              "type": "callback",
              "text": "Таганрог",
              "payload": "CITY_TGN"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Армавир",
              "payload": "CITY_ARM"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Казань",
              "payload": "CITY_KZN"
            }
          ]
        ]
      }
    }
  ]
}
        elif update_type == "message_created":
            # Простой шаблон - в реальности здесь должна быть отправка в очередь
            resp_text = ''
            response = {
                "text": resp_text,
            }

        elif update_type == "message_callback":
            callback = data.get("callback", {})
            pressed_button = callback.get("payload")
            if pressed_button:
                if pressed_button == "CITY_TGN":
                    resp_text = "Вы выбрали Таганрог!"
                    #print("✅ Отправлен ответ: Таганрог")

                elif pressed_button == "CITY_ARM":
                    resp_text = "Вы выбрали Армавир!"
                    #print("✅ Отправлен ответ: Армавир")

                elif pressed_button == "CITY_KZN":
                    resp_text = "Вы выбрали Казань!"
                    #print("✅ Отправлен ответ: Казань")

                else:
                    resp_text= ''
                    #print(f"⚠ Неизвестный код кнопки: {pressed_button}")
            else:

                resp_text = "Произошла ошибка. Попробуйте ещё раз."

            response = {
                "text": resp_text,
            }
            reqv.delete_message_delete_method(message_id, config.BOT_TOKEN_SOTR)
            reqv.send_message(user_id, response, config.BOT_TOKEN_SOTR)
        else:
            resp_text = get_response_text('default.txt', "🤔")
            response = {
                "text": resp_text,
            }

        return jsonify(response), 200

    except Exception as e:
        logger.exception(f"Error generating response:{e}")
        # Возвращаем минимальный ответ, чтобы не ломать протокол
        return jsonify({"text": "⚠️ Произошла ошибка, попробуйте позже"}), 200

@app.route('/webhook2', methods=['GET', 'POST'])
def webhook2():
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

      # 200, чтобы отправитель не повторял

    # === 4. Быстрое логирование (минимум времени) ===
    try:

        update_type = data.get('update_type')



        # Сохраняем в файл (асинхронно в идеале, но пока синхронно)
        if update_type == "message_created":
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            chat_id = message.get('recipient', {}).get('chat_id')
            user_id = message.get('recipient', {}).get('user_id')
            sender = message.get('sender', {}).get('name', 'Unknown')
            text = message.get('body', {}).get('text', '')

            save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_CHECK)

        elif update_type == "message_callback":
            payload = data.get('callback', {}).get('payload',{})
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            user_id = message.get('recipient', {}).get('user_id')
            chat_id = message.get('recipient', {}).get('chat_id')
            save_message_to_log("payload_" + payload + "_chat_id_" + str(chat_id), data, LOGS_DIR_CHECK)

        elif update_type == "bot_started":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"start_{chat_id}", data, LOGS_DIR_CHECK)

        elif update_type == "bot_stopped":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"stop_{chat_id}", data, LOGS_DIR_CHECK)

        else:
            logger.info(f"Received unknown update type {update_type}")

        if update_type and chat_id and data:
            logger.info(f"Webhook [{update_type}] from chat:{chat_id}: '{data}...'")

    except Exception as e:
        logger.exception("Error during logging phase:"+ str(e))
        # Не прерываем обработку, если упало логирование

    # === 5. Формирование ответа (БЫСТРО!) ===
    # Вся тяжелая логика должна быть вынесена в очередь задач!
    try:
        if update_type == "bot_started":
            response = {
  "text": "Добро пожаловать! Пожалуйста, выберите город:",
  "attachments": [
    {
      "type": "inline_keyboard",
      "payload": {
        "buttons": [
          [
            {
              "type": "callback",
              "text": "Таганрог",
              "payload": "CITY_TGN"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Армавир",
              "payload": "CITY_ARM"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Казань",
              "payload": "CITY_KZN"
            }
          ]
        ]
      }
    }
  ]
}
        elif update_type == "message_created":
            # Простой шаблон - в реальности здесь должна быть отправка в очередь
            resp_text = ''
            response = {
                "text": resp_text,
            }

        elif update_type == "message_callback":
            callback = data.get("callback", {})
            pressed_button = callback.get("payload")
            if pressed_button:
                if pressed_button == "CITY_TGN":
                    resp_text = "Вы выбрали Таганрог!"
                    #print("✅ Отправлен ответ: Таганрог")

                elif pressed_button == "CITY_ARM":
                    resp_text = "Вы выбрали Армавир!"
                    #print("✅ Отправлен ответ: Армавир")

                elif pressed_button == "CITY_KZN":
                    resp_text = "Вы выбрали Казань!"
                    #print("✅ Отправлен ответ: Казань")

                else:
                    resp_text= ''
                    #print(f"⚠ Неизвестный код кнопки: {pressed_button}")
            else:

                resp_text = "Произошла ошибка. Попробуйте ещё раз."

            response = {
                "text": resp_text,
            }
            reqv.delete_message_delete_method(message_id, config.BOT_TOKEN_CHECK)
            reqv.send_message(user_id, response, config.BOT_TOKEN_CHECK)
        else:
            resp_text = get_response_text('default.txt', "🤔")
            response = {
                "text": resp_text,
            }

        return jsonify(response), 200

    except Exception as e:
        logger.exception(f"Error generating response:{e}")
        # Возвращаем минимальный ответ, чтобы не ломать протокол
        return jsonify({"text": "⚠️ Произошла ошибка, попробуйте позже"}), 200

@app.route('/webhook3', methods=['GET', 'POST'])
def webhook3():
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

      # 200, чтобы отправитель не повторял

    # === 4. Быстрое логирование (минимум времени) ===
    try:

        update_type = data.get('update_type')



        # Сохраняем в файл (асинхронно в идеале, но пока синхронно)
        if update_type == "message_created":
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            chat_id = message.get('recipient', {}).get('chat_id')
            user_id = message.get('recipient', {}).get('user_id')
            sender = message.get('sender', {}).get('name', 'Unknown')
            text = message.get('body', {}).get('text', '')

            save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_ISP)

        elif update_type == "message_callback":
            payload = data.get('callback', {}).get('payload',{})
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            user_id = message.get('recipient', {}).get('user_id')
            chat_id = message.get('recipient', {}).get('chat_id')
            save_message_to_log("payload_" + payload + "_chat_id_" + str(chat_id), data, LOGS_DIR_ISP)

        elif update_type == "bot_started":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"start_{chat_id}", data, LOGS_DIR_ISP)

        elif update_type == "bot_stopped":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"stop_{chat_id}", data, LOGS_DIR_ISP)

        else:
            logger.info(f"Received unknown update type {update_type}")

        if update_type and chat_id and data:
            logger.info(f"Webhook [{update_type}] from chat:{chat_id}: '{data}...'")

    except Exception as e:
        logger.exception("Error during logging phase:"+ str(e))
        # Не прерываем обработку, если упало логирование

    # === 5. Формирование ответа (БЫСТРО!) ===
    # Вся тяжелая логика должна быть вынесена в очередь задач!
    try:
        if update_type == "bot_started":
            response = {
  "text": "Добро пожаловать! Пожалуйста, выберите город:",
  "attachments": [
    {
      "type": "inline_keyboard",
      "payload": {
        "buttons": [
          [
            {
              "type": "callback",
              "text": "Таганрог",
              "payload": "CITY_TGN"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Армавир",
              "payload": "CITY_ARM"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Казань",
              "payload": "CITY_KZN"
            }
          ]
        ]
      }
    }
  ]
}
        elif update_type == "message_created":
            # Простой шаблон - в реальности здесь должна быть отправка в очередь
            resp_text = ''
            response = {
                "text": resp_text,
            }

        elif update_type == "message_callback":
            callback = data.get("callback", {})
            pressed_button = callback.get("payload")
            if pressed_button:
                if pressed_button == "CITY_TGN":
                    resp_text = "Вы выбрали Таганрог!"
                    #print("✅ Отправлен ответ: Таганрог")

                elif pressed_button == "CITY_ARM":
                    resp_text = "Вы выбрали Армавир!"
                    #print("✅ Отправлен ответ: Армавир")

                elif pressed_button == "CITY_KZN":
                    resp_text = "Вы выбрали Казань!"
                    #print("✅ Отправлен ответ: Казань")

                else:
                    resp_text= ''
                    #print(f"⚠ Неизвестный код кнопки: {pressed_button}")
            else:

                resp_text = "Произошла ошибка. Попробуйте ещё раз."

            response = {
                "text": resp_text,
            }
            reqv.delete_message_delete_method(message_id, config.BOT_TOKEN_ISP)
            reqv.send_message(user_id, response, config.BOT_TOKEN_ISP)
        else:
            resp_text = get_response_text('default.txt', "🤔")
            response = {
                "text": resp_text,
            }

        return jsonify(response), 200

    except Exception as e:
        logger.exception(f"Error generating response:{e}")
        # Возвращаем минимальный ответ, чтобы не ломать протокол
        return jsonify({"text": "⚠️ Произошла ошибка, попробуйте позже"}), 200

@app.route('/webhook4', methods=['GET', 'POST'])
def webhook4():
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

      # 200, чтобы отправитель не повторял

    # === 4. Быстрое логирование (минимум времени) ===
    try:

        update_type = data.get('update_type')



        # Сохраняем в файл (асинхронно в идеале, но пока синхронно)
        if update_type == "message_created":
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            chat_id = message.get('recipient', {}).get('chat_id')
            user_id = message.get('recipient', {}).get('user_id')
            sender = message.get('sender', {}).get('name', 'Unknown')
            text = message.get('body', {}).get('text', '')

            save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_IQ)

        elif update_type == "message_callback":
            payload = data.get('callback', {}).get('payload',{})
            message = data.get('message', {})
            message_id = data.get('message', {}).get('body', {}).get('mid')
            user_id = message.get('recipient', {}).get('user_id')
            chat_id = message.get('recipient', {}).get('chat_id')
            save_message_to_log("payload_" + payload + "_chat_id_" + str(chat_id), data, LOGS_DIR_IQ)

        elif update_type == "bot_started":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"start_{chat_id}", data, LOGS_DIR_IQ)

        elif update_type == "bot_stopped":
            chat_id = data.get('chat_id', {})

            save_message_to_log(f"stop_{chat_id}", data, LOGS_DIR_IQ)

        else:
            logger.info(f"Received unknown update type {update_type}")

        if update_type and chat_id and data:
            logger.info(f"Webhook [{update_type}] from chat:{chat_id}: '{data}...'")

    except Exception as e:
        logger.exception("Error during logging phase:"+ str(e))
        # Не прерываем обработку, если упало логирование

    # === 5. Формирование ответа (БЫСТРО!) ===
    # Вся тяжелая логика должна быть вынесена в очередь задач!
    try:
        if update_type == "bot_started":
            response = {
  "text": "Добро пожаловать! Пожалуйста, выберите город:",
  "attachments": [
    {
      "type": "inline_keyboard",
      "payload": {
        "buttons": [
          [
            {
              "type": "callback",
              "text": "Таганрог",
              "payload": "CITY_TGN"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Армавир",
              "payload": "CITY_ARM"
            }
          ],
          [
            {
              "type": "callback",
              "text": "Казань",
              "payload": "CITY_KZN"
            }
          ]
        ]
      }
    }
  ]
}
        elif update_type == "message_created":
            # Простой шаблон - в реальности здесь должна быть отправка в очередь
            resp_text = ''
            response = {
                "text": resp_text,
            }

        elif update_type == "message_callback":
            callback = data.get("callback", {})
            pressed_button = callback.get("payload")
            if pressed_button:
                if pressed_button == "CITY_TGN":
                    resp_text = "Вы выбрали Таганрог!"
                    #print("✅ Отправлен ответ: Таганрог")

                elif pressed_button == "CITY_ARM":
                    resp_text = "Вы выбрали Армавир!"
                    #print("✅ Отправлен ответ: Армавир")

                elif pressed_button == "CITY_KZN":
                    resp_text = "Вы выбрали Казань!"
                    #print("✅ Отправлен ответ: Казань")

                else:
                    resp_text= ''
                    #print(f"⚠ Неизвестный код кнопки: {pressed_button}")
            else:

                resp_text = "Произошла ошибка. Попробуйте ещё раз."

            response = {
                "text": resp_text,
            }
            reqv.delete_message_delete_method(message_id, config.BOT_TOKEN_IQ)
            reqv.send_message(user_id, response, config.BOT_TOKEN_IQ)
        else:
            resp_text = get_response_text('default.txt', "🤔")
            response = {
                "text": resp_text,
            }

        return jsonify(response), 200

    except Exception as e:
        logger.exception(f"Error generating response:{e}")
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