FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY scripts ./scripts
# Данные миссии «Чёрный пуск»: карту станции сервер читает с диска на
# старте рейда. Без этой строки образ собирается, бот поднимается, и
# всё падает ровно в тот миг, когда кто-то нажал «Объявить рейд»
COPY zombiemission ./zombiemission

# Порт мини-аппа (карточка персонажа)
EXPOSE 8080

CMD ["python", "-m", "bot.main"]
