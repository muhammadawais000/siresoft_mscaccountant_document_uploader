"""Production settings. Assumes TLS terminates at Nginx, which sets X-Forwarded-Proto."""

from .base import *

DEBUG = False
LOG_FORMAT = env("LOG_FORMAT", default="json")
LOGGING["handlers"]["stdout"]["formatter"] = LOG_FORMAT

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Only for a first test over plain http (no TLS yet): DJANGO_SECURE_COOKIES=false.
# Without it the login cookie is never sent back over http and sign-in silently fails.
SECURE_COOKIES = env.bool("DJANGO_SECURE_COOKIES", default=True)
SESSION_COOKIE_SECURE = SECURE_COOKIES
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"

# HSTS preload is hard to undo; enabling it is a deliberate decision for the firm's domain.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
