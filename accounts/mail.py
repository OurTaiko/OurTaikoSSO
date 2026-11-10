"""Outgoing mail, one sender per kind of message.

Verification links, sign-in codes and password resets each leave from the address configured
for them (EMAIL_SENDERS in settings). The SMTP connection logs in as that address, which is what
providers that bind credentials to the sender address, Alibaba Cloud DirectMail among them,
require. Callers keep handling OSError and smtplib.SMTPException themselves.
"""

from django.conf import settings
from django.core.mail import EmailMessage, get_connection


def sender(kind):
    return settings.EMAIL_SENDERS[kind]


def connection(kind):
    configured = sender(kind)
    return get_connection(username=configured["username"], password=configured["password"])


def send(kind, subject, body, to):
    EmailMessage(subject, body, sender(kind)["from_email"], [to], connection=connection(kind)).send()
