# Alert Setup

## Independent Channels

The monitoring module provides separate `EmailAlertChannel` and
`WebhookAlertChannel` transports. Webhooks require HTTPS. SMTP uses TLS and a
password callback supplied by deployment configuration. The transports are
tested with fakes in CI; delivery to an operator endpoint is external setup.

## Deployment Checklist

1. Create a dedicated HTTPS webhook endpoint with least privilege.
2. Create a dedicated SMTP sender and recipient.
3. Store webhook and SMTP secrets in the deployment secret manager.
4. Configure independent credentials and network paths.
5. Send a test alert through each channel separately.
6. Confirm one channel can alert when the other is unavailable.
7. Record endpoint ownership and rotation dates outside source control.

Alert messages must not contain credentials, authorization tokens, raw model
responses, or sensitive account data. A failed critical alert transport is an
operational incident and must not enable trading.
