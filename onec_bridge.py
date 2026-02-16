# onec_bridge.py

import os
import json
import requests
import uuid
import time
from config import USE_HTTP, ONEC_HTTP_URL, ONEC_FILE_OUTBOX, ONEC_FILE_INBOX, DEBUG

def send_to_1c(user_id, message_text, payload_data=None):
    """
    Отправляет запрос в 1С и возвращает ответ (словарь).
    - user_id: идентификатор пользователя в мессенджере
    - message_text: текст сообщения от пользователя
    - payload_data: дополнительные данные (callback_data, состояние и т.п.)
    """
    request_data = {
        "user_id": user_id,
        "text": message_text,
        "payload": payload_data,
        "timestamp": time.time()
    }

    if USE_HTTP:
        return _send_via_http(request_data)
    else:
        return _send_via_files(request_data)

def _send_via_http(request_data):
    """Отправка HTTP POST в 1С."""
    try:
        resp = requests.post(ONEC_HTTP_URL, json=request_data, timeout=10)
        resp.raise_for_status()
        return resp.json()   # ожидаем JSON с полями: text, buttons, ...
    except Exception as e:
        if DEBUG:
            print(f"[1C HTTP ERROR] {e}")
        return {"error": "Не удалось связаться с 1С"}

def _send_via_files(request_data):
    """Файловый обмен: пишем запрос, читаем ответ."""
    req_id = str(uuid.uuid4())
    req_filename = os.path.join(ONEC_FILE_OUTBOX, f"req_{req_id}.json")
    resp_filename = os.path.join(ONEC_FILE_INBOX, f"resp_{req_id}.json")

    # Запись запроса
    with open(req_filename, "w", encoding="utf-8") as f:
        json.dump(request_data, f, ensure_ascii=False)

    # Ожидание ответа (простейший polling)
    for _ in range(30):   # ждём до 3 секунд
        if os.path.exists(resp_filename):
            with open(resp_filename, "r", encoding="utf-8") as f:
                response = json.load(f)
            os.remove(req_filename)
            os.remove(resp_filename)
            return response
        time.sleep(0.1)

    # Таймаут
    os.remove(req_filename)
    return {"error": "Таймаут ожидания ответа от 1С"}