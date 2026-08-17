FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libproj-dev proj-data && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn","-w","2","-b","0.0.0.0:5000","--timeout","600","app:app"]
