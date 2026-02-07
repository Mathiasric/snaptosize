FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
COPY wheels ./wheels
RUN pip install --no-index --find-links=./wheels -r requirements.txt

COPY . .

ENV PORT=7860
EXPOSE 7860

CMD ["python","app.py"]
