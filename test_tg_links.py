import urllib.request
import re

usernames = [
    'maverickmotoclub',
    'travellandia',
    'arsenforum',
    'IRINUSHKA999',
    'IrynaPHT',
    'Thai_Way',
    'pkhuket_police',
    'pkhuketm'
]

for u in usernames:
    url = f'https://t.me/{u}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req).read().decode('utf-8')
        if 'tgme_page_extra' in html or 'tgme_page_title' in html:
            if 'If you have <strong>Telegram</strong>' in html or 'not found' in html.lower():
                print(f'{u}: NOT FOUND (tgme_page says not found)')
            else:
                title_match = re.search(r'<meta property=\"og:title\" content=\"(.*?)\">', html)
                title = title_match.group(1) if title_match else 'Unknown'
                print(f'{u}: EXISTS (Title: {title})')
        else:
            print(f'{u}: UNKNOWN RESPONSE')
    except Exception as e:
        print(f'{u}: ERROR {e}')
