"""Human-readable CRO summaries for problematic Webvisor sessions."""
from __future__ import annotations

import os

import pandas as pd


def build_summary(scored: pd.DataFrame, full_sample: pd.DataFrame | None = None) -> str:
    sample = full_sample if full_sample is not None else scored
    top = scored.sort_values("score", ascending=False).head(20).copy()
    if sample.empty:
        return "Нет визитов для анализа."
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai_summary(top, sample)
        except Exception as exc:  # show fallback instead of breaking the app
            return f"OpenAI API недоступен ({exc}).\n\n" + _rule_summary(top, sample)
    return _rule_summary(top, sample)


def _openai_summary(top: pd.DataFrame, sample: pd.DataFrame) -> str:
    from openai import OpenAI

    cols = [c for c in ["priority_rank", "score", "visitID", "deviceCategory", "startURL", "endURL", "visitDuration", "pageViews", "goalsID", "UTMCampaign", "UTMSource", "reason_to_watch"] if c in top]
    csv_sample = top[cols].to_csv(index=False)
    prompt = (
        "Сформируй CRO-вывод по top-20 визитам Яндекс Метрики. Нужны: короткий вывод, "
        "проблемные сегменты, 3-5 visitID для просмотра первыми и блок 'Что смотреть в Вебвизоре' "
        "с фильтрами Метрики и проверками глазами. Пиши конкретно, без очевидных rule-based формулировок.\n\n"
        + csv_sample
    )
    client = OpenAI()
    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    return response.output_text


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _segment_label(row: pd.Series, cols: list[str]) -> str:
    return " × ".join(str(row.get(col) or "(not set)") for col in cols)


def _top_segment(df: pd.DataFrame, cols: list[str]) -> pd.Series | None:
    if df.empty or any(col not in df for col in cols):
        return None
    tmp = df.copy()
    for col in cols:
        tmp[col] = tmp[col].fillna("(not set)").astype(str).replace("", "(not set)")
    grouped = tmp.groupby(cols).agg(
        visits=("visitID", "count"),
        no_reg=("registered", lambda s: int((~s.astype(bool)).sum()) if "registered" in tmp else len(s)),
        avg_duration=("visitDuration", "mean"),
        avg_score=("score", "mean"),
    ).reset_index()
    return grouped.sort_values(["no_reg", "avg_score", "visits"], ascending=False).head(1).squeeze() if not grouped.empty else None


def _rule_summary(top: pd.DataFrame, sample: pd.DataFrame) -> str:
    no_reg_total = int((~sample.get("registered", pd.Series(False, index=sample.index)).astype(bool)).sum()) if "registered" in sample else len(sample)
    segment_candidates = [
        ("deviceCategory", ["deviceCategory"]),
        ("UTMCampaign", ["UTMCampaign"]),
        ("deviceCategory × UTMCampaign", ["deviceCategory", "UTMCampaign"]),
        ("startURL", ["startURL"]),
    ]
    segments: list[tuple[str, pd.Series]] = []
    for name, cols in segment_candidates:
        row = _top_segment(sample, cols)
        if row is not None:
            segments.append((name, row))

    top_ids = top.get("visitID", pd.Series(dtype=str)).head(5).astype(str).tolist()
    top_reasons = top.get("reason_to_watch", pd.Series(dtype=str)).head(5).astype(str).tolist()
    primary = segments[0] if segments else None
    lines = [
        "## Короткий вывод",
        f"В выборку попало {len(sample)} визитов; без регистрации — {no_reg_total}.",
    ]
    if primary:
        name, row = primary
        lines.append(
            f"Основная зона внимания — {name}: {_segment_label(row, [c for c in row.index if c in ['deviceCategory', 'UTMCampaign', 'startURL']])}. "
            f"Визитов {_fmt_num(float(row['visits']))}, без регистрации {_fmt_num(float(row['no_reg']))}, "
            f"средняя длительность {_fmt_num(float(row['avg_duration']))} сек, средний score {_fmt_num(float(row['avg_score']))}."
        )
    if top_ids:
        lines.append(f"Первые записи для просмотра: {', '.join(top_ids)} — они имеют максимальный score и наиболее явные CRO-сигналы: длинные/глубокие визиты без регистрации, важные источники, переходы к целевым URL или промежуточные действия без финальной цели.")
    lines.append("")
    lines.append("### Почему смотреть эти записи первыми")
    for visit_id, reason in zip(top_ids, top_reasons):
        lines.append(f"- {visit_id}: {reason}")

    lines.append("")
    lines.append("## Проблемные сегменты")
    for name, row in segments[:5]:
        label_cols = [col for col in ["deviceCategory", "UTMCampaign", "startURL"] if col in row.index]
        lines.append(
            f"- {name} {_segment_label(row, label_cols)}: визитов {_fmt_num(float(row['visits']))}, "
            f"без регистрации {_fmt_num(float(row['no_reg']))}, средняя длительность {_fmt_num(float(row['avg_duration']))} сек, средний score {_fmt_num(float(row['avg_score']))}. "
            "Гипотеза: пользователи проявляют интерес, но не доходят до регистрации или теряют мотивацию на пути к CTA."
        )

    lines.append("")
    lines.append("## Что смотреть в Вебвизоре")
    for name, row in segments[:3]:
        visits_for_segment = top.copy()
        filters: list[str] = []
        for col in ["deviceCategory", "UTMCampaign", "startURL"]:
            if col in row.index and col in visits_for_segment:
                value = str(row[col])
                if value and value != "(not set)":
                    filters.append(f"{col} = {value}")
                    visits_for_segment = visits_for_segment[visits_for_segment[col].fillna("").astype(str).eq(value)]
        ids = visits_for_segment.get("visitID", pd.Series(dtype=str)).head(3).astype(str).tolist() or top_ids[:3]
        lines.append(f"- Сегмент {name}: фильтры в Метрике — {', '.join(filters) if filters else 'top score / без регистрации'}. Открыть visitID: {', '.join(ids)}. Проверить глазами: первый экран, видимость и кликабельность CTA, скролл до формы, ошибки/валидацию формы, уход на другую важную страницу без завершения цели.")
    return "\n".join(lines)
