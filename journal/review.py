"""Build bounded, source-linked local journal reviews without model calls."""

from datetime import date, timedelta

from journal import store


MAX_REVIEW_DAYS = 31
MAX_SOURCE_LINKS = 25


def _review_bounds(since, until):
    if not isinstance(since, str) or not isinstance(until, str):
        raise ValueError("Review dates must be YYYY-MM-DD strings.")
    try:
        start, end = date.fromisoformat(since), date.fromisoformat(until)
    except ValueError as error:
        raise ValueError("Review dates must be valid YYYY-MM-DD strings.") from error
    if start.isoformat() != since or end.isoformat() != until or start >= end:
        raise ValueError("Review dates must describe a nonempty date range.")
    if (end - start).days > MAX_REVIEW_DAYS:
        raise ValueError(f"Review range is limited to {MAX_REVIEW_DAYS} days.")
    return start, end


def _links(connection, where, values, *, extra=""):
    query = "SELECT DISTINCT entries.id, entries.occurrence_date FROM entries " + extra + " WHERE " + where
    count = connection.execute("SELECT COUNT(*) FROM (" + query + ")", values).fetchone()[0]
    rows = connection.execute(query + " ORDER BY entries.occurred_at_utc, entries.id LIMIT ?", values + [MAX_SOURCE_LINKS]).fetchall()
    return [dict(row) for row in rows], count > MAX_SOURCE_LINKS


def build_review(path, *, since, until, include_summary=False):
    """Return SQL-based counts and optional deterministic source-linked statements.

    until is exclusive. The optional summary is generated from the returned
    aggregates, never from free text or a model, and each statement cites its
    contributing entry IDs.
    """
    if type(include_summary) is not bool:
        raise ValueError("include_summary must be a boolean.")
    start, end = _review_bounds(since, until)
    where, values = "entries.occurrence_date >= ? AND entries.occurrence_date < ?", [since, until]
    with store._connect(path, readonly=True) as connection:
        store._check_schema(connection)
        entries = connection.execute("SELECT COUNT(*) FROM entries WHERE occurrence_date >= ? AND occurrence_date < ?", values).fetchone()[0]
        logged_dates = {row[0] for row in connection.execute(
            "SELECT DISTINCT occurrence_date FROM entries WHERE occurrence_date >= ? AND occurrence_date < ?", values
        )}
        missing_dates = [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days)
                         if (start + timedelta(days=offset)).isoformat() not in logged_dates]
        averages = connection.execute(
            "SELECT AVG(sleep_hours), COUNT(sleep_hours), AVG(energy), COUNT(energy) FROM entries "
            "WHERE occurrence_date >= ? AND occurrence_date < ?", values
        ).fetchone()
        moods = [dict(row) for row in connection.execute(
            "SELECT mood AS value, COUNT(*) AS count FROM entries WHERE occurrence_date >= ? AND occurrence_date < ? "
            "AND mood IS NOT NULL GROUP BY mood ORDER BY count DESC, mood", values
        )]
        flows = [dict(row) for row in connection.execute(
            "SELECT bleeding AS value, COUNT(*) AS count FROM entries WHERE occurrence_date >= ? AND occurrence_date < ? "
            "AND bleeding IS NOT NULL GROUP BY bleeding ORDER BY count DESC, bleeding", values
        )]
        symptoms = [dict(row) for row in connection.execute(
            "SELECT observations.symptom AS value, COUNT(*) AS count FROM observations JOIN entries "
            "ON entries.id = observations.entry_id WHERE " + where + " GROUP BY observations.symptom "
            "ORDER BY count DESC, observations.symptom", values
        )]
        source_entries, source_truncated = _links(connection, where, values)
        statements = []
        if include_summary and entries:
            statements.append({"text": f"You logged {entries} entr{'y' if entries == 1 else 'ies'} across {len(logged_dates)} day{'s' if len(logged_dates) != 1 else ''}.",
                               "source_ids": [item["id"] for item in source_entries]})
            if averages[1]:
                links, _ = _links(connection, where + " AND entries.sleep_hours IS NOT NULL", values)
                statements.append({"text": f"Average recorded sleep was {averages[0]:.1f} hours across {averages[1]} entr{'y' if averages[1] == 1 else 'ies'}.",
                                   "source_ids": [item["id"] for item in links]})
            if averages[3]:
                links, _ = _links(connection, where + " AND entries.energy IS NOT NULL", values)
                statements.append({"text": f"Average recorded energy was {averages[2]:.1f} out of 10 across {averages[3]} entr{'y' if averages[3] == 1 else 'ies'}.",
                                   "source_ids": [item["id"] for item in links]})
            if symptoms:
                links, _ = _links(connection, where, values, extra="JOIN observations ON observations.entry_id = entries.id")
                statements.append({"text": "Most frequently recorded symptoms: " + ", ".join(
                    f"{item['value']} ({item['count']})" for item in symptoms[:3]) + ".",
                                   "source_ids": [item["id"] for item in links]})
    return {
        "since": since,
        "until": until,
        "entries": entries,
        "logged_days": len(logged_dates),
        "missing_dates": missing_dates,
        "average_sleep_hours": None if averages[0] is None else round(averages[0], 1),
        "sleep_entry_count": averages[1],
        "average_energy": None if averages[2] is None else round(averages[2], 1),
        "energy_entry_count": averages[3],
        "moods": moods,
        "flows": flows,
        "symptoms": symptoms,
        "summary": statements,
        "source_entries": source_entries,
        "source_links_truncated": source_truncated,
    }
