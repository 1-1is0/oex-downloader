FROM python:3.11-alpine
RUN pip install requests
WORKDIR /app
COPY downloader.py /app/downloader.py
CMD ["python", "/app/downloader.py"]
