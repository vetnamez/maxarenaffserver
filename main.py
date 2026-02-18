from flask import Flask, request, jsonify
import logging
import config
import os
import json
import re

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Папка для хранения логов переписки
LOGS_DIR = 'chat_logs'


def sanitize_filename(name):
    """Оставляет в имени файла только безопасные символы (цифры и буквы)."""
    if name is None:
        return "unknown"
    # Разрешаем только цифры, буквы, дефис и подчеркивание
    return re.sub(r'[^\w\-]', '', str(name))


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
        file_path = os.path.join(LOGS_DIR, f"{safe_filename}.txt")

        # Записываем JSON в файл с новой строки (режим добавления)
        with open(file_path, 'a', encoding='cp1251') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    except Exception as e:
        logger.exception(f"Error saving log for chat {filename}: {e}")


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Основной webhook endpoint для MaxBot."""

    if request.method == 'GET':
        return jsonify({
            "status": "Webhook is active",
            "message": "Send POST requests to interact with the bot"
        }), 200

    # Проверяем секрет (если используется)
    received_secret = request.headers.get("X-Hub-Signature")
    if hasattr(config, 'SECRET_KEY') and config.SECRET_KEY and received_secret and received_secret != config.SECRET_KEY:
        logger.warning("Invalid secret received!")
        return jsonify({"error": "Invalid secret"}), 403

    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json()
        message = data.get('message', {})
        chat_id = message.get('recipient', {}).get('chat_id')
        text = message.get('body', {}).get('text', '')
        sender = message.get('sender', {}).get('name', 'Пользователь')
        update_type = data.get('update_type')
        message_text = message.get('body', {}).get('text')
        message_id = message.get('body', {}).get('mid')
        message_time = data.get('timestamp')
        message_sendername = message.get('sender', {}).get('name')
        logger.info(f'Received update: {data}')
        logger.info(f"=== Входящее сообщение ===")
        logger.info(f"Тип: {data.get('update_type')}")
        logger.info(f"Чат ID: {chat_id}")
        logger.info(f"От: {message_sendername}")
        logger.info(f"Текст: '{message_text}'")
        logger.info(f"mid: '{message_id}'")
        logger.info(f"Время: {message_time}")
        # 1. Сохраняем входящее сообщение в файл
        # Пытаемся получить chat_id из новой структуры (recipient.chat_id)

        if message_id:
            save_message_to_log(message_id, message)
        else:
            logger.warning("mID not found in request, skipping log file save.")


        # Выбираем текст ответа в зависимости от типа события, читая из файлов
        if update_type == "bot_started":
            resp_text = get_response_text('welcome.txt', "Добро пожаловать! (файл welcome.txt не найден)")
        elif update_type == "message_created":
            # Можно использовать один файл response.txt для всех сообщений
            # Или шаблон, где {user_text} будет заменен
            #template = get_response_text('response.txt', "Вы сказали: {message_text}")
            #resp_text = template.format(text=message_text)
            resp_text = "Вы сказали: " + str(message_text) + ', ' + "ваш chat_id: "+str(chat_id)
        else:
            resp_text = get_response_text('default.txt', "Неизвестный тип события.")

        # Формирование ответа
        response = {
            "text": resp_text,
            "reply_markup": {
                "keyboard": [
                    [{"text": "Повторить"}, {"text": "Стоп"}],
                    [{"text": "Помощь"}, {"text": "Инфо"}]
                ],
                "resize_keyboard": True,
                "one_time_keyboard": False
            }
        }
        return jsonify(response), 200

    except Exception as e:
        logger.exception('Error processing webhook: ' + str(e))
        return jsonify({"error": "Internal server error"}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Проверка работоспособности приложения."""
    return jsonify({"status": "healthy"}), 200


if __name__ == '__main__':
    cert_path = 'cert.pem'
    key_path = 'key.pem'

    # Проверка наличия сертификатов перед запуском
    if os.path.exists(cert_path) and os.path.exists(key_path):
        context = (cert_path, key_path)
    else:
        logger.warning("SSL certificates not found. Running without SSL (not recommended for production).")
        context = None

    logger.info(f"Запуск webhook сервера на {config.HOST}:{config.PORT}")

    # Если context=None, ssl_context не передаем
    if context:
        app.run(host=config.HOST, port=config.PORT, ssl_context=context, debug=False)
    else:
        app.run(host=config.HOST, port=config.PORT, debug=False)