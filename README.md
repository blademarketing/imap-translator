# Email Translation Service

Automatically monitors your inbox for non-English emails, translates them using OpenAI, and sends formatted notifications via Telegram.

## Features

- Monitors inbox every 60 seconds for unread emails
- Uses PEEK method to keep emails unread
- Detects non-English emails using langdetect library
- Translates emails using OpenAI GPT-3.5-turbo
- Sends formatted Telegram notifications with:
  - Original language detected
  - Sender information
  - One-line TL;DR summary
  - Action required indicator (🔴 Yes / 🟢 No)
  - Category classification (receipt, marketing, notification, personal, business, support, newsletter, account, other)
  - Translated subject and body
- Local cache to avoid duplicate notifications
- Runs as systemd service with automatic restart

## Files

- `email_translator.py` - Main application script
- `.env` - Environment variables (credentials)
- `.env.example` - Example environment file
- `requirements.txt` - Python dependencies
- `email-translator.service` - systemd service file
- `processed_emails.json` - Cache of processed email IDs

## Service Management

### Check service status
```bash
systemctl status email-translator.service
```

### View logs (live)
```bash
journalctl -u email-translator.service -f
```

### View last 100 log lines
```bash
journalctl -u email-translator.service -n 100
```

### Stop service
```bash
systemctl stop email-translator.service
```

### Start service
```bash
systemctl start email-translator.service
```

### Restart service
```bash
systemctl restart email-translator.service
```

### Disable service (won't start on boot)
```bash
systemctl disable email-translator.service
```

### Enable service (will start on boot)
```bash
systemctl enable email-translator.service
```

## Clear Cache

If you want to reprocess emails that were already seen:

```bash
rm /tools/imap-translator/processed_emails.json
systemctl restart email-translator.service
```

## Configuration

All credentials are stored in `.env`. Use `.env.example` as a template:

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

### Environment Variables

- `IMAP_SERVER` - IMAP server address
- `IMAP_PORT` - IMAP port (usually 993 for SSL)
- `IMAP_EMAIL` - Email address to monitor
- `IMAP_PASSWORD` - Email password
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `TELEGRAM_CHAT_ID` - Your Telegram chat ID
- `OPENAI_API_KEY` - OpenAI API key
- `OPENAI_MODEL` - OpenAI model to use (default: gpt-3.5-turbo, options: gpt-4, gpt-4-turbo, etc.)

## Requirements

- Python 3.12+
- Virtual environment located at `/tools/imap-translator/venv`
- Active internet connection
- Valid credentials for IMAP, Telegram, and OpenAI

## Troubleshooting

### Service not starting
```bash
journalctl -u email-translator.service -n 50
```

### Service crashes
The service is configured with `Restart=always` and `RestartSec=10`, so it will automatically restart after 10 seconds if it crashes.

### No notifications received
1. Check service is running: `systemctl status email-translator.service`
2. Check logs for errors: `journalctl -u email-translator.service -f`
3. Test individual components using test scripts
4. Verify credentials in `.env` file

### Emails not being detected as non-English
The langdetect library requires at least a few words to accurately detect language. Very short emails may not be detected correctly.

## License

Private use only.
