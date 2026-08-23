FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stealth browser engine is optional — enable with --build-arg INSTALL_BROWSERS=true
ARG INSTALL_BROWSERS=false
RUN if [ "$INSTALL_BROWSERS" = "true" ]; then scrapling install; fi

COPY . .

RUN mkdir -p data

EXPOSE 8080
CMD ["python", "main.py"]
