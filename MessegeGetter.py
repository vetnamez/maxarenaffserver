from turtledemo.penrose import inflatedart

import requests
from config import *
import time
import json
import re
import logging.handlers
import os
from flask import Flask, request, jsonify
url = API_BASE_URL + "updates" #url MAX
BTokens = [BOT_TOKEN_INVEST, BOT_TOKEN_SOTR, BOT_TOKEN_CHECK, BOT_TOKEN_ISP, BOT_TOKEN_IQ]


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



def sanitize_filename(name):
    """Оставляет в имени файла только безопасные символы (цифры и буквы)."""
    if name is None:
        return "unknown"
    # Разрешаем только цифры, буквы, дефис и подчеркивание
    return re.sub(r'[^\w\-]', '', str(name))

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

while True:
    try:
        headers = {
            "Authorization": BTokens[1],  # Токен Бота
            "Content-Type": "application/json"
        }
        response = requests.get(url, headers=headers)
        data = response.json()
        #print(response.text)
        updates = data.get('updates', {})
        for update in updates:
            callback = update.get('callback', {})
            message = update.get('message', {})

            if message:
                logger.info(data)
                message_id = message.get('body', {}).get('mid')
                chat_id = message.get('recipient', {}).get('chat_id')
                save_message_to_log("message_" + message_id + '_chat_id_' + str(chat_id), data, LOGS_DIR_SOTR)

            if callback:
                logger.info(data)
                callback_id = callback.get('callback_id')
                message_id = data.get('message', {}).get('body', {}).get('mid')
                chat_id = callback.get('recipient', {}).get('chat_id')
                save_message_to_log("callback_id_" + callback_id + "_chat_id_" + str(chat_id), data, LOGS_DIR_SOTR)
                pressed_button = callback.get("payload")


    except Exception as e:
            print(f"Ошибка при запросе getUpdates: {e}")
            time.sleep(5)
