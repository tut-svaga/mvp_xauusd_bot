import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def send_prediction():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID_SER")

    message_path = Path("output_stage_d/stage_d_telegram_message.html")
    message = message_path.read_text(encoding="utf-8")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        },
        timeout=1
    )

    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    result = send_prediction()
    print("Telegram response:", result)