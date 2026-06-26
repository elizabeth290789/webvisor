from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
except Exception:
    go = None

try:
    from metrika_client import MetrikaAPIError, MetrikaLogsClient, get_metrika_token
except Exception as exc:
    st.error("Ошибка при импорте модулей приложения. Проверьте зависимости и конфигурацию деплоя.")
    st.exception(exc)
    st.stop()


EXPERIMENTAL_MODE = False
DEFAULT_COUNTER_ID = 18477952
DEFAULT_URL_CONTAINS = "/promo/b24messenger/team/"
VISIT_TABLE_COLUMNS = [
    "visitID",
    "clientID",
    "dateTime",
    "startURL",
    "endURL",
    "pageViews",
    "visitDuration",
    "bounce",
    "goalsID",
    "selected_goal_reached",
    "lastTrafficSource",
    "UTMSource",
    "UTMCampaign",
    "deviceCategory",
    "browser",
    "regionCountry",
    "regionCity",
]


@st.cache_data(show_spinner=False, ttl=3600)
def load_visits_and_hits(
    counter_id: int,
    date_from: str,
    date_to: str,
    url_contains: str,
    load_hits: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load visits and optionally hits from Yandex Metrica Logs API."""
    return MetrikaLogsClient().fetch_visits_and_hits(counter_id, date_from, date_to, url_contains, load_hits)


@st.cache_data(show_spinner=False, ttl=3600)
def load_user_path_report(
    counter_id: int,
    date_from: str,
    date_to: str,
    url_contains: str,
    mode: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load visits and complete hit chains for the User Paths report."""
    return MetrikaLogsClient().fetch_user_path_report(counter_id, date_from, date_to, url_contains, mode)


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [column.replace("ym:s:", "").replace("ym:pv:", "") for column in frame.columns]
    return frame


def _goal_ids(value: object) -> set[str]:
    if value is None:
        text = ""
    else:
        try:
            text = "" if pd.isna(value) else str(value)
        except (TypeError, ValueError):
            text = str(value)
    text = re.sub(r"\b(\d+)\.0+\b", r"\1", text)
    return set(re.findall(r"\d+", text))


def _selected_goal_ids(goal_id: str) -> list[str]:
    return sorted(_goal_ids(goal_id), key=int)


def _path(value: object) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return "—"
    parsed = urlparse(text if "://" in text else f"https://placeholder{text if text.startswith('/') else '/' + text}")
    path = parsed.path or "/"
    return path.rstrip("/") or "/"


def _path_chain(paths: pd.Series) -> str:
    normalized = [_path(value) for value in paths if _path(value) != "—"]
    deduped: list[str] = []
    for path in normalized:
        if not deduped or deduped[-1] != path:
            deduped.append(path)
    return " → ".join(deduped) if deduped else "—"


def _mark_selected_goals(visits: pd.DataFrame, goal_id: str) -> pd.DataFrame:
    visits = _normalize_columns(visits)
    selected_ids = set(_selected_goal_ids(goal_id))
    if not selected_ids or "goalsID" not in visits:
        visits["selected_goal_reached"] = False
        return visits
    visits["selected_goal_reached"] = visits["goalsID"].map(lambda value: bool(_goal_ids(value).intersection(selected_ids)))
    return visits


def _normalize_url(value: object, full_url: bool = False) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return "—"
    parsed = urlparse(text if "://" in text else f"https://placeholder{text if text.startswith('/') else '/' + text}")
    path = parsed.path or "/"
    path = path.rstrip("/") or "/"
    if full_url and parsed.netloc and parsed.netloc != "placeholder":
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return path


def _contains_url(sequence: list[str], needle: str) -> bool:
    needle_path = _normalize_url(needle, False)
    return any(needle_path in url or str(needle) in url for url in sequence)


def _dedupe_sequence(urls: list[str]) -> list[str]:
    result = []
    for url in urls:
        if url != "—" and (not result or result[-1] != url):
            result.append(url)
    return result


def _build_visit_paths(visits: pd.DataFrame, hits: pd.DataFrame, selected_ids: list[str], url_filter: str, max_steps: int, mode: str, full_url: bool) -> pd.DataFrame:
    visits = _normalize_columns(visits)
    hits = _normalize_columns(hits)
    if visits.empty or hits.empty or "visitID" not in hits or "URL" not in hits:
        return pd.DataFrame()
    hits = hits.copy()
    hits["visitID"] = hits["visitID"].astype(str)
    visits = visits.copy()
    visits["visitID"] = visits["visitID"].astype(str)
    if "dateTime" in hits:
        hits = hits.sort_values(["visitID", "dateTime"])
    selected = set(selected_ids)
    rows = []
    hit_goals = {}
    if "goalsID" in hits and selected:
        for vid, group in hits.groupby("visitID"):
            hit_goals[vid] = group["goalsID"].map(lambda v: bool(_goal_ids(v).intersection(selected))).tolist()
    for vid, group in hits.groupby("visitID"):
        seq = _dedupe_sequence([_normalize_url(v, full_url) for v in group["URL"].tolist()])
        if not seq:
            continue
        full_seq = seq[:]
        target_hit_known = False
        if mode == "Пути после выбранной страницы":
            idx = next((i for i, u in enumerate(seq) if _normalize_url(url_filter, full_url) in u or url_filter in u), None)
            if idx is None:
                continue
            seq = seq[idx:]
        elif mode == "Пути до цели":
            flags = hit_goals.get(vid, [])
            goal_idx = next((i for i, flag in enumerate(flags) if flag), None)
            if goal_idx is not None:
                seq = _dedupe_sequence([_normalize_url(v, full_url) for v in group.iloc[: goal_idx + 1]["URL"].tolist()])
                target_hit_known = True
        rows.append({"visitID": vid, "path_steps": seq[:max_steps], "full_path_steps": full_seq, "target_hit_known": target_hit_known})
    paths = pd.DataFrame(rows)
    if paths.empty:
        return paths
    keep_cols = [c for c in ["visitID", "dateTime", "startURL", "endURL", "pageViews", "visitDuration", "bounce", "goalsID", "selected_goal_reached", "lastTrafficSource", "UTMSource", "UTMCampaign", "deviceCategory"] if c in visits]
    paths = paths.merge(visits[keep_cols], on="visitID", how="left")
    paths["target_reached"] = paths.get("selected_goal_reached", False).fillna(False).astype(bool)
    if mode == "Пути от страницы входа":
        paths = paths[paths["startURL"].map(lambda v: url_filter in str(v) or _normalize_url(url_filter) in _normalize_url(v))]
    elif mode == "Пути до цели":
        paths = paths[paths["target_reached"]]
    elif mode == "Пути без цели":
        paths = paths[~paths["target_reached"]]
    paths["path"] = paths["path_steps"].map(lambda steps: " → ".join(steps) if steps else "—")
    paths["path_length"] = paths["path_steps"].map(len)
    paths["exit_after_first"] = paths["path_length"] <= 1
    paths["exit_url"] = paths["path_steps"].map(lambda steps: steps[-1] if steps else "—")
    return paths


def _aggregate_paths(paths: pd.DataFrame) -> pd.DataFrame:
    if paths.empty:
        return pd.DataFrame()
    df = paths.copy()
    df["pageViews"] = pd.to_numeric(df.get("pageViews"), errors="coerce")
    df["visitDuration"] = pd.to_numeric(df.get("visitDuration"), errors="coerce")
    table = df.groupby("path", dropna=False).agg(visits=("visitID", "nunique"), target_visits=("target_reached", "sum"), exits=("exit_after_first", "sum"), avg_pageViews=("pageViews", "mean"), avg_visitDuration=("visitDuration", "mean")).reset_index()
    table["share"] = table["visits"] / max(table["visits"].sum(), 1)
    table["CR"] = table["target_visits"] / table["visits"]
    table["exit_rate"] = table["exits"] / table["visits"]
    return table.sort_values("visits", ascending=False)


def _transitions(paths: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for _, row in paths.iterrows():
        steps=row["path_steps"]
        for i in range(len(steps)-1):
            rows.append({"step": f"{i+1} → {i+2}", "from_url": steps[i], "to_url": steps[i+1], "visitID": row["visitID"], "target_reached": row["target_reached"]})
    if not rows:
        return pd.DataFrame(columns=["step","from_url","to_url","visits","share_from_previous_step","target_visits","CR","exits","exit_rate"])
    df=pd.DataFrame(rows)
    table=df.groupby(["step","from_url","to_url"]).agg(visits=("visitID","nunique"), target_visits=("target_reached","sum")).reset_index()
    totals=table.groupby(["step","from_url"])["visits"].transform("sum")
    table["share_from_previous_step"]=table["visits"]/totals
    table["CR"]=table["target_visits"]/table["visits"]
    table["exits"]=0; table["exit_rate"]=0.0
    return table.sort_values("visits", ascending=False)

def _render_connection_status() -> bool:
    if get_metrika_token():
        st.success("Токен Яндекс Метрики найден. Можно загружать visits через Logs API.")
        return True

    st.warning(
        "YANDEX_METRIKA_TOKEN не задан. Приложение открылось стабильно, но загрузка visits недоступна до настройки секрета."
    )
    st.info("Добавьте YANDEX_METRIKA_TOKEN в переменные окружения или Streamlit secrets и перезапустите деплой.")
    return False


def _render_sidebar() -> tuple[int, dt.date, dt.date, str, str, bool, bool]:
    with st.sidebar:
        st.header("Параметры отчета")
        counter_id = st.number_input("counter_id", min_value=1, value=DEFAULT_COUNTER_ID, step=1)
        today = dt.date.today()
        yesterday = today - dt.timedelta(days=1)
        date_from = st.date_input("date_from", yesterday, max_value=yesterday)
        date_to = st.date_input("date_to", yesterday, max_value=yesterday)
        st.caption("Выбирайте завершенные даты: текущий день в Logs API может быть неполным или недоступным.")
        url_contains = st.text_input(
            "URL-фильтр",
            value=DEFAULT_URL_CONTAINS,
            help="Обязательный фильтр по startURL или endURL, чтобы не выгружать весь счетчик.",
        )
        goal_id = st.text_input("ID целей", value="2898778", help="Один или несколько ID целей через запятую.")
        load_hits = st.checkbox("Загрузить hits для полного отчета по путям", value=False)
        load = st.button("Загрузить отчет", type="primary")
    return int(counter_id), date_from, date_to, url_contains.strip(), goal_id.strip(), load_hits, load


def _validate_inputs(date_from: dt.date, date_to: dt.date, url_contains: str, token_available: bool) -> bool:
    yesterday = dt.date.today() - dt.timedelta(days=1)
    if not token_available:
        st.error("Сначала настройте YANDEX_METRIKA_TOKEN для подключения к Метрике.")
        return False
    if date_to < date_from:
        st.error("date_to должен быть не раньше date_from.")
        return False
    if date_to > yesterday:
        st.error("date_to не должен быть позже вчерашнего дня.")
        return False
    if not url_contains:
        st.error("Заполните URL-фильтр, чтобы не выгружать весь счетчик.")
        return False
    return True


def _goal_breakdown(visits: pd.DataFrame, selected_ids: list[str]) -> pd.DataFrame:
    rows = []
    for goal in selected_ids:
        count = int(visits["goalsID"].map(lambda value: goal in _goal_ids(value)).sum()) if "goalsID" in visits else 0
        rows.append({"goalID": goal, "visits": count})
    return pd.DataFrame(rows)


def _render_summary(visits: pd.DataFrame, selected_ids: list[str], url_contains: str, date_from: dt.date, date_to: dt.date) -> None:
    st.header("1. Сводка")
    total = len(visits)
    target = int(visits["selected_goal_reached"].sum()) if "selected_goal_reached" in visits else 0
    cr = target / total if total else 0
    found = target > 0
    st.success("Цели найдены: да") if found else st.warning("Цели не найдены — проверьте ID цели, дату, счетчик и URL-фильтр.")
    c1, c2, c3 = st.columns(3)
    c1.metric("URL-фильтр", url_contains)
    c2.metric("Период", f"{date_from} — {date_to}")
    c3.metric("Всего визитов", total)
    c4, c5 = st.columns(2)
    c4.metric("Визитов с выбранными целями", target)
    c5.metric("CR в выбранные цели", f"{cr:.2%}")
    st.write("Разбивка только по выбранным ID целей")
    st.dataframe(_goal_breakdown(visits, selected_ids), use_container_width=True, hide_index=True)


def _paths_from_hits(visits: pd.DataFrame, hits: pd.DataFrame) -> pd.DataFrame:
    hits = _normalize_columns(hits)
    if hits.empty or "visitID" not in hits or "URL" not in hits:
        return pd.DataFrame()
    if "dateTime" in hits:
        hits = hits.sort_values(["visitID", "dateTime"])
    chains = hits.groupby("visitID")["URL"].apply(_path_chain).reset_index(name="path")
    merged = chains.merge(visits[["visitID", "selected_goal_reached"]], on="visitID", how="left")
    exits = visits[["visitID", "endURL"]].copy()
    exits["exit_path"] = exits["endURL"].map(_path)
    merged = merged.merge(exits[["visitID", "exit_path"]], on="visitID", how="left")
    merged["is_exit_path"] = merged.apply(lambda row: str(row.get("path", "")).endswith(str(row.get("exit_path", ""))), axis=1)
    return merged


def _render_where_users_go(visits: pd.DataFrame, hits: pd.DataFrame) -> None:
    st.header("2. Куда уходят пользователи")
    if hits.empty:
        st.info("Без hits видим только вход и выход, не полный путь.")
        flows = visits.copy()
        flows["path"] = flows["startURL"].map(_path) + " → " + flows["endURL"].map(_path)
        table = flows.groupby("path", dropna=False).agg(visits=("visitID", "nunique"), target_goal_visits=("selected_goal_reached", "sum")).reset_index()
        table["CR"] = table["target_goal_visits"] / table["visits"]
        st.dataframe(table.sort_values("visits", ascending=False).head(10), use_container_width=True, hide_index=True)
        st.warning("Для полноценного отчета по путям включите hits.")
        return

    paths = _paths_from_hits(visits, hits)
    if paths.empty:
        st.info("Hits загружены, но не удалось собрать цепочки URL по visitID.")
        return
    table = paths.groupby("path", dropna=False).agg(
        visits=("visitID", "nunique"),
        target_goal_visits=("selected_goal_reached", "sum"),
        exits=("is_exit_path", "sum"),
    ).reset_index()
    table["CR"] = table["target_goal_visits"] / table["visits"]
    table["exit_rate"] = table["exits"] / table["visits"]
    st.dataframe(table.drop(columns="exits").sort_values("visits", ascending=False).head(10), use_container_width=True, hide_index=True)


def _converter_table(visits: pd.DataFrame, converted: bool, hits: pd.DataFrame) -> pd.DataFrame:
    subset = visits[visits["selected_goal_reached"] == converted].copy()
    if subset.empty:
        return pd.DataFrame(columns=["path", "visits", "share", "avg_pageViews", "avg_visitDuration"])
    paths = _paths_from_hits(visits, hits) if not hits.empty else pd.DataFrame()
    if not paths.empty:
        subset = subset.merge(paths[["visitID", "path"]], on="visitID", how="left")
    else:
        subset["path"] = subset["startURL"].map(_path) + " → " + subset["endURL"].map(_path)
    subset["pageViews"] = pd.to_numeric(subset.get("pageViews"), errors="coerce")
    subset["visitDuration"] = pd.to_numeric(subset.get("visitDuration"), errors="coerce")
    table = subset.groupby("path", dropna=False).agg(
        visits=("visitID", "nunique"),
        avg_pageViews=("pageViews", "mean"),
        avg_visitDuration=("visitDuration", "mean"),
    ).reset_index()
    table["share"] = table["visits"] / len(subset)
    return table[["path", "visits", "share", "avg_pageViews", "avg_visitDuration"]].sort_values("visits", ascending=False).head(10)


def _render_converters(visits: pd.DataFrame, hits: pd.DataFrame) -> None:
    st.header("3. Конвертеры vs неконвертеры")
    left, right = st.columns(2)
    left.subheader("Топ путей с выбранной целью")
    left.dataframe(_converter_table(visits, True, hits), use_container_width=True, hide_index=True)
    right.subheader("Топ путей без выбранной цели")
    right.dataframe(_converter_table(visits, False, hits), use_container_width=True, hide_index=True)


def _watch_reason(row: pd.Series) -> tuple[str, str]:
    path = str(row.get("path", ""))
    if "prices" in path or "pricing" in path:
        return "landing → prices → выход", "Пользователь ушел на цены и не зарегистрировался — проверить, не возникает ли барьер цены до CTA."
    if "auth/create" in path or "create/auth" in path or "signup" in path or "register" in path:
        return "landing → auth/create → выход без цели", "Пользователь дошел до create/auth, но цель не сработала — проверить, не было ли проблемы на шаге регистрации."
    if row.get("pageViews", 0) >= 4 or row.get("visitDuration", 0) >= 120:
        return "длинный визит без цели", "Пользователь долго изучал сайт, но не достиг цели — проверить, где теряется следующий шаг к CTA."
    return "landing → выход", "Пользователь ушел после входа без цели — проверить релевантность первого экрана и понятность CTA."


def _render_webvisor_watchlist(visits: pd.DataFrame, hits: pd.DataFrame) -> None:
    st.header("4. Что смотреть в Вебвизоре")
    candidates = visits[~visits["selected_goal_reached"]].copy()
    candidates["pageViews"] = pd.to_numeric(candidates.get("pageViews"), errors="coerce").fillna(0)
    candidates["visitDuration"] = pd.to_numeric(candidates.get("visitDuration"), errors="coerce").fillna(0)
    if candidates.empty:
        st.info("Нет визитов без выбранной цели для просмотра.")
        return
    if not hits.empty:
        paths = _paths_from_hits(visits, hits)
        candidates = candidates.merge(paths[["visitID", "path"]], on="visitID", how="left")
    if "path" not in candidates:
        candidates["path"] = candidates["startURL"].map(_path) + " → " + candidates["endURL"].map(_path)
    candidates[["reason_group", "reason_to_watch"]] = candidates.apply(lambda row: pd.Series(_watch_reason(row)), axis=1)
    candidates["priority"] = candidates["reason_group"].map({
        "landing → выход": 1,
        "landing → prices → выход": 2,
        "landing → auth/create → выход без цели": 3,
        "длинный визит без цели": 4,
    }).fillna(9)
    candidates = candidates.sort_values(["priority", "visitDuration", "pageViews"], ascending=[True, False, False])
    cols = ["reason_group", "visitID", "dateTime", "deviceCategory", "UTMSource", "UTMCampaign", "path", "reason_to_watch"]
    st.dataframe(candidates[[column for column in cols if column in candidates]].head(20), use_container_width=True, hide_index=True)


def _render_debug(visits: pd.DataFrame, hits: pd.DataFrame, selected_ids: list[str]) -> None:
    with st.expander("Отладка", expanded=False):
        st.subheader("Техническая диагностика целей")
        if "goalsID" in visits:
            parsed_goals = visits["goalsID"].map(_goal_ids)
            goal_counts = parsed_goals.explode().dropna().value_counts().rename_axis("goalID").reset_index(name="visits")
            st.write(f"Все уникальные goalsID в выборке: **{', '.join(goal_counts['goalID'].astype(str).tolist()) or '—'}**")
            debug_goals = visits[[column for column in ["visitID", "goalsID"] if column in visits]].copy()
            debug_goals["parsed_goals"] = parsed_goals.map(lambda ids: ", ".join(sorted(ids, key=int)))
            st.dataframe(debug_goals.head(100), use_container_width=True, hide_index=True)
            st.dataframe(goal_counts, use_container_width=True, hide_index=True)
        st.subheader("Примеры сырых визитов")
        st.dataframe(visits.head(50), use_container_width=True, hide_index=True)
        st.download_button("CSV-экспорт visits", csv_bytes(visits), "metrika_visits.csv", "text/csv")
        if not hits.empty:
            st.subheader("Примеры сырых hits")
            st.dataframe(_normalize_columns(hits).head(50), use_container_width=True, hide_index=True)


def _render_sankey(transitions: pd.DataFrame, top_n: int) -> None:
    if go is None or transitions.empty:
        st.info("Sankey недоступен, показываем таблицу переходов.")
        return
    data = transitions.head(top_n)
    labels = pd.unique(data[["from_url", "to_url"]].values.ravel()).tolist()
    idx = {label: i for i, label in enumerate(labels)}
    fig = go.Figure(data=[go.Sankey(node={"label": labels}, link={"source": data["from_url"].map(idx), "target": data["to_url"].map(idx), "value": data["visits"]})])
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)


def _watchlist_from_paths(paths: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    src = paths.copy()
    src["pageViews"] = pd.to_numeric(src.get("pageViews"), errors="coerce").fillna(0)
    src["visitDuration"] = pd.to_numeric(src.get("visitDuration"), errors="coerce").fillna(0)
    groups = [
        ("Выход сразу после выбранной страницы", src[(~src["target_reached"]) & (src["path_length"] <= 1)], "Пользователь ушел сразу после лендинга — проверить первый экран, оффер и видимость CTA."),
        ("Переход на цены и выход", src[(~src["target_reached"]) & src["path"].str.contains("prices|pricing|цены", case=False, na=False)], "Пользователь перешел на цены и не зарегистрировался — проверить, не возникает ли ценовой барьер до регистрации."),
        ("Переход на register/create/auth без достижения цели", src[(~src["target_reached"]) & src["path"].str.contains("register|create|auth", case=False, na=False)], "Пользователь дошел до register/create/auth, но цель не сработала — проверить, не было ли проблемы на шаге регистрации."),
        ("Длинный путь без цели", src[(~src["target_reached"]) & (src["path_length"] >= 4)], "Пользователь долго ходил по сайту, но не достиг цели — проверить, где теряется следующий шаг к CTA."),
        ("Типичный путь с целью", src[src["target_reached"]], "Типичный конверсионный путь — использовать как эталон для сравнения с неконверсионными."),
    ]
    for name, frame, reason in groups:
        for _, row in frame.sort_values(["visitDuration", "pageViews"], ascending=False).head(4).iterrows():
            item = row.to_dict(); item["reason_group"] = name; item["reason_to_watch"] = reason; rows.append(item)
    cols=["reason_group","visitID","dateTime","deviceCategory","UTMSource","UTMCampaign","path","visitDuration","pageViews","reason_to_watch"]
    return pd.DataFrame(rows)[cols] if rows else pd.DataFrame(columns=cols)


def _render_user_paths_tab(token_available: bool) -> None:
    st.header("Пути пользователей")
    st.warning("Для отчета по путям загружаются hits. На большом счетчике выбирайте один завершенный день и точный URL-фильтр, иначе выгрузка может занять много времени.")
    today=dt.date.today(); yesterday=today-dt.timedelta(days=1)
    c1,c2,c3=st.columns(3)
    counter_id=c1.number_input("counter_id", min_value=1, value=DEFAULT_COUNTER_ID, step=1, key="paths_counter")
    date_from=c2.date_input("date_from", yesterday, max_value=yesterday, key="paths_from")
    date_to=c3.date_input("date_to", yesterday, max_value=yesterday, key="paths_to")
    url_filter=st.text_input("URL-фильтр / стартовая страница", DEFAULT_URL_CONTAINS, key="paths_url")
    goal_id=st.text_input("ID целей", "2898778,300089493", key="paths_goals")
    selected_ids=_selected_goal_ids(goal_id)
    c4,c5,c6=st.columns(3)
    device=c4.selectbox("Устройство", ["все","desktop","mobile","tablet"], key="paths_device")
    max_steps=c5.selectbox("Максимум шагов пути", [3,4,5,6], index=1, key="paths_steps")
    mode=c6.selectbox("Режим построения пути", ["Пути от страницы входа","Пути после выбранной страницы","Пути до цели","Пути без цели"], key="paths_mode")
    c7,c8,c9=st.columns(3)
    utm_source=c7.text_input("UTMSource содержит", key="paths_utm_source")
    utm_campaign=c8.text_input("UTMCampaign содержит", key="paths_utm_campaign")
    full_url=c9.checkbox("Показывать полный URL", value=False, key="paths_full_url")
    if (date_to-date_from).days > 1: st.warning("Выбран широкий период. Для стабильной выгрузки лучше выбрать один завершенный день.")
    if st.button("Загрузить пути пользователей", type="primary"):
        if _validate_inputs(date_from, date_to, url_filter.strip(), token_available):
            with st.spinner("Загружаем visits и hits для путей..."):
                visits,hits=load_user_path_report(int(counter_id), str(date_from), str(date_to), url_filter.strip(), mode)
                st.session_state["path_visits"]=_mark_selected_goals(visits, goal_id)
                st.session_state["path_hits"]=_normalize_columns(hits)
                st.session_state["path_params"]=(selected_ids,url_filter.strip(),max_steps,mode,full_url)
    visits=st.session_state.get("path_visits"); hits=st.session_state.get("path_hits", pd.DataFrame())
    if visits is None:
        st.info("Укажите параметры и нажмите «Загрузить пути пользователей»."); return
    if hits.empty:
        st.error("Hits не загружены или пустые — невозможно построить полную цепочку страниц."); return
    selected_ids,url_filter,max_steps,mode,full_url=st.session_state.get("path_params", (selected_ids,url_filter,max_steps,mode,full_url))
    paths=_build_visit_paths(visits,hits,selected_ids,url_filter,max_steps,mode,full_url)
    if device != "все" and "deviceCategory" in paths:
        paths = paths[paths["deviceCategory"].astype(str).str.lower().eq(device)]
    if utm_source and "UTMSource" in paths:
        paths = paths[paths["UTMSource"].astype(str).str.contains(utm_source, case=False, na=False)]
    if utm_campaign and "UTMCampaign" in paths:
        paths = paths[paths["UTMCampaign"].astype(str).str.contains(utm_campaign, case=False, na=False)]
    c10,c11,c12,c13=st.columns(4)
    goal_filter=c10.selectbox("Цель", ["все","только с целью","только без цели"], key="paths_goal_filter")
    include_url=c11.text_input("Содержит URL в пути", key="paths_include")
    exclude_url=c12.text_input("Исключить URL из пути", key="paths_exclude")
    min_visits=c13.number_input("Минимум визитов в пути", min_value=1, value=1, step=1)
    top_n=st.slider("Показывать top-N путей", 5, 100, 20)
    if goal_filter=="только с целью": paths=paths[paths["target_reached"]]
    if goal_filter=="только без цели": paths=paths[~paths["target_reached"]]
    if include_url: paths=paths[paths["path"].str.contains(include_url,case=False,na=False)]
    if exclude_url: paths=paths[~paths["path"].str.contains(exclude_url,case=False,na=False)]
    if paths.empty:
        st.warning("По выбранным фильтрам данных нет."); return
    total=len(paths); target=int(paths["target_reached"].sum())
    m=st.columns(7)
    m[0].metric("Визиты", total); m[1].metric("Целевые визиты", target); m[2].metric("CR", f"{target/total:.2%}")
    m[3].metric("Средняя глубина", f"{paths['path_length'].mean():.1f}"); m[4].metric("Средняя длительность", f"{pd.to_numeric(paths.get('visitDuration'), errors='coerce').mean():.0f} сек")
    agg=_aggregate_paths(paths); agg=agg[agg["visits"]>=min_visits]
    m[5].metric("Уникальные пути", len(agg)); m[6].metric("Выход после 1 шага", f"{paths['exit_after_first'].mean():.2%}")
    if target == 0: st.warning("Выбранные цели не найдены в этой выборке.")
    trans=_transitions(paths)
    st.subheader("Интерактивный flow-отчет")
    _render_sankey(trans, top_n)
    step=st.selectbox("Шаг перехода", sorted(trans["step"].unique().tolist()) if not trans.empty else [])
    if step: st.dataframe(trans[trans["step"]==step].head(top_n), use_container_width=True, hide_index=True)
    st.subheader("Топ путей")
    preset=st.selectbox("Фильтр путей", ["все пути","только с целью","только без цели","только пути с выходом","только пути через /prices/","только пути через /register/ или /create/","только длинные пути"])
    top=agg.copy()
    if preset=="только с целью": top=top[top["target_visits"]>0]
    elif preset=="только без цели": top=top[top["target_visits"]==0]
    elif preset=="только пути с выходом": top=top[top["exits"]>0]
    elif preset=="только пути через /prices/": top=top[top["path"].str.contains("/prices/|/prices|pricing", case=False, na=False)]
    elif preset=="только пути через /register/ или /create/": top=top[top["path"].str.contains("/register/|/register|/create/|/create", case=False, na=False)]
    elif preset=="только длинные пути": top=top[top["path"].str.count("→")>=3]
    st.dataframe(top.head(top_n), use_container_width=True, hide_index=True)
    st.subheader("Конвертеры vs неконвертеры")
    l,r=st.columns(2); l.dataframe(_aggregate_paths(paths[paths["target_reached"]]).rename(columns={"avg_visitDuration":"avg_duration"}).head(10), use_container_width=True, hide_index=True); r.dataframe(_aggregate_paths(paths[~paths["target_reached"]]).rename(columns={"avg_visitDuration":"avg_duration"}).head(10), use_container_width=True, hide_index=True)
    st.subheader("Куда уходят после выбранной страницы")
    next_rows=[]
    for _, row in paths.iterrows():
        steps=row["path_steps"]; next_rows.append({"next_url": steps[1] if len(steps)>1 else "Выход", "visitID": row["visitID"], "target_reached": row["target_reached"], "exit": len(steps)<=1})
    nt=pd.DataFrame(next_rows).groupby("next_url").agg(visits=("visitID","nunique"), target_visits=("target_reached","sum"), exits=("exit","sum")).reset_index(); nt["share"]=nt["visits"]/nt["visits"].sum(); nt["CR"]=nt["target_visits"]/nt["visits"]; nt["exit_rate"]=nt["exits"]/nt["visits"]
    st.dataframe(nt.sort_values("visits", ascending=False).head(top_n), use_container_width=True, hide_index=True)
    st.subheader("Где пользователи отваливаются")
    drop=[]
    for n in range(1, int(max_steps)+1):
        for url in paths["path_steps"].map(lambda x: x[n-1] if len(x)>=n else None).dropna().unique():
            at=paths[paths["path_steps"].map(lambda x: len(x)>=n and x[n-1]==url)]; ex=at[at["path_steps"].map(len)==n]
            drop.append({"step_number":n,"current_url":url,"exits":len(ex),"exit_rate":len(ex)/max(len(at),1),"visits_at_step":len(at),"target_visits_from_this_step":int(at["target_reached"].sum()),"CR":at["target_reached"].mean()})
    st.dataframe(pd.DataFrame(drop).sort_values(["exits","visits_at_step"], ascending=False).head(top_n), use_container_width=True, hide_index=True)
    st.subheader("Записи для просмотра в Вебвизоре")
    st.dataframe(_watchlist_from_paths(paths), use_container_width=True, hide_index=True)
    with st.expander("Отладка", expanded=False):
        st.write("Запросы к Logs API: visits + hits по visitID выбранных визитов.")
        st.dataframe(visits.head(100), use_container_width=True, hide_index=True); st.dataframe(hits.head(100), use_container_width=True, hide_index=True)
        dbg=visits[[c for c in ["visitID","goalsID"] if c in visits]].copy(); dbg["parsed_goals"]=dbg.get("goalsID", pd.Series(dtype=object)).map(_goal_ids) if "goalsID" in dbg else ""
        st.dataframe(dbg.head(100), use_container_width=True, hide_index=True)

def main() -> None:
    st.set_page_config(page_title="Маркетинговый отчет Метрики", layout="wide")
    st.title("Маркетинговый отчет по URL, целям и путям пользователей")
    st.caption("Текущий отбор записей Вебвизора сохранен; отчет по путям вынесен в отдельный таб.")

    token_available = _render_connection_status()
    webvisor_tab, paths_tab = st.tabs(["Вебвизор: записи для просмотра", "Пути пользователей"])

    with webvisor_tab:
        counter_id, date_from, date_to, url_contains, goal_id, load_hits, load = _render_sidebar()
        selected_ids = _selected_goal_ids(goal_id)

        if load and _validate_inputs(date_from, date_to, url_contains, token_available):
            try:
                with st.spinner("Загружаем данные из Logs API..."):
                    visits, hits = load_visits_and_hits(counter_id, str(date_from), str(date_to), url_contains, load_hits)
                    st.session_state["visits"] = _mark_selected_goals(visits, goal_id)
                    st.session_state["hits"] = _normalize_columns(hits)
                    st.session_state["report_params"] = (url_contains, date_from, date_to, goal_id, load_hits)
            except MetrikaAPIError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error("Не удалось загрузить данные. Приложение продолжает работать, проверьте параметры и попробуйте снова.")
                st.exception(exc)

        visits = st.session_state.get("visits")
        if visits is None:
            st.info("Введите counter_id, период, URL-фильтр и ID целей, затем нажмите «Загрузить отчет».")
        else:
            hits = st.session_state.get("hits", pd.DataFrame())
            _render_summary(visits, selected_ids, url_contains, date_from, date_to)
            _render_where_users_go(visits, hits)
            if hits.empty:
                _render_webvisor_watchlist(visits, hits)
            else:
                _render_converters(visits, hits)
                _render_webvisor_watchlist(visits, hits)
            _render_debug(visits, hits, selected_ids)

    with paths_tab:
        _render_user_paths_tab(token_available)

    if EXPERIMENTAL_MODE:
        st.divider()
        st.info("Экспериментальные функции включены, но не участвуют в основном стабильном потоке.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error("Критическая ошибка при запуске приложения.")
        st.exception(exc)
