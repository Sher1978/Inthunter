import re
raw_s = '@_chatik_phuket'
raw_s = re.sub(r'^[_\s\-\*\•\"\'\«\»\>\#]+', '', raw_s)
print('raw_s 1:', raw_s)
raw_s = raw_s.replace('https://t.me/s/', '').replace('https://t.me/', '').replace('http://t.me/s/', '').replace('http://t.me/', '').replace('t.me/', '')
raw_s = re.sub(r'^[_\s\-\*\•\"\'\«\»\>\#]+', '', raw_s)
print('raw_s 2:', raw_s)
clean_user = raw_s.split('/')[0].split('?')[0].lstrip('@').strip()
clean_target = f'@{clean_user}' if not clean_user.startswith('+') else clean_user
print('clean_target:', clean_target)
