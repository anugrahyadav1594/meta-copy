"""Streamlit dashboard for comparing indexed vs unindexed MySQL query plans."""

from __future__ import annotations

import json

import streamlit as st
from mysql.connector import Error

from database import (
    analyze_query,
    apply_index_mode,
    build_target_queries,
    fetch_sample_ids,
    get_connection,
    healthcheck,
    indexes_enabled,
)

st.set_page_config(
    page_title="Query Performance Analyzer",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

UNOPT_RED = "#ef4444"
OPT_GREEN = "#22c55e"
UNOPT_BG = "rgba(239, 68, 68, 0.14)"
OPT_BG = "rgba(34, 197, 94, 0.14)"

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
  font-family: "IBM Plex Sans", sans-serif;
}

.stApp {
  background:
    radial-gradient(1200px 500px at 10% -10%, rgba(56, 189, 248, 0.12), transparent 50%),
    radial-gradient(900px 400px at 100% 0%, rgba(167, 139, 250, 0.10), transparent 45%),
    #0b1220;
  color: #e5eefc;
}

.block-container { padding-top: 1.2rem; max-width: 1280px; }

h1, h2, h3 { letter-spacing: -0.02em; }

.hero {
  background: linear-gradient(135deg, #111827 0%, #1e293b 55%, #0f172a 100%);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 18px;
  padding: 22px 26px;
  margin-bottom: 18px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
}
.hero h1 {
  margin: 0 0 6px 0;
  font-size: 1.85rem;
  color: #f8fafc;
}
.hero p { margin: 0; color: #94a3b8; font-size: 0.98rem; }

.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-top: 12px;
}
.badge-on { background: rgba(34, 197, 94, 0.18); color: #86efac; border: 1px solid rgba(34,197,94,0.35); }
.badge-off { background: rgba(239, 68, 68, 0.16); color: #fca5a5; border: 1px solid rgba(239,68,68,0.35); }

.funnel-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  margin: 8px 0 22px 0;
}
.funnel-step {
  color: #0b1220;
  border-radius: 14px;
  padding: 14px 18px;
  text-align: center;
  box-shadow: 0 10px 30px rgba(0,0,0,0.25);
  border: 1px solid rgba(255,255,255,0.08);
}
.funnel-step .k {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  opacity: 0.72;
}
.funnel-step .v {
  font-family: "IBM Plex Mono", monospace;
  font-size: 1.28rem;
  font-weight: 700;
  margin-top: 2px;
}
.arrow { color: #64748b; font-size: 1.1rem; }

.metric-card {
  border-radius: 14px;
  padding: 16px 18px;
  border: 1px solid rgba(148,163,184,0.2);
}
.metric-card .label { color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; }
.metric-card .value { font-family: "IBM Plex Mono", monospace; font-size: 1.45rem; font-weight: 700; margin-top: 4px; }

.sql-box {
  background: #020617;
  border: 1px solid rgba(148,163,184,0.2);
  border-radius: 12px;
  padding: 14px 16px;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.82rem;
  color: #cbd5e1;
  white-space: pre-wrap;
}

.status-ok { color: #86efac; }
.status-bad { color: #fca5a5; }
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def _fmt_cost(value) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"{value:,.1f}"
    return f"{value:.4g}"


def _fmt_ms(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.0f} µs"
    if value < 1000:
        return f"{value:.2f} ms"
    return f"{value / 1000:.2f} s"


def render_funnel(query_name: str, access_type: str, rows_examined: int, elapsed_ms: float, optimized: bool) -> None:
    color = OPT_GREEN if optimized else UNOPT_RED
    widths = (100, 86, 70, 54)
    stages = (
        ("Query", query_name),
        ("Scan type", access_type),
        ("Rows scanned", f"{rows_examined:,}"),
        ("Execution time", _fmt_ms(elapsed_ms)),
    )
    parts = ['<div class="funnel-wrap">']
    for i, ((label, value), width) in enumerate(zip(stages, widths)):
        parts.append(
            f'<div class="funnel-step" style="width:{width}%;background:{color};">'
            f'<div class="k">{label}</div><div class="v">{value}</div></div>'
        )
        if i < len(stages) - 1:
            parts.append('<div class="arrow">▼</div>')
    parts.append("</div>")
    st.markdown("\n".join(parts), unsafe_allow_html=True)


def render_metric_row(result: dict, optimized: bool) -> None:
    accent = OPT_GREEN if optimized else UNOPT_RED
    bg = OPT_BG if optimized else UNOPT_BG
    items = (
        ("Access type", result["access_type"]),
        ("Rows examined", f"{result['rows_examined']:,}"),
        ("Query cost", _fmt_cost(result["query_cost"])),
        ("Wall time", _fmt_ms(result["elapsed_ms"])),
        ("Rows returned", f"{result['row_count']:,}"),
    )
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        with col:
            st.markdown(
                f'<div class="metric-card" style="background:{bg};border-color:{accent}33;">'
                f'<div class="label">{label}</div>'
                f'<div class="value" style="color:{accent};">{value}</div></div>',
                unsafe_allow_html=True,
            )


@st.cache_data(ttl=15, show_spinner=False)
def load_health() -> dict:
    return healthcheck()


def main() -> None:
    try:
        health = load_health()
    except Error as exc:
        st.error(
            "Cannot reach MySQL. Confirm the server is running and that "
            "`MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, and `MYSQL_DATABASE` "
            f"are set correctly.\n\n`{exc}`"
        )
        st.stop()

    optimized_now = bool(health.get("indexes_enabled"))
    badge_cls = "badge-on" if optimized_now else "badge-off"
    badge_txt = "Indexes currently ON" if optimized_now else "Indexes currently OFF"

    st.markdown(
        f"""
        <div class="hero">
          <h1>Query Performance Analyzer</h1>
          <p>Compare MySQL access paths for a social-graph workload using
          <code>EXPLAIN FORMAT=JSON</code>. Toggle secondary indexes and watch
          scan type, estimated rows, optimizer cost, and wall-clock time move together.</p>
          <span class="badge {badge_cls}">{badge_txt} · {health.get('database')} · {health.get('version')}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.subheader("Controls")
        st.caption("Pick a workload query, then choose whether covering indexes stay enabled.")

        query_names = [
            "Feed generation",
            "Post + Author",
            "Post + Likes",
            "Post + Comments",
            "Follower lookup",
            "Notification retrieval",
        ]
        selected = st.selectbox("Target query", query_names, index=0)
        enable_indexes = st.toggle(
            "Enable Indexes",
            value=optimized_now,
            help="Creates or drops secondary indexes (and briefly drops FKs so InnoDB allows the drop).",
        )

        st.markdown("---")
        st.caption(
            "Unoptimized runs are shown in **red** (typical `ALL` / high row estimates). "
            "Indexed runs are **green** (`ref` / `eq_ref`, lower cost)."
        )
        run = st.button("Run analysis", type="primary", use_container_width=True)

    if not run:
        st.info("Choose a query and click **Run analysis**. First run after toggling indexes may take a few seconds while DDL applies.")
        with st.expander("What each query measures"):
            st.markdown(
                """
                | Query | Why it stresses the optimizer |
                | --- | --- |
                | Feed generation | Join `follows → posts → users` plus `ORDER BY created_at` |
                | Post + Author | Point lookup with profile join |
                | Post + Likes | Aggregate over `likes.post_id` |
                | Post + Comments | Filter + sort a large child table |
                | Follower lookup | Reverse-direction follow graph (`followee_id`) |
                | Notification retrieval | Per-user time-ordered slice |
                """
            )
        return

    optimized = bool(enable_indexes)
    status = st.status("Preparing MySQL session…", expanded=True)
    try:
        with get_connection() as conn:
            status.update(label="Applying index mode…")
            apply_index_mode(conn, optimized)
            load_health.clear()

            status.update(label="Selecting representative keys…")
            ids = fetch_sample_ids(conn)
            catalog = build_target_queries(ids)
            spec = catalog[selected]

            status.update(label="Running EXPLAIN FORMAT=JSON and timing SELECT…")
            result = analyze_query(conn, spec["sql"], spec["params"])
            result["indexes_enabled"] = indexes_enabled(conn)
        status.update(label="Analysis complete", state="complete")
    except Error as exc:
        status.update(label="MySQL error", state="error")
        st.error(str(exc))
        return
    except RuntimeError as exc:
        status.update(label="Setup error", state="error")
        st.error(str(exc))
        return

    state_label = "Optimized (indexes on)" if optimized else "Unoptimized (indexes off)"
    st.markdown(f"### {selected}")
    st.caption(spec["description"] + f"  ·  {state_label}")

    render_funnel(
        selected,
        result["access_type"],
        result["rows_examined"],
        result["elapsed_ms"],
        optimized=optimized,
    )
    render_metric_row(result, optimized=optimized)

    st.markdown("#### Plan by table")
    table_rows = []
    for item in result["tables"]:
        key = item.get("key") or "—"
        table_rows.append(
            {
                "table": item.get("table_name"),
                "access_type": item.get("access_type"),
                "rows_examined_per_scan": item.get("rows_examined_per_scan"),
                "filtered_%": item.get("filtered"),
                "key": key,
                "using_index": bool(item.get("using_index")),
            }
        )
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        st.markdown("#### SQL")
        st.markdown(f'<div class="sql-box">{result["sql"]}</div>', unsafe_allow_html=True)
        st.caption(f"Bind parameters: `{result['params']}`")
    with right:
        st.markdown("#### Result preview")
        if result["preview"]:
            st.dataframe(result["preview"], use_container_width=True, hide_index=True)
        else:
            st.warning("Query returned no rows for the sampled keys.")

    with st.expander("Raw EXPLAIN JSON"):
        st.code(json.dumps(result["raw_plan"], indent=2), language="json")


if __name__ == "__main__":
    main()
