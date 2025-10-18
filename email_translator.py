#!/usr/bin/env python3
"""
Email Translation Service
Monitors inbox for non-English emails and sends translations via Telegram.
"""
import imaplib
import email
from email.header import decode_header
import json
import os
from datetime import datetime
from dotenv import load_dotenv
from langdetect import detect, DetectorFactory
from openai import OpenAI
import requests
import time
import sys

# Set seed for consistent language detection
DetectorFactory.seed = 0

# Load environment variables
load_dotenv()

# Configuration
CACHE_FILE = '/tools/imap-translator/processed_emails.json'
IMAP_SERVER = os.getenv('IMAP_SERVER')
IMAP_PORT = int(os.getenv('IMAP_PORT'))
IMAP_EMAIL = os.getenv('IMAP_EMAIL')
IMAP_PASSWORD = os.getenv('IMAP_PASSWORD')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def load_cache():
    """Load processed email IDs from cache file."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"processed_ids": []}
    return {"processed_ids": []}


def save_cache(cache):
    """Save processed email IDs to cache file."""
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def decode_mime_header(header):
    """Decode MIME encoded header."""
    if header is None:
        return ""
    decoded = decode_header(header)
    result = []
    for content, encoding in decoded:
        if isinstance(content, bytes):
            result.append(content.decode(encoding or 'utf-8', errors='ignore'))
        else:
            result.append(content)
    return ''.join(result)


def get_email_body(msg):
    """Extract email body text."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    body = part.get_payload(decode=True).decode(errors='ignore')
                    break
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(errors='ignore')
        except:
            pass

    return body.strip()


def detect_language(text):
    """Detect the language of the text. Returns language code or 'unknown'."""
    try:
        # Combine subject and body for better detection
        if len(text) < 10:
            return 'unknown'
        lang = detect(text)
        return lang
    except:
        return 'unknown'


def is_english(text):
    """Check if text is in English."""
    lang = detect_language(text)
    return lang == 'en'


def translate_and_analyze_email(subject, body, from_addr):
    """Use OpenAI to translate and analyze the email."""
    prompt = f"""You are analyzing an email that was sent in a non-English language.

From: {from_addr}
Subject: {subject}
Body: {body}

Please provide the following information in a structured JSON format:
1. "language": The original language name (e.g., "Swedish", "Spanish", "French")
2. "tldr": A one-line TL;DR summary of what the email is about (in English)
3. "action_required": Boolean - true if the recipient needs to take action, false otherwise
4. "category": One of: "receipt", "marketing", "notification", "personal", "business", "support", "newsletter", "account", "other"
5. "translated_subject": The subject translated to English
6. "translated_body": The body translated to English

Return ONLY the JSON object, no other text."""

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful email translation and analysis assistant. Always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        result_text = response.choices[0].message.content.strip()

        # Try to extract JSON if there's extra text
        if result_text.startswith('```json'):
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif result_text.startswith('```'):
            result_text = result_text.split('```')[1].split('```')[0].strip()

        return json.loads(result_text)

    except Exception as e:
        print(f"Error in OpenAI translation: {str(e)}")
        return None


def send_telegram_message(message):
    """Send a message via Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }

    response = requests.post(url, data=payload)
    return response.json()


def escape_html(text):
    """Escape HTML special characters for Telegram."""
    if not text:
        return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_telegram_message(from_addr, analysis):
    """Format the Telegram message with email details."""
    action_emoji = "🔴" if analysis['action_required'] else "🟢"

    # Escape all user content for HTML
    safe_from = escape_html(from_addr)
    safe_language = escape_html(analysis['language'])
    safe_tldr = escape_html(analysis['tldr'])
    safe_category = escape_html(analysis['category'].upper())
    safe_subject = escape_html(analysis['translated_subject'])
    safe_body = escape_html(analysis['translated_body'][:1000])

    message = f"""📧 <b>Foreign Language Email Received</b>

🌍 <b>Language:</b> {safe_language}
👤 <b>From:</b> {safe_from}
📝 <b>TL;DR:</b> {safe_tldr}
{action_emoji} <b>Action Required:</b> {'Yes' if analysis['action_required'] else 'No'}
🏷 <b>Category:</b> {safe_category}

━━━━━━━━━━━━━━━━━━

<b>Subject:</b> {safe_subject}

<b>Body:</b>
{safe_body}{"..." if len(analysis['translated_body']) > 1000 else ""}"""

    return message


def connect_imap():
    """Connect to IMAP server."""
    imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    imap.login(IMAP_EMAIL, IMAP_PASSWORD)
    return imap


def process_emails():
    """Main function to check and process emails."""
    try:
        # Load cache
        cache = load_cache()
        processed_ids = set(cache['processed_ids'])

        # Connect to IMAP
        imap = connect_imap()
        imap.select('INBOX')

        # Search for unread emails using PEEK
        status, messages = imap.search(None, 'UNSEEN')
        email_ids = messages[0].split()

        new_emails_processed = 0

        for email_id in email_ids:
            email_id_str = email_id.decode()

            # Skip if already processed
            if email_id_str in processed_ids:
                continue

            # Fetch email using PEEK to keep it unread
            status, msg_data = imap.fetch(email_id, '(BODY.PEEK[])')

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])

                    # Decode headers
                    subject = decode_mime_header(msg['subject'])
                    from_addr = decode_mime_header(msg['from'])

                    # Get body
                    body = get_email_body(msg)

                    # Combine for language detection
                    combined_text = f"{subject} {body}"

                    print(f"Processing email ID {email_id_str} from {from_addr}")

                    # Check if English
                    if is_english(combined_text):
                        print(f"  → Email is in English, skipping")
                    else:
                        print(f"  → Non-English email detected, translating...")

                        # Translate and analyze
                        analysis = translate_and_analyze_email(subject, body, from_addr)

                        if analysis:
                            # Format and send Telegram message
                            telegram_msg = format_telegram_message(from_addr, analysis)
                            result = send_telegram_message(telegram_msg)

                            if result.get('ok'):
                                print(f"  → Telegram notification sent successfully")
                                new_emails_processed += 1
                            else:
                                print(f"  → Failed to send Telegram notification: {result}")
                        else:
                            print(f"  → Failed to translate email")

                    # Mark as processed
                    processed_ids.add(email_id_str)

        # Save updated cache
        cache['processed_ids'] = list(processed_ids)
        save_cache(cache)

        # Close connection
        imap.close()
        imap.logout()

        print(f"Check completed. Processed {new_emails_processed} new non-English emails.")

    except Exception as e:
        print(f"Error processing emails: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    """Main loop - runs continuously checking emails every minute."""
    print(f"Email Translation Service started at {datetime.now()}")
    print(f"Monitoring: {IMAP_EMAIL}")
    print(f"Checking every 60 seconds...")
    print("-" * 50)

    while True:
        try:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for new emails...")
            process_emails()
            print(f"Next check in 60 seconds...")
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nService stopped by user")
            sys.exit(0)
        except Exception as e:
            print(f"Unexpected error: {str(e)}")
            print("Retrying in 60 seconds...")
            time.sleep(60)


if __name__ == "__main__":
    main()
