FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Warsaw
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd -g 10001 quicksilver && useradd -u 10001 -g quicksilver -M -s /usr/sbin/nologin quicksilver
COPY engine.py safe_images.py app_online.py settlements.py ./
COPY static/ ./static/
RUN mkdir -p /tmp/quicksilver && chown -R quicksilver:quicksilver /tmp/quicksilver /app
USER quicksilver
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app_online:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
