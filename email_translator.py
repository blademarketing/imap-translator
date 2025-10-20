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
MAX_EMAILS = int(os.getenv('MAX_EMAILS', '50'))  # Maximum number of recent emails to check
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')

# Parse IMAP accounts - support both single account (legacy) and multiple accounts (JSON)
IMAP_ACCOUNTS = []
imap_accounts_json = os.getenv('IMAP_ACCOUNTS')
if imap_accounts_json:
    # Multiple accounts via JSON
    try:
        IMAP_ACCOUNTS = json.loads(imap_accounts_json)
    except json.JSONDecodeError as e:
        print(f"Error parsing IMAP_ACCOUNTS JSON: {e}")
        sys.exit(1)
else:
    # Legacy single account support
    imap_server = os.getenv('IMAP_SERVER')
    imap_port = os.getenv('IMAP_PORT')
    imap_email = os.getenv('IMAP_EMAIL')
    imap_password = os.getenv('IMAP_PASSWORD')

    if imap_server and imap_port and imap_email and imap_password:
        IMAP_ACCOUNTS = [{
            'server': imap_server,
            'port': int(imap_port),
            'email': imap_email,
            'password': imap_password,
            'name': imap_email  # Use email as default name
        }]

if not IMAP_ACCOUNTS:
    print("Error: No IMAP accounts configured. Please set IMAP_ACCOUNTS or legacy IMAP_* variables.")
    sys.exit(1)

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def load_cache():
    """Load processed email IDs from cache file."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
                # Migrate old format to new format
                if 'processed_ids' in cache and not isinstance(cache.get('processed_ids'), dict):
                    # Old format: {"processed_ids": ["id1", "id2"]}
                    # Convert to new format with a default account
                    old_ids = cache['processed_ids']
                    cache = {"accounts": {"default": old_ids}}
                elif 'accounts' not in cache:
                    cache = {"accounts": {}}
                return cache
        except:
            return {"accounts": {}}
    return {"accounts": {}}


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
4. "category": ENUM of: "receipt", "marketing", "notification", "personal", "business", "support", "newsletter", "account", "other"
5. "translated_subject": The subject translated to English
6. "translated_body": The body translated to English

Return result as a JSON object ONLY, no other text."""

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful email translation and analysis assistant. Always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4
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


def send_telegram_message(message, max_retries=3):
    """Send a message via Telegram bot with retry logic and rate limit handling."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, data=payload, timeout=30)
            result = response.json()

            # Check for rate limit (HTTP 429)
            if response.status_code == 429:
                retry_after = result.get('parameters', {}).get('retry_after', 5)
                print(f"    → Telegram rate limit hit, waiting {retry_after}s...")
                time.sleep(retry_after)
                continue

            return result

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2  # 2, 4, 6 seconds
                print(f"    → Network error sending to Telegram (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"    → Failed to send Telegram message after {max_retries} attempts: {str(e)}")
                return {'ok': False, 'error': str(e)}


def escape_html(text):
    """Escape HTML special characters for Telegram."""
    if not text:
        return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_telegram_message(from_addr, analysis, inbox_name=None):
    """Format the Telegram message with email details."""
    action_emoji = "🔴" if analysis['action_required'] else "🟢"

    # Escape all user content for HTML
    safe_from = escape_html(from_addr)
    safe_language = escape_html(analysis['language'])
    safe_tldr = escape_html(analysis['tldr'])
    safe_category = escape_html(analysis['category'].upper())
    safe_subject = escape_html(analysis['translated_subject'])
    safe_body = escape_html(analysis['translated_body'][:1000])

    # Add inbox info if provided
    inbox_info = ""
    if inbox_name:
        safe_inbox = escape_html(inbox_name)
        inbox_info = f"📬 <b>Inbox:</b> {safe_inbox}\n"

    message = f"""📧 <b>Foreign Language Email Received</b>

{inbox_info}🌍 <b>Language:</b> {safe_language}
👤 <b>From:</b> {safe_from}
📝 <b>TL;DR:</b> {safe_tldr}
{action_emoji} <b>Action Required:</b> {'Yes' if analysis['action_required'] else 'No'}
🏷 <b>Category:</b> {safe_category}

━━━━━━━━━━━━━━━━━━

<b>Subject:</b> {safe_subject}

<b>Body:</b>
{safe_body}{"..." if len(analysis['translated_body']) > 1000 else ""}"""

    return message


def connect_imap(account):
    """Connect to IMAP server for a specific account."""
    imap = imaplib.IMAP4_SSL(account['server'], account['port'])
    imap.login(account['email'], account['password'])
    return imap


def process_account_emails(account, cache):
    """Process emails for a single IMAP account."""
    account_email = account['email']
    account_name = account.get('name', account_email)

    try:
        # Get processed IDs for this account
        if account_email not in cache['accounts']:
            cache['accounts'][account_email] = []
        processed_ids = set(cache['accounts'][account_email])

        # Connect to IMAP
        imap = connect_imap(account)
        imap.select('INBOX')

        # Search for all emails (both read and unread)
        status, messages = imap.search(None, 'ALL')
        email_ids = messages[0].split()

        # Get only the most recent MAX_EMAILS emails
        email_ids = email_ids[-MAX_EMAILS:] if len(email_ids) > MAX_EMAILS else email_ids

        new_emails_processed = 0

        for email_id in email_ids:
            email_id_str = email_id.decode()

            # Skip if already processed
            if email_id_str in processed_ids:
                continue

            try:
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

                        print(f"  [{account_name}] Processing email ID {email_id_str} from {from_addr}")

                        # Check if English
                        if is_english(combined_text):
                            print(f"    → Email is in English, skipping")
                        else:
                            print(f"    → Non-English email detected, translating...")

                            # Translate and analyze
                            analysis = translate_and_analyze_email(subject, body, from_addr)

                            if analysis:
                                # Format and send Telegram message with inbox info
                                telegram_msg = format_telegram_message(from_addr, analysis, account_name)
                                result = send_telegram_message(telegram_msg)

                                if result.get('ok'):
                                    print(f"    → Telegram notification sent successfully")
                                    new_emails_processed += 1
                                    # Small delay to avoid rate limits (1 message/sec to same chat)
                                    time.sleep(1.5)
                                else:
                                    print(f"    → Failed to send Telegram notification: {result}")
                            else:
                                print(f"    → Failed to translate email")

                        # Mark as processed
                        processed_ids.add(email_id_str)

            except Exception as e:
                print(f"    → Error processing email ID {email_id_str}: {str(e)}")
                # Mark as processed anyway to avoid getting stuck on problematic emails
                processed_ids.add(email_id_str)
                continue

        # Update cache for this account
        cache['accounts'][account_email] = list(processed_ids)

        # Close connection
        imap.close()
        imap.logout()

        print(f"  [{account_name}] Check completed. Processed {new_emails_processed} new non-English emails.")
        return new_emails_processed

    except Exception as e:
        print(f"  [{account_name}] Error processing emails: {str(e)}")
        import traceback
        traceback.print_exc()
        return 0


def process_emails():
    """Main function to check and process emails for all accounts."""
    try:
        # Load cache
        cache = load_cache()

        total_processed = 0

        # Process each IMAP account
        for account in IMAP_ACCOUNTS:
            account_name = account.get('name', account['email'])
            print(f"\nChecking account: {account_name}")
            processed = process_account_emails(account, cache)
            total_processed += processed

        # Save updated cache
        save_cache(cache)

        print(f"\n{'='*50}")
        print(f"Total: Processed {total_processed} new non-English emails across all accounts.")

    except Exception as e:
        print(f"Error in main process: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    """Main loop - runs continuously checking emails every minute."""
    print(f"Email Translation Service started at {datetime.now()}")
    print(f"Monitoring {len(IMAP_ACCOUNTS)} account(s):")
    for account in IMAP_ACCOUNTS:
        account_name = account.get('name', account['email'])
        print(f"  - {account_name} ({account['email']})")
    print(f"Max emails to check per account: {MAX_EMAILS}")
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
