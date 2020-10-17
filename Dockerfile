FROM python:3.9

WORKDIR .

COPY requirements.txt .
RUN python -m pip install -U git+https://github.com/Rapptz/discord-ext-menus
RUN pip install -r requirements.txt
COPY . .

CMD ["python", "bot.py"]