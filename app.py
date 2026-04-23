import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
from groq import Groq
import os
import tempfile

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
        help="Supports SQLite (.db, .sqlite, .sqlite3) and SQL Server / SAP B1 backup files (.bak)",
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


# ── File loading ─────────────────────────────────────────────────────────────

SQLITE_MAGIC = b"SQLite format 3\x00"


def _write_temp(data: bytes, suffix: str) -> str:
    """Write bytes to a temp file and return the path."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        return tmp.name


def _try_sqlite(data: bytes):
    """Try opening data as a SQLite database. Returns connection or raises."""
    path = _write_temp(data, ".db")
    conn = sqlite3.connect(path)
    # Verify it's a real SQLite db — this will raise if not
    conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return conn


def _try_sqlbak(data: bytes):
    """Try parsing as SQL Server backup using the `sqlbak` library."""
    from sqlbak import BakFile  # pip install sqlbak

    path = _write_temp(data, ".bak")
    bak = BakFile(path)
    mem = sqlite3.connect(":memory:")
    for table_name in bak.tables:
        df = bak.read_table(table_name)
        df.to_sql(table_name, mem, if_exists="replace", index=False)
    os.unlink(path)
    return mem


def _try_mssqlreader(data: bytes):
    """Try parsing as SQL Server backup using the `mssqlreader` library."""
    from mssqlreader import MSSQLReader  # pip install mssqlreader

    path = _write_temp(data, ".bak")
    reader = MSSQLReader(path)
    mem = sqlite3.connect(":memory:")
    for table_name, df in reader.read_tables():
        df.to_sql(table_name, mem, if_exists="replace", index=False)
    os.unlink(path)
    return mem


def _try_pyodbc(data: bytes):
    """
    Restore SQL Server .bak via pyodbc + local SQL Server / LocalDB.
    Requires: pip install pyodbc  AND  SQL Server or LocalDB installed.
    """
    import pyodbc  # pip install pyodbc

    bak_path = _write_temp(data, ".bak")
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise RuntimeError("No SQL Server ODBC driver found on this machine.")

    conn_str = f"DRIVER={{{drivers[0]}}};SERVER=(localdb)\\MSSQLLocalDB;Trusted_Connection=yes;"
    sql_conn = pyodbc.connect(conn_str, autocommit=True)
    cursor = sql_conn.cursor()

    db_name = f"tmpbak_{os.getpid()}"
    mdf_path = os.path.join(tempfile.gettempdir(), f"{db_name}.mdf")

    # Get logical names from the backup header
    cursor.execute(f"RESTORE FILELISTONLY FROM DISK = N'{bak_path}'")
    file_rows = cursor.fetchall()
    move_clauses = ""
    for i, row in enumerate(file_rows):
        logical = row[0]
        ext = ".mdf" if i == 0 else f"_{i}.ldf"
        phys = os.path.join(tempfile.gettempdir(), f"{db_name}{ext}")
        move_clauses += f"MOVE N'{logical}' TO N'{phys}', "

    cursor.execute(
        f"RESTORE DATABASE [{db_name}] FROM DISK = N'{bak_path}' "
        f"WITH {move_clauses} REPLACE, RECOVERY"
    )

    sql_conn2 = pyodbc.connect(conn_str + f"DATABASE={db_name};")
    mem = sqlite3.connect(":memory:")
    tables = [
        r[0]
        for r in sql_conn2.cursor()
        .execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE'"
        )
        .fetchall()
    ]
    for t in tables:
        df = pd.read_sql(f"SELECT * FROM [{t}]", sql_conn2)
        df.to_sql(t, mem, if_exists="replace", index=False)

    sql_conn2.close()
    cursor.execute(f"DROP DATABASE [{db_name}]")
    sql_conn.close()
    os.unlink(bak_path)
    return mem


def load_connection(uploaded_file):
    """
    Auto-detect file type and return (sqlite3.Connection, label).

    Detection order
    ---------------
    1. SQLite magic bytes  → open directly
    2. .bak → try sqlbak        (pure-Python, no SQL Server needed)
    3. .bak → try mssqlreader   (pure-Python, no SQL Server needed)
    4. .bak → try pyodbc        (needs local SQL Server / LocalDB)
    5. .bak → SQLite fallback   (in case it's just a renamed .db)
    6. All failed → friendly error with install instructions
    """
    raw = uploaded_file.read()
    fname = uploaded_file.name.lower()
    errors = []

    # ── 1. SQLite magic bytes check (100% reliable) ───────────────────────
    if raw[:16] == SQLITE_MAGIC:
        try:
            return _try_sqlite(raw), "SQLite"
        except Exception as e:
            errors.append(f"SQLite open failed: {e}")

    # ── 2–5. .bak strategies — NO magic-byte gate (handles all versions) ──
    if fname.endswith(".bak"):

        # Strategy 2: sqlbak (pure-Python)
        try:
            return _try_sqlbak(raw), "SQL Server .bak → sqlbak"
        except ImportError:
            errors.append("sqlbak not installed  →  pip install sqlbak")
        except Exception as e:
            errors.append(f"sqlbak: {e}")

        # Strategy 3: mssqlreader (pure-Python)
        try:
            return _try_mssqlreader(raw), "SQL Server .bak → mssqlreader"
        except ImportError:
            errors.append("mssqlreader not installed  →  pip install mssqlreader")
        except Exception as e:
            errors.append(f"mssqlreader: {e}")

        # Strategy 4: pyodbc + local SQL Server
        try:
            return _try_pyodbc(raw), "SQL Server .bak → pyodbc"
        except ImportError:
            errors.append("pyodbc not installed  →  pip install pyodbc")
        except Exception as e:
            errors.append(f"pyodbc: {e}")

        # Strategy 5: last-ditch SQLite fallback
        try:
            return _try_sqlite(raw), "SQLite (.bak renamed)"
        except Exception as e:
            errors.append(f"SQLite fallback: {e}")

        # All strategies exhausted
        detail = "\n".join(f"  • {e}" for e in errors)
        raise RuntimeError(
            f"❌ Could not open **{uploaded_file.name}**.\n\n"
            "**Attempts made:**\n"
            f"{detail}\n\n"
            "**Quick fix — install a pure-Python parser (no SQL Server needed):**\n"
            "```bash\npip install sqlbak\n```\n"
            "or\n"
            "```bash\npip install mssqlreader\n```\n"
            "Then restart the app and re-upload the file."
        )

    # Non-.bak, non-SQLite
    raise ValueError(
        f"Unsupported file: **{uploaded_file.name}**\n"
        "Accepted formats: `.db`, `.sqlite`, `.sqlite3`, `.bak`"
    )


# ── Schema helper ────────────────────────────────────────────────────────────

def get_schema(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    schema = ""
    for t in tables:
        cur.execute(f"PRAGMA table_info('{t}')")
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
        st.sidebar.success(f"✅ Loaded as: **{file_type}**")
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
