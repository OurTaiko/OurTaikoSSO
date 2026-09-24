import os

bind = os.getenv("SSO_LISTEN_ADDR", "127.0.0.1:8090")
workers = 2
worker_class = "gthread"
threads = 4
timeout = 30
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
# Access logs intentionally omit URLs/query strings containing authorization codes.
accesslog = None
errorlog = "-"
forwarded_allow_ips = "127.0.0.1,::1"
umask = 0o077
