FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY decide.py driver.py server.py ./

EXPOSE 8010

CMD ["python", "server.py"]
