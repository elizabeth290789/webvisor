"""Human-readable CRO summaries for problematic Webvisor segments."""
from __future__ import annotations

import os

import pandas as pd


def build_summary(problem_segments: pd.DataFrame, full_sample: pd.DataFrame | None = None) -> str:
    sample = full_sample if full_sample is not None else pd.DataFrame()
    if sample.empty:
        return "Нет визитов для анализа."
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai_summary(problem_segments, sample)
        except Exception as exc:  # show fallback instead of breaking the app
            return f"OpenAI API недоступен ({exc}).\n\n" + _rule_summary(problem_segments, sample)
    return _rule_summary(problem_segments, sample)


def _openai_summary(problem_segments: pd.DataFrame, sample: pd.DataFrame) -> str:
    from openai import OpenAI

    cols = [c for c in ["segment_type", "segment_name", "visits", "registrations", "CR", "baseline_CR", "CR_delta", "avg_visitDuration", "avg_pageViews", "share_of_traffic", "priority_score"] if c in problem_segments]
    csv_sample = problem_segments[cols].head(10).to_csv(index=False)
    total = len(sample)
    regs = int(sample.get("registered", pd.Series(False, index=sample.index)).astype(bool).sum()) if "registered" in sample else 0
    prompt = (
        "Сформируй CRO-вывод по проблемным сегментам Яндекс Метрики. Нельзя делать главным инсайтом "
        "простые правила вроде длинного визита без регистрации или 2+ просмотра без регистрации. Нужны: общий CR выборки, "
        "3-5 сегментов с самой большой просадкой, почему они подозрительные, что проверить в Вебвизоре.\n\n"
        f"Всего визитов: {total}; регистраций: {regs}; CR: {(regs / total * 100) if total else 0:.2f}%\n\n"
        + csv_sample
    )
    client = OpenAI()
    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    return response.output_text


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _rule_summary(problem_segments: pd.DataFrame, sample: pd.DataFrame) -> str:
    registered = sample.get("registered", pd.Series(False, index=sample.index)).astype(bool) if "registered" in sample else pd.Series(False, index=sample.index)
    total = len(sample)
    regs = int(registered.sum())
    cr = regs / total * 100 if total else 0
    avg_duration = pd.to_numeric(sample.get("visitDuration", pd.Series(0, index=sample.index)), errors="coerce").fillna(0).mean() if total else 0
    avg_pageviews = pd.to_numeric(sample.get("pageViews", pd.Series(0, index=sample.index)), errors="coerce").fillna(0).mean() if total else 0

    lines = [
        "## Короткий вывод",
        f"Общий CR выбранной выборки — {cr:.2f}%: {regs} регистраций из {total} визитов. Средняя длительность — {_fmt_num(float(avg_duration))} сек, средняя глубина — {_fmt_num(float(avg_pageviews))} просмотра.",
    ]
    if problem_segments.empty:
        lines.append("Проблемные сегменты с CR ниже базового уровня и достаточным объемом не найдены. Расширьте период, уменьшите минимальный объем сегмента или проверьте корректность ID целей регистрации.")
        return "\n".join(lines)

    top = problem_segments.head(5)
    first = top.iloc[0]
    interest = []
    if float(first.get("avg_visitDuration", 0)) > avg_duration:
        interest.append("средняя длительность выше средней")
    if float(first.get("avg_pageViews", 0)) > avg_pageviews:
        interest.append("глубина выше средней")
    interest_text = ", ".join(interest) if interest else "есть заметная просадка CR при достаточном объеме"
    lines.append(
        f"Основная зона внимания — {first['segment_name']}: {int(first['visits'])} визитов, CR {float(first['CR']):.2f}% против {float(first['baseline_CR']):.2f}% в среднем. "
        f"При этом {interest_text}. Вероятная проблема может быть не только в качестве трафика, а в прохождении или понимании страницы."
    )
    lines.append("")
    lines.append("## Сегменты с самой большой просадкой")
    for _, row in top.iterrows():
        reasons = []
        if float(row.get("avg_visitDuration", 0)) > avg_duration:
            reasons.append("длительность выше средней")
        if float(row.get("avg_pageViews", 0)) > avg_pageviews:
            reasons.append("глубина выше средней")
        if not reasons:
            reasons.append("сегмент достаточно большой и CR ниже базы")
        lines.append(
            f"- {row['segment_name']} ({row['segment_type']}): {int(row['visits'])} визитов, CR {float(row['CR']):.2f}% против {float(row['baseline_CR']):.2f}%, "
            f"дельта {float(row['CR_delta']):.2f} п.п., доля трафика {float(row['share_of_traffic']):.2f}%, priority {float(row['priority_score']):.1f}. "
            f"Почему подозрительно: {', '.join(reasons)}. Смотреть в Вебвизоре: первый экран, соответствие объявления/UTM и URL входа, видимость CTA, форму регистрации, момент ухода и URL выхода."
        )
    lines.append("")
    lines.append("## Что именно проверить в Вебвизоре")
    lines.append("- Сравнить записи проблемных сегментов с 2–3 конверсионными визитами: отличаются ли первый экран, путь к CTA и момент принятия решения.")
    lines.append("- Проверить, возникает ли непонимание оффера, техническая ошибка, неработающий CTA, неудобство на конкретном устройстве или несоответствие UTM-обещания странице входа.")
    lines.append("- Не трактовать длинную длительность или 2+ просмотра как самостоятельный инсайт: это только признак интереса внутри сегмента с CR ниже базы.")
    return "\n".join(lines)
