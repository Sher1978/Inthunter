import re
import logging
from sqlalchemy import select, delete
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel, DiscoveredChat, ChannelCandidate

logger = logging.getLogger("intent_hunter.spam_guard")

# 1. Non-target scripts & spam keywords
SPAM_PATTERNS = [
    r'[\u4e00-\u9fff]',  # Chinese characters
    r'[\uac00-\ud7af]',  # Korean characters
    r'[\u0600-\u06ff]',  # Arabic non-GEO spam characters
    r'trader', r'cricket', r'crypto', r'pump', r'casino', r'baccarat', r'betting', r'gambling',
    r'担保', r'公群', r'开房', r'记录', r'사기', r'骗子', r'套路', r'柬埔寨',
    r'movie', r'movies', r'bollywood', r'free-content', r'free_content', r'18\+', r'adult', r'erotic', r'porn', r'sinner'
]

# 2. Strict Non-Dubai GEO Rejection (Explicitly exclude other cities/countries)
NON_DUBAI_GEO_PATTERNS = [
    r'нячанг', r'nhatrang', r'вьетнам', r'vietnam',
    r'бали', r'bali', r'индонезия', r'indonesia',
    r'пхукет', r'phuket', r'патайя', r'pattaya', r'тайланд', r'thailand',
    r'тбилиси', r'tbilisi', r'грузия', r'georgia',
    r'ереван', r'yerevan', r'армения',
    r'стамбул', r'istanbul', r'анталья', r'antalya', r'турция', r'turkey',
    r'сербия', r'белград', r'кипр', r'cyprus',
    r'черногория', r'montenegro',
    r'москва', r'moscow', r'питер', r'spb', r'петербург',
    r'минск', r'minsk', r'алматы', r'almaty', r'астана', r'astana', r'ташкент', r'tashkent'
]

# 3. Dubai / UAE GEO Matching Keywords
DUBAI_GEO_PATTERNS = [
    r'dubai', r'дубай', r'дубае', r'дубая', r'дубаю', r'дубаем',
    r'uae', r'оаэ', r'эмираты', r'emirates', r'dxb',
    r'marina', r'jlt', r'jvc', r'downtown', r'business bay', r'palm jumeirah',
    r'barsha', r'deira', r'bur dubai', r'difc', r'ras al khaimah', r'rak',
    r'abu dhabi', r'абу даби', r'sharjah', r'шарджа', r'ajman', r'аджман'
]

def is_spam_or_non_target(username_or_link: str, title: str = "") -> bool:
    """
    Returns True if the channel contains:
    1. Asian spam scripts / Adult / Betting / Crypto / Movies.
    2. Non-Dubai GEOs (Nha Trang, Bali, Phuket, Moscow, Georgia, Turkey, etc.).
    """
    text = f"{username_or_link or ''} {title or ''}".lower()

    if any(re.search(pat, text, re.IGNORECASE) for pat in SPAM_PATTERNS):
        return True

    if any(re.search(pat, text, re.IGNORECASE) for pat in NON_DUBAI_GEO_PATTERNS):
        return True

    return False

def has_dubai_geo_relevance(username_or_link: str, title: str = "") -> bool:
    """Returns True if the candidate explicitly mentions Dubai / UAE or relevant local keywords."""
    text = f"{username_or_link or ''} {title or ''}".lower()
    if is_spam_or_non_target(username_or_link, title):
        return False
    return any(re.search(pat, text, re.IGNORECASE) for pat in DUBAI_GEO_PATTERNS)

async def purge_all_database_spam():
    """Purges all non-target / non-Dubai channels directly from PostgreSQL on Railway."""
    try:
        async with AsyncSessionLocal() as session:
            # 1. Purge MonitoredChannel
            res_m = await session.execute(select(MonitoredChannel))
            mons = list(res_m.scalars().all())
            del_mons = [m.id for m in mons if is_spam_or_non_target(m.username_or_link, m.title)]

            if del_mons:
                for i in range(0, len(del_mons), 500):
                    batch = del_mons[i:i+500]
                    await session.execute(delete(MonitoredChannel).where(MonitoredChannel.id.in_(batch)))
                await session.commit()
                logger.info(f"🧹 SPAM GUARD: Purged {len(del_mons)} non-Dubai/spam channels from MonitoredChannel table!")

            # 2. Purge DiscoveredChat
            res_d = await session.execute(select(DiscoveredChat))
            discs = list(res_d.scalars().all())
            del_discs = [d.id for d in discs if is_spam_or_non_target(d.chat_username, d.title)]

            if del_discs:
                for i in range(0, len(del_discs), 500):
                    batch = del_discs[i:i+500]
                    await session.execute(delete(DiscoveredChat).where(DiscoveredChat.id.in_(batch)))
                await session.commit()
                logger.info(f"🧹 SPAM GUARD: Purged {len(del_discs)} non-Dubai/spam items from DiscoveredChat table!")

            # 3. Purge ChannelCandidate
            res_c = await session.execute(select(ChannelCandidate))
            cands = list(res_c.scalars().all())
            del_cands = [c.id for c in cands if is_spam_or_non_target(c.username_or_link, c.title)]

            if del_cands:
                for i in range(0, len(del_cands), 500):
                    batch = del_cands[i:i+500]
                    await session.execute(delete(ChannelCandidate).where(ChannelCandidate.id.in_(batch)))
                await session.commit()
                logger.info(f"🧹 SPAM GUARD: Purged {len(del_cands)} non-Dubai candidates from ChannelCandidate table!")
    except Exception as e:
        logger.error(f"Spam Guard DB Purge notice: {e}")


SEED_16_SCRAPERS = [
    {"id": 1, "phone_number": "+919126748484", "account_username": "@Tapas_Kumar", "session_string": "BQAlPc_zZ5jzBE9Cqr9_a0l4Z0_cgxmWQ9VrxkHySj7fRtbJ1WLSF9PqHzR1FisTl3zyBa4e2jlO0BEMbI7FX98B0OeIfL2GGuZDrAeJ3NOCgP8e9tLUt-rQoRxd1uaRH6TpXwCxpTP5ySNgSWI7a9H_XKGynSBEYEEgxRFodJvRaXxCAvRN0idUP1VHVwD68FRbtbkRDGlUWobC9YZGi9P_JTiW8taJ6zHIXpX0RWaSnr4QZh1bsGQ60rpj8lm514FAiRYJfJAO309G72UNUWugkPmBnyExlLByiIs02Na8JzHp84CuIjU2Jr3Uowdep4JDYh7EgXco4QQFYAemi5wsAAAAAeEx6f0A", "status": "ACTIVE"},
    {"id": 2, "phone_number": "+919128386400", "account_username": "@Raja_Yadav", "session_string": "BQAw2DtZvI97lCMGUaNRDyMwrvhbRljozLTlgqxpl2BPY__LwIDw8IvkcuS4KXo_u9ZRZTZSTf0AIH9QECxRYTlCawY-XwCuWeffGWrPl6GUaJr7wcb76ZoOJl2PYx_IEYxIQ0pDTHYnNZn6fs9e6PfAga2DUXjA0amQw9W4XPgzrFOa_8tKlrGbK7BQMIQliGQqx0z6XSgQDjeayNXQ4xTwZWBtZA2OFjzp0Iw0Ut2jMpFP3wrUT9z98kEmsBOpRi9jwBrkEjpatkRdjJX06TkmopalItKQ7e6xhDwJ3A0J8ZZjasjsRuJRekSJEvnVlrprCd4cZnITd-IT6YqVem7yAAAAAFQZDYYA", "status": "ACTIVE"},
    {"id": 3, "phone_number": "+919129172189", "account_username": "@Mohd_Khan", "session_string": "BQA1BA2tl9ExXahnHp1VNbn_ztbucl8qoMzzqkO7E_jOPEG5fW5s7qjtJdZtOTrTcWYsW7lnlbUejyM9El0GkdNVopVn5QbI_PD50Fb--7_2voa16otBME7SdqMXFD5RFGBI_LKtaqcxFsRkaotnciBB1KuXPPY_k5dmzPBbLe8pRjB9e0Rl_j8K2-4wy3Ce3UfAngcHGhZApFOXChV_b940TG330TNf9H53glI78bVFJOEI4E8GvwxFaRVZZWrjMGT0QoavBqqmNaIzeS9lfLI8ul5mZ9dPkrvsTX7YOOJv4YtrvuGHsLCcEOLqznhJBwbHB3jSj_N8mFP2imk2NlayAAAAAfoakwoA", "status": "ACTIVE"},
    {"id": 4, "phone_number": "+919130123966", "account_username": "@Rahul_Sharma", "session_string": "BQCj5QXUrLExwTyzgSrf1aDxofIq3nfa__OnBtPDBiWWikMEpYDF9ys9k-kv7qb7f37ORBngC2LxaXw1D7dUUT2EFgLtt3uz7ndrcDdFBB0OKYvkPrDbzjVyZ7hEBIyec7f6gVG7AnC58QK0WCvAoCdxDV0SILnkCygAdJYw8GoFXQy_yMC0ketBVU8bMncDJIvhnRZ9Mf4zZ28OtpbgYmkjK2HEpgWqro1K0D_oZSUhvadSaKqYsKiM_iKdfB7Z7Ko3g6vok1Up11pSt1yqxH9SwMcOmEwbds8w0et1MUu1Xou3c8A6gs88NIJjQQfX2NOx7bYOeRYcL-DqHXM9iU6sAAAAAGFBTHoA", "status": "ACTIVE"},
    {"id": 5, "phone_number": "+919130321375", "account_username": "@Mijanur_Rahman", "session_string": "BQAjaX0TUV08Z-_70WHvh-k8ixGvXwP6mIcdW5nMDKkKcPY-n7vpMiqz9envi1R8q1JzVMamYwxfHK9rcHIg4d8qECy0LzAaFbOqTU9laGM3moOXlW-FH8tbABf3MYLkPzBBn38Ba9mLWu96BhI9RSKTw8XDE0sSNV6ji66EuKYDM5OW92aiCsfJYwQhowZE6O5WEKUe2Nc05QPGdLwH3g0EgoI1sn2-Nhyrz5KVe0w7qhgBByj6vFISMYbAyplOBjCH5WlsTHk8xVPMvzHu4TwyLzcPxsU7KLIyE8jUHS9FbkP-aRsUfBjJyJR9fCCVLmUZy7I3MehgyR9llS0WW52aAAAAAZz9RSIA", "status": "ACTIVE"},
    {"id": 6, "phone_number": "+919130394609", "account_username": "@Ranjana_Devi", "session_string": "BQC1fQE04_TmajfX93y8v7hozXKSDzbAkHceFZh_Jdsi-WR1B5gRCK6bZdcrqOwdCeao3yXLN7CRoW6oVPyu4R0DChZYYqVLsY6E2ORbjGgyvSvCmgJDRtID1n6SIAr1la1vgf41i5rtS6iXRTyTeMpMhJ06kTUEMFunqSnzUcBBe2T05RAj4cSVbgvuED0rqsQ2DGpcqSHqOT8lOS6mqgFqujAqZ9iVsQuqNagbKrAgypUqOim7mSGSPwT_z1PxZX-gI_VrdP4-PtYfg3Pj12rwuutZjJE183CYbhiHUuaRtiw0FaxhFx1IdbXPGNoEk9rW4_zuYroKBxtNv21kbMFfAAAAAdt76EsA", "status": "ACTIVE"},
    {"id": 7, "phone_number": "+919130764678", "account_username": "@Salim_Sheikh", "session_string": "BQB0bBV_2gGnYIRPVBr0hJeVZui2jMGtHZITuQ40dNxwWoQLnNT38ooKr7yGevlYDxCyTK1EnNFFDDXjI_rdw2onoIVnD6aQP0qvOZFv_k1eH8uIHTZXYBEyB8Vw7H2W7SpeajbtvAFnuz4QrjYXj3tUVjhnjRtNF3sWpf_J4TmEVKv1K0Vpk4-lhmOIWqmnQC6WWrKNiVgeAlH0KTBFpFhar9mku6jM0gMw5tlnAIU7AF59BKSMZZIodf0gKLlt88WJUZcg7Q4snp6cQTSxA466m6Bim4A6Oq21dkRxlDa_wTGRODxprIZ4hcIjMdIt19hhhdIIutUyYiNxQ558jzuJAAAAAYMSqncA", "status": "ACTIVE"},
    {"id": 8, "phone_number": "+919130768509", "account_username": "@Bharti_Kumari", "session_string": "BQAtlnILEwiieQIwEX4N_q0izABY5dcMyAlnPElvl9jfnLlh_UJsAxEP1-o3Y3XGimBDXceLh3OoQX3U1K-tV18wixz6hu-2vEdQPsqgdwTBoOE1mSv-OfBIBgYiZi9Q-dqv-mthAsKfid7QvWPDdpi5_3nk1RQgO_nv4XBnszVvHCxSktP-x3BdAGyzGlhmaReiso8XJ7eavwKdtj5N6waDOEhVNtR_RTTDXgat5ocu6ka-HBMZ5jvl_pOOYXPjL-29qurNOiH1JnPA79dJYYOvFX6P89Zxf9jcM3FHltAiiACZh7HfIaym0avmS4lv0DbDly5XntpRQka5YMpixYmJAAAAAWgg7EcA", "status": "ACTIVE"},
    {"id": 9, "phone_number": "+919131324637", "account_username": "@Royal_prince76", "session_string": "BQCasW4yTBk9B35oRyXGsk7vc0gFx7ZEBzsPk3rxabUDuOfk6KMZB_34yV4Ug7BY2X0HkYrD_1buWwNeKUyoD9Jv248Sj8uSxh61IXOwF9ryEWOGHO79RqKNTUaVoEw6KBkd-JUMsC6P8yi_1ByBfjS6dUFHs7qvk0gA895uXrffQc6H_83GsAZZj7qrVTuo11zGNCJUgOd95mLIUINPLxZhSOQCFjNYUWrgMQ9ticc4n0o4qkGg4YbhIFmIP-2VQNS98E4Pb8USHG-vQ-oplGUfDz35ELE4Yn8VIgMCv7nIrOq8Oe43lGb0nIA7vceu3QDwa1iWqq3bo9RFhOn3YTcbAAAAAY1CRwoA", "status": "ACTIVE"},
    {"id": 10, "phone_number": "+919131846837", "account_username": "@Arun_Kumar", "session_string": "BQAY7uIXal3-6Unkwa7nJLMUvCAVO-xNfzwn9wRP8kwcOTORcfenXmCuK9QgHJ-nNod_jw31OrblKyzSfSTizSsE18ba4SWVUhll3vkDHnMQw8X3WuI-EJ_L5p27gKAoOg0Hou9_6Q1cGH8d_PGP0BjpZngbyt73LKVRRZca-JVCiZoYV7dd_TqkMIk1QjpuKS9MfRRUglMRDHNaOlGocmunYsKBHaEH7p6sCsWg1h2a3td66ISTLxZ6Tz2mYdfZ3IFILN6YZA07ub4aeslGwFnuQUUBJwNEwGrZ-w_J0b32POt7FVP2jmVR3WZVcN6SPDrj068JHJhLhnj-roSExPUuAAAAAb51RBYA", "status": "ACTIVE"},
    {"id": 11, "phone_number": "+919133757662", "account_username": "@Sandeep_Singh", "session_string": "BQCM0FlQfjMBznqaneXaioHPAGRmkTKDNmBalITdC77nVlSLADNyZ93tCfNIm6tNcw0FSVaJwktrixUN-5w92uk8mn4yLDNf4NIMLDjnkr3jNOK0ge6K_VG4CEcLull5FUpe-JB0y9_q5EMpRvErjRHYXtg03xv2Nh2NO3mK3hTn1HsJHZ9UOnNkcyXmEKiAKrnTMWFdJqycjwtDZYDVeQuINq0o2ITwErO5M2S_-WUUPqLLWZCWCKAENcsXadoQkV-I1qRQ3fUzF6BKPMKAAmtlXObfEKiJDlBHNgQEAS9wI0slFGEyd4sTfjHJPpA0uRQODxL06EnoTEPjZddGGWFkAAAAAdjQYrAA", "status": "ACTIVE"},
    {"id": 12, "phone_number": "+919133962988", "account_username": "@Fatima_Begum", "session_string": "BQCFpzdN6iWl-_xpdDkEek4UTKpwgSFiwsrX2HICPb6q-80Of7rLC2PuHeAADjICHO-Wri9xY3nvx4irfX-9QQlyL4MXEhZ3upi6ocUGOWqalHyymv8uMwWIkJ7jxhM9J8YL_VCbzvdrM8HeQT3FL-pGYhqpOig8TnBeMpQAB0k2p5m59jipLu_J6xeTVQWCw95pZwoHfPxfZvflVbpxAOzzBBxeyu7NjPM5zzN2DgOEIILAA4pI61N8vxOMBGhYtVa4jvYJuyZtMCdYMOv1mxL4cmqypV8MGTSMkzgbZ-g9NxdSaJ7Q7471DR48qkk8GNLnf9CK1Up87pRgBZRUrRkkAAAAAXUdsvYA", "status": "BANNED"},
    {"id": 13, "phone_number": "+919134074891", "account_username": "@Avinash_Verma", "session_string": "BQCycoWtGWgWVRM4G4WXEiPB9XHlJPL1gAZwewueYQEYVJ46jQb7LHboKtHCCcpwOG1eQyJlRWwNRBdEv288Bdx5JVf5-53-_Bnhoj8IQ-tQqIcE0gizXxDciYuwq5CFrClnIhifcDrbM0VGYjY0BXjj20gj27nRNZZ2Y_gBP-i34QZDPq8-OmfNUASeuhA1_UQoZVj3KvEsONNUQ1ozU9X5HcDdMiAQJJmhWGE9iEMrwfb_bKkegH70NDB58xojVc0y3Nk0sbi1zTg6ubcXUR560-vnkKe1e5feTd-7XtW-y-rbWZjQ_XzSzmTgtADdyLH_LwZq_6XYGwZVtzdXYIWVAAAAAV51Nd0A", "status": "ACTIVE"},
    {"id": 14, "phone_number": "+919135458591", "account_username": "@Kadirkhan12345", "session_string": "BQC_8ceeCyD-fDGVMouVz16XEhGqU2CPlk7xDc-ZdYrlVI7nR6hJka_MiszYzglKGdgz5LgAVEq6lytEv4S3Rf5X8hQa8RJdclXTjmLFmS1Z35DeDh3ANY9YYyzfRC1QYY9h_RRjgiEcrMjGlxOSv2urbwDgsXAm5uKt6l6ENEJmjW4HOYZHqTjcbZygiTxmvZ9OGMmqm0nfgbSjbKWChzkjgp2XxTUeaX9xzigVzXbU0hJ42KMyWFuKbPa6nJKmOKmJwHtJqbB1CHt13vSq5B1pzWgGPv8dAJ9WdUKn_CvRG7XvWk-DFVZzR2b0degQRtCeIDD5L5XFxSrqwF_m0c2MAAAAAcbrCd0A", "status": "ACTIVE"},
    {"id": 15, "phone_number": "+919136660458", "account_username": "@Rishi_Verma", "session_string": "BQCx0Aka5X110mzZExbJf5w0i6SjM8BMNuhLA35xDirO6z0Y9JO4FgD9_lhwyi4dTIJUpQ7FXOsL9aPmHQKA-9UYPXDXO_9YqTnYSd2bxoW-fQL1UuNqdBF4_HdNDY529k6cx2_7xaBVKfzpZ7l8sMWZJWz5cYUaNW9UslFWDzKggSq01gury2wRfKfFOPqnaCETpE3SPHO-TRx7zIC3xi_ICm3i4L09MymC-J3zflvj2xs3inA8etEUPpHdSNwKsACuLtk8OheDJYQgIrFZ6Skxl1L674hYGCxk6u9-OtlaArXeqx25Eto6HkGgqeloQOD6fEa_Ew9W6Rk4D0KeoWS_AAAAAYmwSdUA", "status": "ACTIVE"},
    {"id": 16, "phone_number": "+971588044688", "account_username": "@Sherlock_cars_uae", "session_string": "BQAlPc_zZ5jzBE9Cqr9_a0l4Z0_cgxmWQ9VrxkHySj7fRtbJ1WLSF9PqHzR1FisTl3zyBa4e2jlO0BEMbI7FX98B0OeIfL2GGuZDrAeJ3NOCgP8e9tLUt-rQoRxd1uaRH6TpXwCxpTP5ySNgSWI7a9H_XKGynSBEYEEgxRFodJvRaXxCAvRN0idUP1VHVwD68FRbtbkRDGlUWobC9YZGi9P_JTiW8taJ6zHIXpX0RWaSnr4QZh1bsGQ60rpj8lm514FAiRYJfJAO309G72UNUWugkPmBnyExlLByiIs02Na8JzHp84CuIjU2Jr3Uowdep4JDYh7EgXco4QQFYAemi5wsAAAAAeEx6f0A", "status": "ACTIVE"}
]

async def sync_all_16_scrapers():
    """Ensures all 16 Scraper Accounts are seeded into PostgreSQL database on Railway with phone numbers and usernames."""
    try:
        from src.db.models import ScraperAccount
        async with AsyncSessionLocal() as session:
            # Purge fake orphaned dummy accounts (e.g. +971588044689 or missing phone_number)
            res_all = await session.execute(select(ScraperAccount))
            all_db = list(res_all.scalars().all())
            valid_phones = {item["phone_number"] for item in SEED_16_SCRAPERS}

            for s in all_db:
                if not s.phone_number or s.phone_number not in valid_phones:
                    await session.delete(s)
            await session.commit()

            existing_res = await session.execute(select(ScraperAccount))
            existing = {s.phone_number: s for s in existing_res.scalars().all() if s.phone_number}

            added = 0
            for item in SEED_16_SCRAPERS:
                p_num = item["phone_number"]
                if p_num not in existing:
                    new_sc = ScraperAccount(
                        phone_number=p_num,
                        account_username=item["account_username"],
                        session_string=item["session_string"],
                        status=item["status"],
                        max_daily_joins=20
                    )
                    session.add(new_sc)
                    added += 1
                else:
                    sc = existing[p_num]
                    sc.account_username = item["account_username"]
                    sc.session_string = item["session_string"]

            await session.commit()
            if added > 0:
                logger.info(f"⚡ SPAM GUARD / SEEDER: Successfully synced Scraper Accounts in DB! Total: 16.")
    except Exception as e:
        logger.error(f"Error seeding 16 scraper accounts: {e}")
