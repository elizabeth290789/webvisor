"""Scoring rules for selecting suspicious Webvisor sessions."""
from __future__ import annotations

import pandas as pd

REGISTRATION_GOAL_IDS: set[str] = set()
KEY_URL_PARTS = ["/promo/tasks-24/", "/features/company/", "/pricing", "/signup", "/register"]
CTA_MARKERS = ["cta", "button", "signup_click", "register_click", "demo_click"]
EVENT_MARKERS = ["form_start", "form_error", "modal_close", "scroll_75", "scroll_90"]


def _contains_any(value: object, markers: list[str]) -> bool:
    text = str(value or "").lower()
    return any(marker.lower() in text for marker in markers)


def _goals_set(value: object) -> set[str]:
    if pd.isna(value) or value == "":
        return set()
    return {part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()}


def score_sessions(visits: pd.DataFrame, hits: pd.DataFrame, registration_goal_ids: list[str] | None = None) -> pd.DataFrame:
    goal_ids = set(registration_goal_ids or REGISTRATION_GOAL_IDS)
    visits = visits.copy()
    hits = hits.copy()
    visits.columns = [c.replace("ym:s:", "") for c in visits.columns]
    hits.columns = [c.replace("ym:pv:", "") for c in hits.columns]

    if "visitID" not in visits:
        return visits.assign(score=0, reasons="Нет visitID в выгрузке")

    hit_features = _hit_features(hits)
    visits = visits.merge(hit_features, how="left", on="visitID")
    for col in ["key_page", "important_url", "cta_click", "form_start", "form_error", "modal_close", "scroll_deep"]:
        visits[col] = visits[col].fillna(False).astype(bool)

    scores: list[int] = []
    reasons_col: list[str] = []
    for _, row in visits.iterrows():
        score = 0
        reasons: list[str] = []
        goals = _goals_set(row.get("goalsID"))
        registered = bool(goal_ids and goals.intersection(goal_ids))
        no_reg = not registered
        duration = float(row.get("visitDuration") or 0)
        page_views = float(row.get("pageViews") or 0)
        device = str(row.get("deviceCategory") or "").lower()

        def add(points: int, reason: str) -> None:
            nonlocal score
            score += points
            reasons.append(reason)

        if duration > 40 and no_reg:
            add(20, "визит дольше 40 секунд без регистрации")
        if page_views >= 2 and no_reg:
            add(15, "2+ просмотра страниц без регистрации")
        if row.get("key_page") and no_reg:
            add(20, "просмотр ключевой страницы")
        if row.get("important_url") and no_reg:
            add(15, "дошел до важного URL без цели")
        if "mobile" in device and no_reg:
            add(10, "мобильный визит без цели")
        if duration > 120 and no_reg and not row.get("cta_click"):
            add(15, "длинный визит без CTA click")
        for event in ["form_start", "form_error", "modal_close"]:
            if row.get(event) and no_reg:
                add(15, f"событие {event} без регистрации")
        if row.get("scroll_deep") and no_reg and not row.get("cta_click"):
            add(15, "глубокий scroll без CTA click")
        if row.get("cta_click") and no_reg:
            add(25, "CTA click без регистрации")

        scores.append(min(score, 100))
        reasons_col.append("; ".join(reasons) or "нет подозрительных признаков")

    visits["registered"] = visits["goalsID"].map(lambda v: bool(goal_ids and _goals_set(v).intersection(goal_ids)))
    visits["score"] = scores
    visits["reasons"] = reasons_col
    return visits.sort_values("score", ascending=False)


def _hit_features(hits: pd.DataFrame) -> pd.DataFrame:
    if hits.empty or "visitID" not in hits:
        return pd.DataFrame(columns=["visitID"])
    text_cols = [c for c in ["URL", "title", "params", "referer"] if c in hits]
    tmp = hits[["visitID", *text_cols]].copy()
    tmp["_text"] = tmp[text_cols].astype(str).agg(" ".join, axis=1).str.lower() if text_cols else ""
    grouped = tmp.groupby("visitID")["_text"].agg(" ".join).reset_index()
    grouped["key_page"] = grouped["_text"].map(lambda x: _contains_any(x, KEY_URL_PARTS))
    grouped["important_url"] = grouped["_text"].map(lambda x: _contains_any(x, ["/signup", "/register", "/checkout", "/demo"]))
    grouped["cta_click"] = grouped["_text"].map(lambda x: _contains_any(x, CTA_MARKERS))
    grouped["form_start"] = grouped["_text"].str.contains("form_start", na=False)
    grouped["form_error"] = grouped["_text"].str.contains("form_error", na=False)
    grouped["modal_close"] = grouped["_text"].str.contains("modal_close", na=False)
    grouped["scroll_deep"] = grouped["_text"].str.contains("scroll_75|scroll_90", regex=True, na=False)
    return grouped.drop(columns=["_text"])


def aggregate_stats(scored: pd.DataFrame) -> dict[str, pd.DataFrame]:
    def agg(col: str) -> pd.DataFrame:
        if col not in scored:
            return pd.DataFrame()
        return scored.groupby(col, dropna=False).agg(visits=("visitID", "count"), avg_score=("score", "mean")).reset_index().sort_values("visits", ascending=False)
    scored = scored.copy()
    scored["start_path"] = scored.get("startURL", pd.Series(dtype=str)).astype(str).str.extract(r"https?://[^/]+([^?#]*)", expand=False).fillna(scored.get("startURL", ""))
    return {"url": agg("start_path"), "device": agg("deviceCategory"), "utm": agg("UTMCampaign")}
