# Alert Setup

## Independent Channels

The monitoring module provides separate `EmailAlertChannel`,
`WebhookAlertChannel`, and `TelegramAlertChannel` transports, plus a
`FileAlertChannel` for file destinations. Webhooks and Telegram require HTTPS
(the Telegram bot-API endpoint is HTTPS by construction). SMTP uses TLS and a
password callback supplied by deployment configuration. The default
`AlertHandler` channels are genuinely separate destinations: channel A writes
`[ALERT-CHAN-A]` to stderr and channel B writes to a dedicated alert file. The
transports are tested with fakes in CI; delivery to an operator endpoint is
external setup.

## Telegram Channel

`TelegramAlertChannel(bot_token, chat_id)` posts critical alerts to the
Telegram bot API over HTTPS. Credentials are never logged, and error messages
report HTTP status only. Missing credentials fail closed: construction with an
empty token or chat id raises, and `from_env()` raises unless both
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured in the environment.

Configuration template (deployment secret manager, never source control):

```
TELEGRAM_BOT_TOKEN=<bot-token-from-botfather>
TELEGRAM_CHAT_ID=<operator-chat-id>
```

## External Dead-Man CLI

The dead-man CLI (`dead_man.py`, entry point `dead-man-monitor`) is a real
standalone process deployed outside the trading VM. See
`docs/runbooks/incident-response.md` for the deployment steps. A failed
critical alert transport is an operational incident and must not enable
trading.

## Deployment Checklist

1. Create a dedicated HTTPS webhook endpoint with least privilege.
2. Create a dedicated SMTP sender and recipient.
3. Create a dedicated Telegram bot and operator chat.
4. Store webhook, SMTP, and Telegram secrets in the deployment secret manager.
5. Configure independent credentials and network paths.
6. Send a test alert through each channel separately.
7. Confirm one channel can alert when the other is unavailable.
8. Record endpoint ownership and rotation dates outside source control.

Alert messages must not contain credentials, authorization tokens, raw model
responses, or sensitive account data.
