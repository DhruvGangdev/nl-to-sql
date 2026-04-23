# 🔍 Natural Language to SQL

A Streamlit app that converts plain English questions into SQL queries using **Groq's LLaMA 3.3 70B** model — and gives you results, charts, and an accuracy score.

---

## ✨ Features

- 💬 Ask questions in plain English, get SQL instantly
- 📁 Upload **SQLite** (`.db`, `.sqlite`, `.bak`) or **Excel** (`.xlsx`, `.xls`) files
- 🔒 **Schema-only mode** — generate SQL without uploading actual data
- 📊 Auto-generates bar, line, and pie charts based on results
- 🎯 **Accuracy scoring** — LLaMA rates every query on 4 dimensions (0–100)
- 📈 Session-level score history with trend chart

---

## 🗂️ File Structure

```
nl-to-sql-app/
├── app.py                    # Main Streamlit app
├── generate_sap_b1_demo.py   # Script to generate SAP B1 demo database
├── requirements.txt          # Python dependencies
├── .gitignore
└── README.md
```

---

## 🚀 Running Locally

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/nl-to-sql-app.git
cd nl-to-sql-app

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
streamlit run app.py
```

---

## ☁️ Deploying on Streamlit Cloud

1. Push this repo to GitHub (public or private)
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub
3. Click **New app** → select your repo → branch: `main` → Main file: `app.py`
4. Click **Deploy** — done!

---

## 🧪 SAP B1 Demo Database

Generate a realistic SAP Business One demo database (5,200+ rows, 20 tables) locally:

```bash
python generate_sap_b1_demo.py
# Produces: sap_b1_demo.db
```

**Tables included:**

| Module | Tables |
|--------|--------|
| Master Data | `OCRD`, `OITM`, `OSLP`, `OWHS`, `OITB` |
| Sales | `ORDR`, `RDR1`, `OINV`, `INV1` |
| Purchasing | `OPOR`, `POR1`, `OPCH`, `PCH1` |
| Payments | `ORCT`, `OVPM` |
| Inventory | `OINM`, `ITM1` |
| Finance | `OJDT`, `JDT1`, `OACT` |

**Sample questions to try:**
- `Top 5 customers by total invoice amount`
- `Monthly sales revenue trend`
- `Which items have the highest stock on hand?`
- `All open purchase orders with supplier name`
- `Total payments received by payment type`

---

## 🎯 Accuracy Scoring

Click **🎯 Score It** to evaluate any query. LLaMA scores it on:

| Dimension | Max |
|-----------|-----|
| ✅ Correctness — does it answer the question? | 25 |
| 🗂️ Schema Alignment — right tables & columns? | 25 |
| ⚙️ SQL Quality — readable, aliased, efficient? | 25 |
| 📌 Result Relevance — does the output look right? | 25 |

🟢 85–100 &nbsp; 🟡 60–84 &nbsp; 🔴 0–59

---

## ⚙️ Tech Stack

- [Streamlit](https://streamlit.io) — UI framework
- [Groq](https://groq.com) — LLaMA 3.3 70B inference
- [Plotly](https://plotly.com) — interactive charts
- [Pandas](https://pandas.pydata.org) — data handling
- [SQLite3](https://docs.python.org/3/library/sqlite3.html) — query execution
