import requests
import config
url = config.API_BASE_URL + "subscriptions" #url MAX

headers = {
    "Authorization": config.BOT_TOKEN, #Токен Бота
    "Content-Type": "application/json"
}

data = {
    "url": f"https://{config.MAIN_HOST}/webhook", #Адрес webhook сервера
    "update_types": ["message_created", "bot_started"], #Типы событий для webhook
    "secret": config.SECRET_KEY #Секретный ключ
}

response = requests.post(url, headers=headers, json=data)

print(response.status_code)
print(response.text)