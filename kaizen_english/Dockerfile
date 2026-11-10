FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The database MUST live on a persistent volume mounted at /data
ENV KAIZEN_DB_PATH=/data/kaizen.db
VOLUME /data
CMD ["python", "main.py"]
