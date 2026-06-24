"""Demo dataset for running the Streamlit app without Yandex Metrica access."""
from __future__ import annotations

import datetime as dt

import pandas as pd


def build_demo_visits_and_hits() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return small visits/hits samples shaped like Yandex Metrica Logs API exports."""
    base = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=1)
    visits = pd.DataFrame(
        [
            {
                "ym:s:visitID": "900001",
                "ym:s:clientID": "demo-client-01",
                "ym:s:dateTime": (base - dt.timedelta(minutes=8)).isoformat(sep=" "),
                "ym:s:startURL": "https://example.com/promo/tasks-24/",
                "ym:s:endURL": "https://example.com/signup",
                "ym:s:pageViews": 4,
                "ym:s:visitDuration": 185,
                "ym:s:bounce": 0,
                "ym:s:goalsID": "",
                "ym:s:lastTrafficSource": "ad",
                "ym:s:UTMCampaign": "summer_demo_mobile",
                "ym:s:UTMSource": "telegram",
                "ym:s:UTMTerm": "task tracker",
                "ym:s:deviceCategory": "mobile",
                "ym:s:browser": "Chrome Mobile",
                "ym:s:regionCountry": "Russia",
                "ym:s:regionCity": "Moscow",
                "ym:s:screenWidth": 390,
                "ym:s:screenHeight": 844,
            },
            {
                "ym:s:visitID": "900002",
                "ym:s:clientID": "demo-client-02",
                "ym:s:dateTime": (base - dt.timedelta(hours=2)).isoformat(sep=" "),
                "ym:s:startURL": "https://example.com/features/company/",
                "ym:s:endURL": "https://example.com/pricing",
                "ym:s:pageViews": 3,
                "ym:s:visitDuration": 96,
                "ym:s:bounce": 0,
                "ym:s:goalsID": "",
                "ym:s:lastTrafficSource": "organic",
                "ym:s:UTMCampaign": "",
                "ym:s:UTMSource": "yandex",
                "ym:s:UTMTerm": "",
                "ym:s:deviceCategory": "desktop",
                "ym:s:browser": "YaBrowser",
                "ym:s:regionCountry": "Russia",
                "ym:s:regionCity": "Saint Petersburg",
                "ym:s:screenWidth": 1440,
                "ym:s:screenHeight": 900,
            },
            {
                "ym:s:visitID": "900003",
                "ym:s:clientID": "demo-client-03",
                "ym:s:dateTime": (base - dt.timedelta(hours=4)).isoformat(sep=" "),
                "ym:s:startURL": "https://example.com/blog/checklist",
                "ym:s:endURL": "https://example.com/register/success",
                "ym:s:pageViews": 5,
                "ym:s:visitDuration": 240,
                "ym:s:bounce": 0,
                "ym:s:goalsID": "1001",
                "ym:s:lastTrafficSource": "ad",
                "ym:s:UTMCampaign": "retargeting_success",
                "ym:s:UTMSource": "vk",
                "ym:s:UTMTerm": "",
                "ym:s:deviceCategory": "desktop",
                "ym:s:browser": "Chrome",
                "ym:s:regionCountry": "Russia",
                "ym:s:regionCity": "Kazan",
                "ym:s:screenWidth": 1920,
                "ym:s:screenHeight": 1080,
            },
            {
                "ym:s:visitID": "900004",
                "ym:s:clientID": "demo-client-04",
                "ym:s:dateTime": (base - dt.timedelta(hours=7)).isoformat(sep=" "),
                "ym:s:startURL": "https://example.com/pricing",
                "ym:s:endURL": "https://example.com/demo",
                "ym:s:pageViews": 2,
                "ym:s:visitDuration": 62,
                "ym:s:bounce": 0,
                "ym:s:goalsID": "",
                "ym:s:lastTrafficSource": "ad",
                "ym:s:UTMCampaign": "pricing_ab_test",
                "ym:s:UTMSource": "direct",
                "ym:s:UTMTerm": "",
                "ym:s:deviceCategory": "tablet",
                "ym:s:browser": "Safari",
                "ym:s:regionCountry": "Russia",
                "ym:s:regionCity": "Novosibirsk",
                "ym:s:screenWidth": 820,
                "ym:s:screenHeight": 1180,
            },
        ]
    )
    hits = pd.DataFrame(
        [
            {"ym:pv:visitID": "900001", "ym:pv:URL": "https://example.com/promo/tasks-24/", "ym:pv:dateTime": base.isoformat(sep=" "), "ym:pv:title": "Promo", "ym:pv:goalsID": "", "ym:pv:referer": "", "ym:pv:artificial": 0, "ym:pv:params": "scroll_90"},
            {"ym:pv:visitID": "900001", "ym:pv:URL": "https://example.com/signup", "ym:pv:dateTime": base.isoformat(sep=" "), "ym:pv:title": "Signup", "ym:pv:goalsID": "", "ym:pv:referer": "https://example.com/promo/tasks-24/", "ym:pv:artificial": 0, "ym:pv:params": "form_start form_error"},
            {"ym:pv:visitID": "900002", "ym:pv:URL": "https://example.com/features/company/", "ym:pv:dateTime": base.isoformat(sep=" "), "ym:pv:title": "Company features", "ym:pv:goalsID": "", "ym:pv:referer": "", "ym:pv:artificial": 0, "ym:pv:params": "modal_close"},
            {"ym:pv:visitID": "900002", "ym:pv:URL": "https://example.com/pricing", "ym:pv:dateTime": base.isoformat(sep=" "), "ym:pv:title": "Pricing", "ym:pv:goalsID": "", "ym:pv:referer": "https://example.com/features/company/", "ym:pv:artificial": 0, "ym:pv:params": "scroll_75"},
            {"ym:pv:visitID": "900003", "ym:pv:URL": "https://example.com/register/success", "ym:pv:dateTime": base.isoformat(sep=" "), "ym:pv:title": "Success", "ym:pv:goalsID": "1001", "ym:pv:referer": "", "ym:pv:artificial": 0, "ym:pv:params": "register_click"},
            {"ym:pv:visitID": "900004", "ym:pv:URL": "https://example.com/demo", "ym:pv:dateTime": base.isoformat(sep=" "), "ym:pv:title": "Demo", "ym:pv:goalsID": "", "ym:pv:referer": "https://example.com/pricing", "ym:pv:artificial": 0, "ym:pv:params": "demo_click"},
        ]
    )
    return visits, hits
