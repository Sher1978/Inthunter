import sqlite3
conn = sqlite3.connect('intent_hunter.db')
cursor = conn.cursor()
cursor.execute("UPDATE scraper_accounts SET status = 'ACTIVE', error_log = NULL;")
conn.commit()
conn.close()
print('All userbots reset to ACTIVE.')
