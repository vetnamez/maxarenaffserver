import config
import os
import json
import requests

def load_payload(filepath: str) -> dict:
    """Загружает JSON-файл с полезной нагрузкой."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            payload =  json.load(f)

    except FileNotFoundError:
        print(f"❌ Файл {filepath} не найден!")
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга JSON: {e}")
    return payload



def send_message(user_id: str, token: str, payload: dict) -> requests.Response:
    """Отправляет сообщение в бота."""
    url = f"{config.API_BASE_URL}messages?user_id={user_id}"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json"
    }
    try:
        request = requests.post(
            url,
            json=payload,  # requests сам сериализует dict в JSON
            headers=headers,
            timeout=15
        )
    except requests.exceptions.RequestException as e:
        print(f"❌ Сетевая ошибка: {e}")
    return request