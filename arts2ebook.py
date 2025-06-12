import requests
from urllib.parse import urlparse
from ebooklib import epub
from bs4 import BeautifulSoup
import subprocess
import qrcode
from io import BytesIO
import base64
import html
import datetime
from dotenv import load_dotenv
import os

load_dotenv()
DATE = datetime.datetime.now().strftime('%Y-%m-%d')

# ======= WALLABAG CONF =======
WALLABAG_CLIENT_ID = os.getenv('WALLABAG_CLIENT_ID')
WALLABAG_CLIENT_SECRET = os.getenv('WALLABAG_CLIENT_SECRET')
WALABAG_USERNAME = os.getenv('WALABAG_USERNAME')
WALABAG_PASSWORD = os.getenv('WALABAG_PASSWORD')
WALLABAG_API_BASE_URL = os.getenv('WALLABAG_API_BASE_URL')

# ======= EMAIL CONF =======
smtp_server = os.getenv('SMTP_SERVER')
smtp_port = os.getenv('SMTP_PORT')
smtp_user = os.getenv('SMTP_USER')
smtp_password = os.getenv('SMTP_PASSWORD')
smtp_from = os.getenv('SMTP_FROM')
email = os.getenv('EMAIL')

# ======= OTHER CONF =======
FULLTEXTRSS_API_KEY = os.getenv('FULLTEXTRSS_API_KEY')
DOMAINS_EXTRACT_USING_API = os.getenv('DOMAINS_EXTRACT_USING_API', '').split(',')
DOMAINS_EXCLUDED = os.getenv('DOMAINS_EXCLUDED', '').split(',')
DOMAINS_THAT_NEED_ENCODING_FIX = os.getenv('DOMAINS_THAT_NEED_ENCODING_FIX', '').split(',')

MARK_AS_READ = os.getenv('MARK_AS_READ', 'False').lower() == 'true'
SEND_EMAIL = os.getenv('SEND_EMAIL', 'False').lower() == 'true'

OUTPUT_FILE = DATE + '-wallabag.epub'
# =====================================

def get_token_wallabag():
    url = f'{WALLABAG_API_BASE_URL}/oauth/v2/token'
    data = {
        'grant_type': 'password',
        'client_id': WALLABAG_CLIENT_ID,
        'client_secret': WALLABAG_CLIENT_SECRET,
        'username': WALABAG_USERNAME,
        'password': WALABAG_PASSWORD
    }
    response = requests.post(url, data=data)
    response.raise_for_status()
    return response.json()['access_token']

def get_unread_entries_from_wallabag():

    token = get_token_wallabag()
    url = f'{WALLABAG_API_BASE_URL}/api/entries.json'
    headers = {'Authorization': f'Bearer {token}'}
    params = {'archive': 0, 'perPage': 100}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()['_embedded']['items']

def should_exclude(url):
    url = url.lower()
    return any(excl.lower() in url for excl in DOMAINS_EXCLUDED)

def should_use_api(url):
    url = url.lower()
    return any(excl.lower() in url for excl in DOMAINS_EXTRACT_USING_API)

def mark_as_read_in_wallabag(entry_id, token):
    url = f'{WALLABAG_API_BASE_URL}/api/entries/{entry_id}.json'
    headers = {'Authorization': f'Bearer {token}'}
    data = {'archive': 1}
    response = requests.patch(url, json=data, headers=headers)
    response.raise_for_status()

def get_content_from_fulltextrss_api(article_url):
    """
    Given a URL, fetches the content using the full-text-rss.p.rapidapi.com API.
    Returns a tuple (content, title) or (None, None) if there is an error.
    """
    api_url = "https://full-text-rss.p.rapidapi.com/extract.php"
    api_key = FULLTEXTRSS_API_KEY
    payload = {
        "url": article_url,
        "xss": "1",
        "lang": "2",
        "content": "1"
    }
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "full-text-rss.p.rapidapi.com",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        response = requests.post(api_url, data=payload, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        print(f"Fetched using Full-Text RSS API: {article_url}")
        return data.get('content'), data.get('title')
    except Exception as e:
        print(f"Error fetching content from Full-Text RSS API for {article_url}: {e}")
        return None, None

def clean_html(content):
    # Unescape HTML entities and normalize line breaks
    content = html.unescape(content)
    # Replace double line breaks or <br> with <p>
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    # If there are two or more line breaks, split into paragraphs
    paragraphs = [f"<p>{p.strip()}</p>" for p in content.split('\n\n') if p.strip()]
    content = "\n".join(paragraphs)
    soup = BeautifulSoup(content, 'lxml')
    return soup.prettify()

def fix_encoding_if_needed(content, domain):
    if any(d in domain for d in DOMAINS_THAT_NEED_ENCODING_FIX):
        try:
            return content.encode('latin1').decode('utf-8')
        except Exception:
            return content
    return content

def create_epub(grouped_articles):
    book = epub.EpubBook()
       
    book.set_identifier('wallabag-export + ' + DATE)
    book.set_title('Wallabag -' + DATE)
    book.set_language('es')
    book.add_author('Script')

    spine = ['nav']
    toc = []

    for domain, articles in grouped_articles.items():
        section_items = []
        for entry in articles:
            title = entry['title'] or entry['url']
            url = entry['url']

            # If the domain is in the list, use the external API to get the content
            if any(d in domain for d in DOMAINS_EXTRACT_USING_API):
                api_content, title = get_content_from_fulltextrss_api(url)
                content = api_content
            else:
                fixed_content = fix_encoding_if_needed(entry['content'], domain)
                content = clean_html(fixed_content)

            # Generate QR as base64 image
            qr_img = qrcode.make(url)
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            img_b64 = base64.b64encode(buffer.getvalue()).decode()
            img_html = f'<img src="data:image/png;base64,{img_b64}" alt="QR" style="width:150px;height:150px;"/>'

            # Add URL and QR in footer
            footer = f'<p><b>Original URL:</b> <a href="{url}">{url}</a></p>{img_html}<hr/>'
            
            chapter = epub.EpubHtml(title=title, file_name=f'{entry["id"]}.xhtml', lang='es')
            chapter.content = f'<h1>{title}</h1>{content}{footer}'
            book.add_item(chapter)
            section_items.append(chapter)

        toc.append((epub.Section(domain), section_items))
        spine.extend(section_items)

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(OUTPUT_FILE, book)
    print(f'📘 EPUB created: {OUTPUT_FILE}')

def main():

    entries = get_unread_entries_from_wallabag()
    print(f'Found {len(entries)} unread articles.')

    grouped = {}
    excluded_count = 0
    api_count = 0
    wallabag_count = 0

    for entry in entries:
        url = entry['url']
        if should_exclude(url):
            print(f'⛔ Excluded by URL: {url}')
            excluded_count += 1
            continue

        domain = urlparse(url).netloc.lower()
        grouped.setdefault(domain, []).append(entry)

        # Count by source
        if any(d in domain for d in DOMAINS_EXTRACT_USING_API):
            api_count += 1
        else:
            wallabag_count += 1

    print(f'{excluded_count} articles excluded by URL match.')
    print(f'{api_count} articles fetched via external API.')
    print(f'{wallabag_count} articles fetched directly from Wallabag.')

    if not grouped:
        print('No articles to export.')
        return

    create_epub(grouped)

    if MARK_AS_READ:
        for domain, articles in grouped.items():
            for entry in articles:
                print(f'Marking as read: {entry["title"] or entry["url"]}')
                mark_as_read_in_wallabag(entry['id'], token)

    # Send the file
    if SEND_EMAIL:
        print('Sending email to: ' + email)
        subprocess.run(
            ['calibre-smtp','--attachment',OUTPUT_FILE,'--relay',smtp_server,'--port',smtp_port,'--username',smtp_user,'--password',smtp_password,
            '--encryption-method','TLS','--subject',OUTPUT_FILE,smtp_from,email,'email body']
        )

if __name__ == '__main__':
    main()
