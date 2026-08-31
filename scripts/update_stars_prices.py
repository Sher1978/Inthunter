import sys
import io

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

FILES = [
    "src/bot/hr_bot.py",
    "src/bot/vacancy_group_poster.py",
    "src/db/models.py",
    "src/api/routes.py",
]

# Tuples: (old_str, new_str)
REPLACEMENTS = [
    # Plan codes
    ("WEEK_200",   "WEEK_500"),
    ("MONTH_300",  "MONTH_1000"),
    # Stars amounts in emoji strings
    ("200 \u2b50",    "500 \u2b50"),
    ("300 \u2b50",    "1000 \u2b50"),
    ("200 Stars",  "500 Stars"),
    ("300 Stars",  "1000 Stars"),
    # Contact price
    ("50 \u2b50",    "100 \u2b50"),
    ("50 Stars",   "100 Stars"),
    # stars_price defaults
    ("stars_price: int = 50",   "stars_price: int = 100"),
    ("stars_price=50",          "stars_price=100"),
    ("\"stars_price\": 50",     "\"stars_price\": 100"),
    ("'stars_price': 50",       "'stars_price': 100"),
    # is_week check
    ("plan_code == \"WEEK_200\"",  "plan_code == \"WEEK_500\""),
    ("plan_code == 'WEEK_200'",    "plan_code == 'WEEK_500'"),
    # VIP Stars price calculation
    ("stars_price = 200 if is_week else 300",   "stars_price = 500 if is_week else 1000"),
    # amount_usd
    ("stars_paid * 0.025",  "stars_paid * 0.02"),
    # Upsell text
    ("\u0434\u0435\u0448\u0435\u0432\u043b\u0435 4 \u043f\u043e\u043a\u0443\u043f\u043e\u043a \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u043e\u0432 \u043f\u043e 50 \u2b50",   "\u0434\u0435\u0448\u0435\u0432\u043b\u0435 6 \u0440\u0430\u0437\u043e\u0432\u044b\u0445 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u043e\u0432 \u043f\u043e 100 \u2b50"),
    ("\u043f\u043b\u0430\u0442\u0438\u0442\u044c 50 \u2b50 \u0437\u0430 \u043a\u0430\u0436\u0434\u044b\u0439",  "\u043f\u043b\u0430\u0442\u0438\u0442\u044c 100 \u2b50 \u0437\u0430 \u043a\u0430\u0436\u0434\u044b\u0439"),
    # VIP button labels
    ("VIP \u043d\u0430 \u043d\u0435\u0434\u0435\u043b\u044e \u2014 200 \u2b50",  "VIP \u043d\u0430 \u043d\u0435\u0434\u0435\u043b\u044e \u2014 500 \u2b50 (~$10)"),
    ("VIP \u043d\u0430 \u043c\u0435\u0441\u044f\u0446 \u2014 300 \u2b50",   "VIP \u043d\u0430 \u043c\u0435\u0441\u044f\u0446 \u2014 1000 \u2b50 (~$20)"),
    # Contact upsell price hint
    ("\u0432\u0441\u0435\u0433\u043e 200 \u2b50 \u043d\u0430 \u043d\u0435\u0434\u0435\u043b\u044e",  "\u0432\u0441\u0435\u0433\u043e 500 \u2b50 \u043d\u0430 \u043d\u0435\u0434\u0435\u043b\u044e (~$10)"),
    ("\u0412\u0441\u0435\u0433\u043e 200 \u2b50 \u043d\u0430 \u043d\u0435\u0434\u0435\u043b\u044e",  "\u0412\u0441\u0435\u0433\u043e 500 \u2b50 \u043d\u0430 \u043d\u0435\u0434\u0435\u043b\u044e (~$10)"),
    # Reminder text "50 zvyozd"
    ("\u043f\u043e\u043a\u0443\u043f\u0430\u0442\u044c \u043a\u043e\u043d\u0442\u0430\u043a\u0442 \u0437\u0430 50 \u2b50",  "\u043f\u043e\u043a\u0443\u043f\u0430\u0442\u044c \u043a\u043e\u043d\u0442\u0430\u043a\u0442 \u0437\u0430 100 \u2b50"),
    # Group poster Stars label in post card
    ("50 \u2b50)",  "100 \u2b50)"),
]

total = 0
for fpath in FILES:
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        original = content
        for old, new in REPLACEMENTS:
            if old == new:
                continue
            n = content.count(old)
            if n:
                content = content.replace(old, new)
                print(f"  [{fpath}] {n}x: ...replaced...")
                total += n
        if content != original:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"Saved: {fpath}")
        else:
            print(f"No changes: {fpath}")
    except FileNotFoundError:
        print(f"SKIP: {fpath}")

print(f"Total replacements: {total}")
