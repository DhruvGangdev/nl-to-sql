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
    api_key = st.text_input("Groq API Key", type="password", placeholder="gsk_...")
    uploaded_file = st.file_uploader("Upload your .db file", type=["db", "sqlite", "sqlite3"])
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

def get_schema(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    schema = ""
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = cur.fetchall()
        col_str = ", ".join([f"{c[1]} {c[2]}" for c in cols])
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        count = cur.fetchone()[0]
        schema += f"- {t}({col_str})  [{count} rows]\n"
    return schema, tables

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
        temperature=0.1
    )
    return response.choices[0].message.content.strip().replace("```sql","").replace("```","").strip()

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

# Main app
if not api_key:
    st.info("Enter your Groq API key in the sidebar to get started.")
elif not uploaded_file:
    st.info("Upload your shop.db file in the sidebar.")
else:
    # Load DB
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    conn = sqlite3.connect(tmp_path)
    schema, tables = get_schema(conn)

    with st.expander("View database schema"):
        st.code(schema)

    st.markdown("---")
    question = st.text_input(
        "Ask your question in plain English",
        value=st.session_state.get("question", ""),
        placeholder="e.g. Top 10 selling items in last month"
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
