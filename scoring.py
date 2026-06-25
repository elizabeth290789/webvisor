"""Scoring and aggregation helpers for CRO-focused Webvisor triage."""
from __future__ import annotations

from urllib.parse import urlparse

import pandas as pd

REGISTRATION_GOAL_IDS: set[str] = set()
KEY_URL_PARTS = ["/promo/tasks-24/", "/features/company/", "/pricing", "/signup", "/register"]
IMPORTANT_URL_PARTS = ["/signup", "/register", "/checkout", "/demo", "/pricing"]
PAID_SOURCE_MARKERS = ["ad", "ads", "cpc", "ppc", "paid", "direct", "vk", "telegram", "facebook", "google", "yandex"]
CTA_MARKERS = ["cta", "button", "signup_click", "register_click", "demo_click"]
EVENT_MARKERS = ["form_start", "form_error", "modal_close", "scroll_75", "scroll_90"]
DEVICE_MAP = {"1": "desktop", "2": "mobile", "3": "tablet", 1: "desktop", 2: "mobile", 3: "tablet"}


def _contains_any(value: object, markers: list[str]) -> bool:
    text = str(value or "").lower()
    return any(marker.lower() in text for marker in markers)


def _goals_set(value: object) -> set[str]:
    if pd.isna(value) or value == "":
        return set()
    return {part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()}


def _numeric(series: pd.Series | object, index: pd.Index) -> pd.Series:
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce").fillna(0)
    return pd.Series(0, index=index, dtype="float64")


def _text_series(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col in df:
        return df[col].fillna(default).astype(str)
    return pd.Series(default, index=df.index, dtype="object")


def normalize_device(value: object) -> str:
    if pd.isna(value) or value == "":
        return "unknown"
    raw = str(value).strip().lower()
    return DEVICE_MAP.get(raw, raw)


def _path(value: object) -> str:
    text = str(value or "")
    parsed = urlparse(text)
    return parsed.path or text or "(empty)"


def _same_url_family(start_url: object, end_url: object) -> bool:
    return _path(start_url).rstrip("/") == _path(end_url).rstrip("/")


def score_sessions(visits: pd.DataFrame, hits: pd.DataFrame, registration_goal_ids: list[str] | None = None) -> pd.DataFrame:
    goal_ids = set(registration_goal_ids or REGISTRATION_GOAL_IDS)
    visits = visits.copy()
    hits = hits.copy()
    visits.columns = [c.replace("ym:s:", "") for c in visits.columns]
    hits.columns = [c.replace("ym:pv:", "") for c in hits.columns]

    if "visitID" not in visits:
        return visits.assign(score=0, registered=False, reason_to_watch="Нет visitID в выгрузке", reasons="Нет visitID в выгрузке")

    visits["deviceCategory"] = _text_series(visits, "deviceCategory", "unknown").map(normalize_device)
    visits["visitDuration"] = _numeric(visits.get("visitDuration"), visits.index)
    visits["pageViews"] = _numeric(visits.get("pageViews"), visits.index)
    visits["goalsID"] = _text_series(visits, "goalsID")
    visits["registered"] = visits["goalsID"].map(lambda v: bool(goal_ids and _goals_set(v).intersection(goal_ids)))

    hit_features = _hit_features(hits)
    visits = visits.merge(hit_features, how="left", on="visitID")
    visit_url_text = _text_series(visits, "startURL") + " " + _text_series(visits, "endURL")
    for col in ["key_page", "important_url", "cta_click", "form_start", "form_error", "modal_close", "scroll_deep", "intermediate_goal"]:
        if col not in visits:
            visits[col] = False
        visits[col] = visits[col].fillna(False).astype(bool)
    visits["key_page"] = visits["key_page"] | visit_url_text.map(lambda x: _contains_any(x, KEY_URL_PARTS))
    visits["important_url"] = visits["important_url"] | visit_url_text.map(lambda x: _contains_any(x, IMPORTANT_URL_PARTS))
    visits["url_chain_count"] = _numeric(visits.get("url_chain_count"), visits.index)

    cr_by_device = visits.groupby("deviceCategory")["registered"].mean().to_dict()
    desktop_cr = cr_by_device.get("desktop", 0)
    mobile_cr = cr_by_device.get("mobile", 0)
    campaign_counts = _text_series(visits, "UTMCampaign").replace("", "(not set)").value_counts().to_dict()

    scores: list[int] = []
    reasons_col: list[str] = []
    watch_col: list[str] = []
    for _, row in visits.iterrows():
        score = 0
        reasons: list[str] = []
        no_reg = not bool(row.get("registered"))
        duration = float(row.get("visitDuration") or 0)
        page_views = float(row.get("pageViews") or 0)
        device = str(row.get("deviceCategory") or "unknown")
        campaign = str(row.get("UTMCampaign") or "")
        source = str(row.get("UTMSource") or row.get("lastTrafficSource") or "")
        start_url = row.get("startURL", "")
        end_url = row.get("endURL", "")

        def add(points: int, reason: str) -> None:
            nonlocal score
            score += points
            reasons.append(reason)

        if no_reg and duration >= 90 and page_views >= 2:
            add(30, "длительный визит без регистрации и 2+ просмотра")
        elif no_reg and duration >= 40 and page_views >= 2:
            add(15, "заметный визит без регистрации и 2+ просмотра")
        if no_reg and page_views >= 4:
            add(25, "высокая глубина без регистрации")
        if no_reg and row.get("important_url") and not _same_url_family(start_url, end_url):
            add(20, "переход на важную страницу без цели")
        if no_reg and device == "mobile" and desktop_cr > mobile_cr:
            add(20, "mobile CR хуже desktop")
        if no_reg and (campaign or _contains_any(source, PAID_SOURCE_MARKERS)):
            points = 20 if campaign_counts.get(campaign or "(not set)", 0) >= 2 else 12
            add(points, "платный или важный источник без регистрации")
        if no_reg and (row.get("cta_click") or row.get("form_start") or row.get("intermediate_goal")):
            add(25, "промежуточная цель/CTA/форма без регистрации")
        if no_reg and row.get("form_error"):
            add(20, "ошибка формы без регистрации")
        if no_reg and row.get("scroll_deep") and not row.get("cta_click"):
            add(12, "глубокий скролл без CTA")

        scores.append(min(score, 100))
        reasons_col.append("; ".join(reasons) or "нет выраженных CRO-сигналов")
        watch_col.append(_build_reason_to_watch(row, reasons))

    visits["score"] = scores
    visits["reasons"] = reasons_col
    visits["reason_to_watch"] = watch_col
    visits = visits.sort_values(["score", "visitDuration", "pageViews"], ascending=False).reset_index(drop=True)
    visits["priority_rank"] = range(1, len(visits) + 1)
    return visits


def _duration_ru(seconds: object) -> str:
    total = int(float(seconds or 0))
    minutes, sec = divmod(total, 60)
    return f"{minutes} мин {sec:02d} сек" if minutes else f"{sec} сек"


def _build_reason_to_watch(row: pd.Series, reasons: list[str]) -> str:
    no_reg_text = "регистрации нет" if not bool(row.get("registered")) else "регистрация есть"
    campaign = str(row.get("UTMCampaign") or "кампания не задана")
    source = str(row.get("UTMSource") or row.get("lastTrafficSource") or "источник не задан")
    reason_text = "; ".join(reasons[:3]) if reasons else "проверить как контрольную запись с низким score"
    return (
        f"{str(row.get('deviceCategory') or 'unknown').capitalize()} визит {_duration_ru(row.get('visitDuration'))}, "
        f"{int(float(row.get('pageViews') or 0))} просмотров, источник {source}, campaign {campaign}, {no_reg_text} — "
        f"{reason_text}. Стоит проверить, видел ли пользователь CTA, понял ли оффер и не было ли проблем с формой/навигацией."
    )


def _hit_features(hits: pd.DataFrame) -> pd.DataFrame:
    if hits.empty or "visitID" not in hits:
        return pd.DataFrame(columns=["visitID"])
    text_cols = [c for c in ["URL", "title", "params", "referer", "goalsID"] if c in hits]
    tmp = hits[["visitID", *text_cols]].copy()
    tmp["_text"] = tmp[text_cols].astype(str).agg(" ".join, axis=1).str.lower() if text_cols else ""
    grouped = tmp.groupby("visitID")["_text"].agg(" ".join).reset_index()
    grouped["key_page"] = grouped["_text"].map(lambda x: _contains_any(x, KEY_URL_PARTS))
    grouped["important_url"] = grouped["_text"].map(lambda x: _contains_any(x, IMPORTANT_URL_PARTS))
    grouped["cta_click"] = grouped["_text"].map(lambda x: _contains_any(x, CTA_MARKERS))
    grouped["form_start"] = grouped["_text"].str.contains("form_start", na=False)
    grouped["form_error"] = grouped["_text"].str.contains("form_error", na=False)
    grouped["modal_close"] = grouped["_text"].str.contains("modal_close", na=False)
    grouped["scroll_deep"] = grouped["_text"].str.contains("scroll_75|scroll_90", regex=True, na=False)
    grouped["intermediate_goal"] = grouped["cta_click"] | grouped["form_start"] | grouped["scroll_deep"]
    if "URL" in hits:
        grouped = grouped.merge(hits.groupby("visitID")["URL"].nunique().rename("url_chain_count").reset_index(), on="visitID", how="left")
    return grouped.drop(columns=["_text"])


def aggregate_stats(scored: pd.DataFrame) -> dict[str, pd.DataFrame]:
    scored = scored.copy()
    if scored.empty:
        return {}
    if "deviceCategory" in scored:
        scored["deviceCategory"] = scored["deviceCategory"].map(normalize_device)
    scored["start_path"] = _text_series(scored, "startURL").map(_path)
    scored["end_path"] = _text_series(scored, "endURL").map(_path)
    scored["UTMSource"] = _text_series(scored, "UTMSource").replace("", "(not set)")
    scored["UTMCampaign"] = _text_series(scored, "UTMCampaign").replace("", "(not set)")

    def agg(cols: str | list[str]) -> pd.DataFrame:
        group_cols = [cols] if isinstance(cols, str) else cols
        if any(col not in scored for col in group_cols):
            return pd.DataFrame()
        out = scored.groupby(group_cols, dropna=False).agg(
            visits=("visitID", "count"),
            visits_without_registration=("registered", lambda s: int((~s.astype(bool)).sum())),
            registration_cr=("registered", "mean"),
            avg_duration=("visitDuration", "mean"),
            avg_depth=("pageViews", "mean"),
            avg_score=("score", "mean"),
        ).reset_index()
        out["registration_cr"] = (out["registration_cr"] * 100).round(1)
        for col in ["avg_duration", "avg_depth", "avg_score"]:
            out[col] = out[col].round(1)
        return out.sort_values(["visits_without_registration", "avg_score", "visits"], ascending=False)

    return {
        "По устройствам": agg("deviceCategory"),
        "По UTMSource": agg("UTMSource"),
        "По UTMCampaign": agg("UTMCampaign"),
        "По startURL": agg("start_path"),
        "По endURL": agg("end_path"),
        "deviceCategory × UTMCampaign": agg(["deviceCategory", "UTMCampaign"]),
    }


def analyze_hits(hits: pd.DataFrame) -> dict[str, pd.DataFrame | str]:
    hits = hits.copy()
    hits.columns = [c.replace("ym:pv:", "") for c in hits.columns]
    if hits.empty or "visitID" not in hits:
        return {"warning": "Сейчас анализ построен только на визитах. Для анализа кликов, форм и поведения внутри страницы нужно включить загрузку hits или добавить события."}
    hits["path"] = _text_series(hits, "URL").map(_path)
    per_visit = hits.groupby("visitID").agg(urls_in_chain=("path", "nunique"), hits=("path", "count")).reset_index()
    url_freq = hits["path"].value_counts().rename_axis("URL").reset_index(name="hits").head(20)
    exits = hits.sort_values(["visitID", "dateTime" if "dateTime" in hits else "path"]).groupby("visitID").tail(1)["path"].value_counts().rename_axis("exitURL").reset_index(name="exits").head(20)
    goal_cols = [c for c in ["goalsID", "params", "title"] if c in hits]
    event_freq = pd.DataFrame()
    if goal_cols:
        event_text = hits[goal_cols].astype(str).agg(" ".join, axis=1)
        event_freq = event_text[event_text.str.strip().ne("")].value_counts().rename_axis("event_or_goal").reset_index(name="hits").head(20)
    return {"per_visit": per_visit, "url_freq": url_freq, "exits": exits, "event_freq": event_freq}
