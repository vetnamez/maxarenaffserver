from flask import Flask, request, jsonify
import logging
import config
app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



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
    if config.SECRET_KEY and received_secret and received_secret != config.SECRET_KEY:
        logger.warning("Invalid secret received!")
        return jsonify({"error": "Invalid secret"}), 403

    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json()
        logger.info(f'Received message: {data}')
        update_type = data.get("update_type")

        # Можно добавлять разные сценарии по update_type
        user_id = data.get('user_id')
        text = data.get('text', '')

        if update_type == "bot_started":
            resp_text = "Добро пожаловать!"
        elif update_type == "message_created":
            resp_text = f"Вы сказали: {text}"
        else:
            resp_text = "Неизвестный тип события."

        # Формирование ответа (user_id не требуется в большинстве случаев)
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
        logger.exception('Error processing webhook:'+str(e))
        return jsonify({"error": "Internal server error"}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Проверка работоспособности приложения."""
    return jsonify({"status": "healthy"}), 200


if __name__ == '__main__':
    cert_path = 'cert.pem'
    key_path = 'key.pem'
    #    if os.path.exists(cert_path) and os.path.exists(key_path):
    context = (cert_path, key_path)
    #    app.run(host='0.0.0.0', port=17000, ssl_context=context, debug=True)
    # else:
    #     logger.warning('SSL certificates not found. Running without HTTPS!')
    #    app.run(host='0.0.0.0', port=8080, debug=True)
    logger.info(f"Запуск webhook сервера на {config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT,ssl_context=context, debug=False)