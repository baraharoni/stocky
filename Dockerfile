FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata curl tini \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Jerusalem

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Windows checkouts often use CRLF; strip CR so bash does not see $'\r' / "set: invalid option".
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

EXPOSE 8080
ENV PORT=8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "start.sh"]
