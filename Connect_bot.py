import requests
import config
url = config.API_BASE_URL + "subscriptions" #url MAX
BTokens = [config.BOT_TOKEN_INVEST, config.BOT_TOKEN_SOTR, config.BOT_TOKEN_CHECK, config.BOT_TOKEN_ISP, config.BOT_TOKEN_IQ]
hooks = ["webhook", "webhook1", "webhook2", "webhook3", "webhook4"]

for tkn in range(len(BTokens)):
    headers = {
        "Authorization": BTokens[tkn],  # Токен Бота
        "Content-Type": "application/json"
    }

    data = {
        "url": f"https://{config.MAIN_HOST}/{hooks[tkn]}",  # Адрес webhook сервера
        "update_types": ["message_created", "bot_started", "message_callback", "bot_stopped"],
        # Типы событий для webhook
    }
    print(tkn," =================================")
    #response1 = requests.post(url, headers=headers, json=data)
    response2 = requests.get(url, headers=headers)
    #print(response1.text)
    print(response2.text)
    print("=================================")
