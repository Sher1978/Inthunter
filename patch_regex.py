import os

with open('src/api/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_str = r"for match in re.finditer(r'(?:http|https|socks4|socks5)://[a-zA-Z0-9_\-\.\@]+:\d+', text):"
new_str = r"for match in re.finditer(r'(?:http|https|socks4|socks5)://[a-zA-Z0-9_\-\.\:\@]+:\d+', text):"

content = content.replace(old_str, new_str)

with open('src/api/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)
