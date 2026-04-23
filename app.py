import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
from groq import Groq
import os
import tempfile
import struct

st.set_page_config(page_title="NL to SQL", page_icon="🔍", layout="wide")

st.title("🔍 Natural Language to SQL")
st.caption("Ask questions in plain English — get SQL + results + charts")

# Sidebar
with st.sidebar:
    st.header("Setup")
    api_key = st.text_input("Groq API Key", type="password", placeholder="gsk_.........")
    uploaded_file = st.file_uploader(
        "Upload your database file",
        type=["db", "sqlite", "sqlite3", "bak"],
        help="Supports SQLite (.db, .sqlite, .sqlite3) and backup files (.bak)"
    )
    st.markdown("---")
    st.markdown("**Sample questions:**")
    samples = [
        "Top 10 selling items in last month",
        "Total revenue by category",
        "Which city has the most customers?",
        "Monthly revenue trend",
        "Top 5 customers by total spending",
        "All pending orders with customer name",
    ]
    for s in samples:
        if st.button(s, key=s):
            st.session_state["question"] = s


# ── BAK detection helpers ────────────────────────────────────────────────────

SQLITE_MAGIC = b"SQLite format 3\x00"   # first 16 bytes of every SQLite file

def is_sqlite_file(data: bytes) -> bool:
    """Return True if the raw bytes look like a SQLite database."""
    return data[:16] == SQLITE_MAGIC


def is_mssql_bak(data: bytes) -> bool:
    """
    SQL Server .bak files start with the Microsoft Tape Format (MTF) header.
    The first 4 bytes are the DBLK type 'TAPE' (0x54415045) or the string 'MSSQLBAK'.
    A reliable check: look for the MTF magic bytes at offset 0.
    """
    MTF_MAGIC = b"\x54\x41\x50\x45"   # 'TAPE'
    return data[:4] == MTF_MAGIC


def load_connection(uploaded_file):
    """
    Accept any supported file, return (sqlite3.Connection, file_type_label).
    Handles:
      - .db / .sqlite / .sqlite3  → open directly
      - .bak that IS a SQLite file → open directly
      - .bak that IS a SQL Server backup → parse with mssql-to-sqlite conversion
    """
    raw = uploaded_file.read()
    fname = uploaded_file.name.lower()

    # ── Case 1: plain SQLite (any extension) ──────────────────────────────
    if is_sqlite_file(raw):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        conn = sqlite3.connect(tmp_path)
        return conn, "SQLite"

    # ── Case 2: SQL Server .bak ───────────────────────────────────────────
    if fname.endswith(".bak") and is_mssql_bak(raw):
        return _load_mssql_bak(raw)

    # ── Case 3: .bak that is neither SQLite nor MTF — try SQLite anyway ──
    #    (some tools just rename .db to .bak)
    if fname.endswith(".bak"):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            conn = sqlite3.connect(tmp_path)
            # Quick sanity check — will raise if not a valid SQLite db
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            return conn, "SQLite (.bak renamed)"
        except Exception:
            pass  # fall through to error below

    raise ValueError(
        f"Unsupported file format for '{uploaded_file.name}'.\n"
        "Accepted formats: SQLite (.db, .sqlite, .sqlite3) or SQL Server backup (.bak)."
    )


def _load_mssql_bak(raw: bytes):
    """
    Convert a SQL Server .bak to an in-memory SQLite database.

    Strategy
    --------
    We use the `mssql-bak-reader` / `sqlbak` pure-Python library when available.
    If it isn't installed we fall back to a helpful error message that tells the
    user exactly what to install — rather than a cryptic crash.
    """
    # ── Try sqlbak (pip install sqlbak) ───────────────────────────────────
    try:
        import sqlbak  # noqa: F401 – imported for side-effects / availability check
        from sqlbak import BakFile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bak") as tmp:
            tmp.write(raw)
            bak_path = tmp.name

        bak = BakFile(bak_path)

        # Build in-memory SQLite from every table in the backup
        mem_conn = sqlite3.connect(":memory:")

        for table_name in bak.tables:
            df = bak.read_table(table_name)
            df.to_sql(table_name, mem_conn, if_exists="replace", index=False)

        os.unlink(bak_path)
        return mem_conn, "SQL Server .bak (via sqlbak)"

    except ImportError:
        pass  # library not installed — try next method

    # ── Try mssqlreader (pip install mssqlreader) ─────────────────────────
    try:
        from mssqlreader import MSSQLReader  # noqa: F401

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bak") as tmp:
            tmp.write(raw)
            bak_path = tmp.name

        reader = MSSQLReader(bak_path)
        mem_conn = sqlite3.connect(":memory:")

        for table_name, df in reader.read_tables():
            df.to_sql(table_name, mem_conn, if_exists="replace", index=False)

        os.unlink(bak_path)
        return mem_conn, "SQL Server .bak (via mssqlreader)"

    except ImportError:
        pass

    # ── No library available — friendly instructions ──────────────────────
    raise RuntimeError(
        "A SQL Server .bak file was detected, but no parser library is installed.\n\n"
        "Install one of the following and restart the app:\n"
        "  pip install sqlbak\n"
        "  pip install mssqlreader\n\n"
        "Alternatively, restore the .bak in SQL Server Management Studio and "
        "export the tables as CSV, then re-upload."
    )


# ── Schema helper ────────────────────────────────────────────────────────────

def get_schema(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    schema = ""
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = cur.fetchall()
        col_str = ", ".join([f"{c[1]} {c[2]}" for c in cols])
        cur.execute(f"SELECT COUNT(*) FROM '{t}'")
        count = cur.fetchone()[0]
        schema += f"- {t}({col_str})  [{count} rows]\n"
    return schema, tables


# ── LLM SQL generation ───────────────────────────────────────────────────────

def generate_sql(question, schema, api_key):
    client = Groq(api_key=api_key)
    prompt = f"""You are a SQLite expert. Given this database schema:
{schema}

Important date notes:
- todays date is 2025-03-20
- last month = between '2025-02-01' AND '2025-02-28'
- this year = 2025

Write a SQLite SQL query to answer: "{question}"

Rules:
- Return ONLY the raw SQL query
- No markdown, no backticks, no explanation
- Use proper SQLite syntax
- Always use table aliases for clarity
"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.1,
    )
    return (
        response.choices[0].message.content
        .strip()
        .replace("```sql", "")
        .replace("```", "")
        .strip()
    )


# ── Auto chart ───────────────────────────────────────────────────────────────

def auto_chart(df, question):
    if df is None or df.empty or len(df.columns) < 2:
        return None

    q = question.lower()
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()

    if not num_cols:
        return None

    x_col = cat_cols[0] if cat_cols else df.columns[0]
    y_col = num_cols[0]

    if any(w in q for w in ["trend", "monthly", "daily", "over time", "by month", "by date"]):
        fig = px.line(df, x=x_col, y=y_col, markers=True,
                      title=question, template="plotly_white")
    elif any(w in q for w in ["top", "most", "best", "highest", "ranking", "selling"]):
        df_sorted = df.sort_values(y_col, ascending=True).tail(15)
        fig = px.bar(df_sorted, x=y_col, y=x_col, orientation="h",
                     title=question, template="plotly_white",
                     color=y_col, color_continuous_scale="Blues")
    elif any(w in q for w in ["category", "city", "status", "distribution", "breakdown", "by"]):
        if len(df) <= 10:
            fig = px.pie(df, names=x_col, values=y_col,
                         title=question, template="plotly_white")
        else:
            fig = px.bar(df, x=x_col, y=y_col,
                         title=question, template="plotly_white",
                         color=y_col, color_continuous_scale="Teal")
    else:
        fig = px.bar(df, x=x_col, y=y_col,
                     title=question, template="plotly_white",
                     color=y_col, color_continuous_scale="Viridis")

    fig.update_layout(margin=dict(t=50, b=30), height=420)
    return fig


# ── Main app ─────────────────────────────────────────────────────────────────

if not api_key:
    st.info("Enter your Groq API key in the sidebar to get started.")
elif not uploaded_file:
    st.info("Upload a database file (.db, .sqlite, .sqlite3, or .bak) in the sidebar.")
else:
    try:
        conn, file_type = load_connection(uploaded_file)
        st.sidebar.success(f"Loaded as: **{file_type}**")
    except (ValueError, RuntimeError) as e:
        st.error(str(e))
        st.stop()

    schema, tables = get_schema(conn)

    with st.expander("View database schema"):
        st.code(schema)

    st.markdown("---")
    question = st.text_input(
        "Ask your question in plain English",
        value=st.session_state.get("question", ""),
        placeholder="e.g. Top 10 selling items in last month",
    )

    col1, col2 = st.columns([1, 5])
    run = col1.button("Run Query", type="primary")

    if run and question:
        with st.spinner("Generating SQL with LLaMA 3.3 70B..."):
            try:
                sql = generate_sql(question, schema, api_key)

                st.markdown("#### Generated SQL")
                st.code(sql, language="sql")

                df = pd.read_sql_query(sql, conn)

                col_a, col_b = st.columns(2)
                col_a.metric("Rows returned", len(df))
                col_b.metric("Columns", len(df.columns))

                tab1, tab2 = st.tabs(["Table", "Chart"])

                with tab1:
                    st.dataframe(df, use_container_width=True)

                with tab2:
                    fig = auto_chart(df, question)
                    if fig:
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("No chart available for this result — need at least one numeric column.")
                        st.dataframe(df, use_container_width=True)

            except Exception as e:
                st.error(f"Error: {e}")

    conn.close()
