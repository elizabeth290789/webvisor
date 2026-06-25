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



def baseline_metrics(scored: pd.DataFrame) -> dict[str, float]:
    """Return baseline metrics for the currently selected visit sample."""
    if scored.empty:
        return {"total_visits": 0, "registrations": 0, "registration_cr": 0.0, "avg_visitDuration": 0.0, "avg_pageViews": 0.0}
    registered = scored.get("registered", pd.Series(False, index=scored.index)).astype(bool)
    duration = _numeric(scored.get("visitDuration"), scored.index)
    page_views = _numeric(scored.get("pageViews"), scored.index)
    total = int(len(scored))
    regs = int(registered.sum())
    return {
        "total_visits": total,
        "registrations": regs,
        "registration_cr": float(regs / total) if total else 0.0,
        "avg_visitDuration": float(duration.mean()) if total else 0.0,
        "avg_pageViews": float(page_views.mean()) if total else 0.0,
    }


SEGMENT_DEFINITIONS: list[tuple[str, list[str]]] = [
    ("deviceCategory", ["deviceCategory"]),
    ("UTMSource", ["UTMSource"]),
    ("UTMCampaign", ["UTMCampaign"]),
    ("startURL", ["start_path"]),
    ("endURL", ["end_path"]),
    ("deviceCategory × UTMSource", ["deviceCategory", "UTMSource"]),
    ("deviceCategory × UTMCampaign", ["deviceCategory", "UTMCampaign"]),
    ("startURL × deviceCategory", ["start_path", "deviceCategory"]),
]


def _prepare_segment_frame(scored: pd.DataFrame) -> pd.DataFrame:
    df = scored.copy()
    if df.empty:
        return df
    df["deviceCategory"] = _text_series(df, "deviceCategory", "unknown").map(normalize_device)
    df["UTMSource"] = _text_series(df, "UTMSource").replace("", "(not set)")
    df["UTMCampaign"] = _text_series(df, "UTMCampaign").replace("", "(not set)")
    df["start_path"] = _text_series(df, "startURL").map(_path).replace("", "(empty)")
    df["end_path"] = _text_series(df, "endURL").map(_path).replace("", "(empty)")
    df["visitDuration"] = _numeric(df.get("visitDuration"), df.index)
    df["pageViews"] = _numeric(df.get("pageViews"), df.index)
    df["registered"] = df.get("registered", pd.Series(False, index=df.index)).astype(bool)
    return df


def find_problem_segments(scored: pd.DataFrame, min_visits: int | None = None) -> pd.DataFrame:
    """Find segments whose registration CR is worse than the selected sample baseline."""
    df = _prepare_segment_frame(scored)
    if df.empty:
        return pd.DataFrame()
    base = baseline_metrics(df)
    baseline_cr = base["registration_cr"]
    avg_duration = base["avg_visitDuration"]
    avg_pageviews = base["avg_pageViews"]
    total_visits = max(int(base["total_visits"]), 1)
    min_visits = min_visits if min_visits is not None else max(3, int(total_visits * 0.02))
    rows: list[pd.DataFrame] = []
    for segment_type, cols in SEGMENT_DEFINITIONS:
        if any(col not in df for col in cols):
            continue
        g = df.groupby(cols, dropna=False).agg(
            visits=("visitID", "count"),
            registrations=("registered", "sum"),
            avg_visitDuration=("visitDuration", "mean"),
            avg_pageViews=("pageViews", "mean"),
        ).reset_index()
        g["segment_type"] = segment_type
        g["segment_name"] = g[cols].astype(str).agg(" + ".join, axis=1)
        rows.append(g)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True, sort=False)
    out["CR"] = out["registrations"] / out["visits"].clip(lower=1)
    out["baseline_CR"] = baseline_cr
    out["CR_delta"] = out["CR"] - baseline_cr
    out["share_of_traffic"] = out["visits"] / total_visits
    interest = (out["avg_visitDuration"] / max(avg_duration, 1)).clip(upper=2) * 0.5 + (out["avg_pageViews"] / max(avg_pageviews, 1)).clip(upper=2) * 0.5
    volume_factor = (out["visits"] / max(min_visits, 1)).clip(upper=1)
    cr_drop = ((baseline_cr - out["CR"]) / max(baseline_cr, 0.0001)).clip(lower=0, upper=3)
    out["priority_score"] = (100 * cr_drop * (0.65 + 0.35 * interest) * volume_factor).round(1)
    out = out[(out["visits"] >= min_visits) & (out["CR"] < baseline_cr)]
    for col in ["CR", "baseline_CR", "CR_delta", "share_of_traffic"]:
        out[col] = (out[col] * 100).round(2)
    for col in ["avg_visitDuration", "avg_pageViews"]:
        out[col] = out[col].round(1)
    display_cols = ["segment_type", "segment_name", "visits", "registrations", "CR", "baseline_CR", "CR_delta", "avg_visitDuration", "avg_pageViews", "share_of_traffic", "priority_score"]
    return out.sort_values(["priority_score", "visits"], ascending=False)[display_cols].reset_index(drop=True)


def _segment_mask(df: pd.DataFrame, segment_type: str, segment_name: str) -> pd.Series:
    prep = _prepare_segment_frame(df)
    defs = dict(SEGMENT_DEFINITIONS)
    cols = defs.get(segment_type, [])
    if not cols:
        return pd.Series(False, index=df.index)
    labels = prep[cols].astype(str).agg(" + ".join, axis=1)
    return labels.eq(str(segment_name))


def select_records_to_watch(scored: pd.DataFrame, problem_segments: pd.DataFrame, per_segment: int = 5) -> pd.DataFrame:
    df = scored.copy()
    if df.empty or problem_segments.empty:
        return pd.DataFrame()
    rows = []
    for _, seg in problem_segments.head(5).iterrows():
        mask = _segment_mask(df, str(seg["segment_type"]), str(seg["segment_name"]))
        pool = df[mask].copy()
        if "registered" in pool:
            pool = pool[~pool["registered"].astype(bool)]
        if pool.empty:
            continue
        pool["visitDuration"] = _numeric(pool.get("visitDuration"), pool.index)
        selected = []
        high = pool.sort_values("visitDuration", ascending=False).head(2)
        selected.append((high, "длинный визит в проблемном сегменте"))
        rest = pool.drop(index=high.index, errors="ignore")
        if not rest.empty:
            median = rest["visitDuration"].median()
            mid = rest.assign(_dist=(rest["visitDuration"] - median).abs()).sort_values("_dist").head(2).drop(columns=["_dist"])
            selected.append((mid, "типичный по длительности визит в проблемном сегменте"))
            rest = rest.drop(index=mid.index, errors="ignore")
        short = rest[(rest["visitDuration"] <= 15) | (pd.to_numeric(rest.get("pageViews", 0), errors="coerce").fillna(0) <= 1)].sort_values("visitDuration").head(1)
        if not short.empty:
            selected.append((short, "короткий отказной визит в проблемном сегменте"))
        picked = pd.concat([x[0].assign(_watch_type=x[1]) for x in selected if not x[0].empty]).head(per_segment)
        for _, row in picked.iterrows():
            reason = (
                f"Представитель проблемного сегмента {seg['segment_name']}: CR сегмента {seg['CR']}% ниже среднего {seg['baseline_CR']}%, "
                f"визит {_duration_ru(row.get('visitDuration'))}, {int(float(row.get('pageViews') or 0))} просмотра(ов), регистрации нет. {row.get('_watch_type')}."
            )
            item = {"segment_name": seg["segment_name"], "priority_score": seg["priority_score"], "reason_to_watch": reason}
            for col in ["visitID", "dateTime", "deviceCategory", "UTMSource", "UTMCampaign", "startURL", "endURL", "visitDuration", "pageViews", "goalsID"]:
                item[col] = row.get(col, "")
            rows.append(item)
    return pd.DataFrame(rows)


def webvisor_filter_table(records: pd.DataFrame) -> pd.DataFrame:
    if records.empty:
        return pd.DataFrame()
    tmp = records.copy()
    tmp["date"] = pd.to_datetime(tmp.get("dateTime"), errors="coerce").dt.date.astype(str)
    return tmp.groupby("segment_name", dropna=False).agg(
        date=("date", lambda s: ", ".join(sorted(set(x for x in s if x != "NaT")))[:200]),
        device=("deviceCategory", lambda s: ", ".join(sorted(set(map(str, s))))),
        UTMSource=("UTMSource", lambda s: ", ".join(sorted(set(map(str, s))))),
        UTMCampaign=("UTMCampaign", lambda s: ", ".join(sorted(set(map(str, s))))),
        startURL=("startURL", lambda s: ", ".join(sorted(set(map(str, s))))[:300]),
        endURL=("endURL", lambda s: ", ".join(sorted(set(map(str, s))))[:300]),
        visitID=("visitID", lambda s: ", ".join(map(str, s))),
    ).reset_index()


def analyze_hits(hits: pd.DataFrame, scored: pd.DataFrame | None = None) -> dict[str, pd.DataFrame | str]:
    hits = hits.copy()
    hits.columns = [c.replace("ym:pv:", "") for c in hits.columns]
    if hits.empty or "visitID" not in hits:
        return {"warning": "Анализ построен только на visits. Он умеет искать проблемные сегменты, но не видит клики, скролл и путь внутри визита. Для анализа пути включите hits или добавьте события."}
    hits["path"] = _text_series(hits, "URL").map(_path)
    per_visit = hits.groupby("visitID").agg(urls_in_chain=("path", "nunique"), hits=("path", "count")).reset_index()
    url_freq = hits["path"].value_counts().rename_axis("URL").reset_index(name="hits").head(20)
    exits = hits.sort_values(["visitID", "dateTime" if "dateTime" in hits else "path"]).groupby("visitID").tail(1)["path"].value_counts().rename_axis("exitURL").reset_index(name="exits").head(20)
    goal_cols = [c for c in ["goalsID", "params", "title"] if c in hits]
    event_freq = pd.DataFrame()
    if goal_cols:
        event_text = hits[goal_cols].astype(str).agg(" ".join, axis=1)
        event_freq = event_text[event_text.str.strip().ne("")].value_counts().rename_axis("event_or_goal").reset_index(name="hits").head(20)
    result = {"per_visit": per_visit, "url_freq": url_freq, "exits": exits, "event_freq": event_freq}
    chains = hits.sort_values(["visitID", "dateTime" if "dateTime" in hits else "path"]).groupby("visitID")["path"].apply(lambda s: " → ".join(s.astype(str).head(6))).reset_index(name="url_chain")
    if scored is not None and not scored.empty and "visitID" in scored:
        meta = scored[["visitID", "registered", "endURL"]].copy()
        meta["visitID"] = meta["visitID"].astype(str)
        chains["visitID"] = chains["visitID"].astype(str)
        chains_meta = chains.merge(meta, on="visitID", how="left")
        nonconv = chains_meta[~chains_meta.get("registered", pd.Series(False, index=chains_meta.index)).astype(bool)]
        conv = chains_meta[chains_meta.get("registered", pd.Series(False, index=chains_meta.index)).astype(bool)]
        result["nonconverter_chains"] = nonconv["url_chain"].value_counts().rename_axis("url_chain").reset_index(name="visits").head(20)
        result["converter_chains"] = conv["url_chain"].value_counts().rename_axis("url_chain").reset_index(name="visits").head(20)
        result["nonconverter_end_urls"] = nonconv["endURL"].map(_path).value_counts().rename_axis("endURL").reset_index(name="visits").head(20)
        if "startURL" in scored:
            selected_paths = scored["startURL"].map(_path).dropna().astype(str).unique().tolist()[:10]
            next_rows = []
            for selected in selected_paths:
                for chain in nonconv["url_chain"].dropna():
                    parts = chain.split(" → ")
                    for i, part in enumerate(parts[:-1]):
                        if part == selected:
                            next_rows.append({"selected_URL": selected, "next_URL": parts[i + 1]})
            result["after_selected_url"] = pd.DataFrame(next_rows).value_counts().reset_index(name="visits").head(20) if next_rows else pd.DataFrame()
    return result
