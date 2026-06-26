from __future__ import annotations

import datetime as dt
import re

import pandas as pd
import streamlit as st

try:
    from metrika_client import MetrikaAPIError, MetrikaLogsClient, get_metrika_token
except Exception as exc:
    st.error("Ошибка при импорте модулей приложения. Проверьте зависимости и конфигурацию деплоя.")
    st.exception(exc)
    st.stop()


EXPERIMENTAL_MODE = False
DEFAULT_COUNTER_ID = 18477952
DEFAULT_URL_CONTAINS = "chat"
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
    "goal_reached",
    "lastTrafficSource",
    "UTMSource",
    "UTMCampaign",
    "deviceCategory",
    "browser",
    "regionCountry",
    "regionCity",
]


@st.cache_data(show_spinner=False, ttl=3600)
def load_visits(counter_id: int, date_from: str, date_to: str, url_contains: str) -> pd.DataFrame:
    """Load only visits from Yandex Metrica Logs API for the stable app flow."""
    return MetrikaLogsClient().fetch_visits(counter_id, date_from, date_to, url_contains)


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _normalize_visit_columns(visits: pd.DataFrame) -> pd.DataFrame:
    visits = visits.copy()
    visits.columns = [column.replace("ym:s:", "") for column in visits.columns]
    return visits


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


def _mark_goal(visits: pd.DataFrame, goal_id: str) -> pd.DataFrame:
    visits = _normalize_visit_columns(visits)
    goal_ids = _goal_ids(goal_id)
    if not goal_ids or "goalsID" not in visits:
        return visits
    visits["goal_reached"] = visits["goalsID"].map(lambda value: bool(_goal_ids(value).intersection(goal_ids)))
    return visits


def _render_connection_status() -> bool:
    if get_metrika_token():
        st.success("Токен Яндекс Метрики найден. Можно загружать visits через Logs API.")
        return True

    st.warning(
        "YANDEX_METRIKA_TOKEN не задан. Приложение открылось стабильно, но загрузка visits недоступна до настройки секрета."
    )
    st.info("Добавьте YANDEX_METRIKA_TOKEN в переменные окружения или Streamlit secrets и перезапустите деплой.")
    return False


def _render_sidebar() -> tuple[int, dt.date, dt.date, str, str, bool]:
    with st.sidebar:
        st.header("Параметры Метрики")
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
        goal_id = st.text_input("ID цели", value="2898778", help="Один или несколько ID целей для пометки goal_reached в таблице.")
        load = st.button("Загрузить visits", type="primary")
    return int(counter_id), date_from, date_to, url_contains.strip(), goal_id.strip(), load


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


def _render_goal_diagnostics(visits: pd.DataFrame, goal_id: str) -> None:
    st.subheader("Диагностика целей")

    total_visits = len(visits)
    entered_goal_ids = sorted(_goal_ids(goal_id), key=int)

    if "goalsID" not in visits:
        st.warning("В выгрузке нет колонки goalsID.")
        st.write(f"Всего визитов в выгрузке: **{total_visits}**")
        st.write(f"ID целей, введенные пользователем: **{', '.join(entered_goal_ids) or '—'}**")
        return

    goals = visits["goalsID"]
    parsed_goals = goals.map(_goal_ids)
    visits_with_goals = int(parsed_goals.map(bool).sum())
    goal_counts = parsed_goals.explode().dropna().value_counts().rename_axis("goalID").reset_index(name="visits")
    unique_goal_ids = sorted(goal_counts["goalID"].astype(str).tolist(), key=int) if not goal_counts.empty else []
    found_goal_ids = sorted(set(entered_goal_ids).intersection(unique_goal_ids), key=int)

    col1, col2 = st.columns(2)
    col1.metric("Визитов всего в выгрузке", total_visits)
    col2.metric("Визитов с непустым goalsID", visits_with_goals)
    st.write(f"Уникальные goalsID в выборке: **{', '.join(unique_goal_ids) or '—'}**")
    st.write(f"ID целей, введенные пользователем: **{', '.join(entered_goal_ids) or '—'}**")
    st.write(f"Введенные ID, найденные в выборке: **{', '.join(found_goal_ids) or '—'}**")

    if not found_goal_ids and entered_goal_ids:
        st.warning(
            "В выбранной выгрузке не найдено ни одного из указанных ID целей. "
            "Проверьте ID целей, счетчик, URL-фильтр и дату."
        )

    st.write("Сколько раз встречается каждый goalID")
    if goal_counts.empty:
        st.info("В выборке нет визитов с goalsID.")
    else:
        st.dataframe(goal_counts, use_container_width=True, hide_index=True)

    st.subheader("Примеры визитов с goalsID")
    examples = visits.loc[parsed_goals.map(bool)].copy()
    if examples.empty:
        st.info("Нет примеров визитов с непустым goalsID.")
        return

    examples["parsed_goals"] = parsed_goals.loc[examples.index].map(lambda ids: ", ".join(sorted(ids, key=int)))
    if "registered" not in examples:
        examples["registered"] = parsed_goals.loc[examples.index].map(lambda ids: bool(set(entered_goal_ids).intersection(ids)))
    example_cols = ["visitID", "dateTime", "startURL", "endURL", "goalsID", "parsed_goals", "registered"]
    existing_cols = [column for column in example_cols if column in examples]
    st.dataframe(examples[existing_cols].head(50), use_container_width=True, hide_index=True)


def _render_visits_table(visits: pd.DataFrame) -> None:
    st.subheader("Таблица визитов")
    if visits.empty:
        st.info("Visits не найдены для выбранных параметров.")
        return

    cols = [column for column in VISIT_TABLE_COLUMNS if column in visits]
    st.dataframe(visits[cols] if cols else visits, use_container_width=True, hide_index=True)
    st.download_button("CSV-экспорт visits", csv_bytes(visits), "metrika_visits.csv", "text/csv")


def main() -> None:
    st.set_page_config(page_title="Metrika Visits", layout="wide")
    st.title("Metrika Visits")
    st.caption("Минимальная стабильная версия: подключение к Метрике, параметры загрузки, visits, таблица и CSV-экспорт.")

    token_available = _render_connection_status()
    counter_id, date_from, date_to, url_contains, goal_id, load = _render_sidebar()

    if load and _validate_inputs(date_from, date_to, url_contains, token_available):
        try:
            with st.spinner("Загружаем visits из Logs API..."):
                visits = load_visits(counter_id, str(date_from), str(date_to), url_contains)
                st.session_state["visits"] = _mark_goal(visits, goal_id)
        except MetrikaAPIError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error("Не удалось загрузить visits. Приложение продолжает работать, проверьте параметры и попробуйте снова.")
            st.exception(exc)

    visits = st.session_state.get("visits")
    if visits is None:
        st.info("Введите counter_id, date_from, date_to, URL-фильтр и ID цели, затем нажмите «Загрузить visits».")
        return

    _render_goal_diagnostics(visits, goal_id)
    _render_visits_table(visits)

    if EXPERIMENTAL_MODE:
        st.divider()
        st.info("Экспериментальные функции включены, но не участвуют в основном стабильном потоке.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error("Критическая ошибка при запуске приложения.")
        st.exception(exc)
