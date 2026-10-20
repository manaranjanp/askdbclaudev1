# Natural-Language Database Query App: Specification

Status: Draft v1, derived from `one-pager.md` and the interview decisions recorded below.

## 1. Summary

A local web app where business analysts ask questions of a SQLite database in plain English and get back a plain-English explanation and the underlying data. Users never see SQL unless they expand it. The app is strictly read-only.

## 2. Scope

### 2.1 In scope (v1)

- Conversational Q&A over one selected database at a time
- Four question types: totals and trends, rankings and top-N, filtering to specific records, comparisons across segments
- Clarifying questions when a question is ambiguous
- Per-user private conversation history, kept indefinitely
- Follow-up questions within a conversation
- CSV export of result tables
- Startup connection check for each configured database
- Audit log of executed queries
- Golden-question regression suite

### 2.2 Out of scope (v1)

- Multi-database questions
- Scheduled reports and alerts
- Writing data back to any database
- Forecasting and predictions
- Access control by role, team or region (every logged-in user sees every configured database)
- Searching user history
- Excel export
- Personal data (PII) enforcement (see section 9)
- Non-SQLite databases (see section 4.2)

## 3. Users and deployment

| Item | Decision |
|---|---|
| Primary users | Business analysts who query data regularly and don't write SQL |
| Secondary users | Support staff who maintain YAML configuration files |
| Max concurrent users | 5 |
| Hosting | Single local machine (developer laptop). Not a shared server in v1. |
| Authentication | Username and password, managed inside the app. Passwords stored hashed (argon2 or bcrypt). |

Notes:
- Only 5 users are expected, so SQLite for app storage is acceptable.
- Because the app runs on a laptop, "always-on availability" is not a v1 requirement. Moving to a shared server is a later phase.

## 4. Data sources

### 4.1 Configuration

- Support maintains one YAML file per database, in a config directory.
- Each file covers:
  - Connection (SQLite file path, with the read-only flag in section 6.1)
  - Schema: tables and columns, each with a description and optional synonyms, plus join hints (section 5.4)
  - Business context: metric definitions and notes, each with a description and optional synonyms (section 5.5)
  - Few-shot SQL examples: a separate file per database, `examples/<database>.yaml` (section 5.8)
  - Per-database query timeout override is **not** in v1; a single global timeout applies (section 7.3)
- Secrets (where any exist) are referenced as environment variables, e.g. `${ANALYTICS_DB_PATH}`. No secret values appear in YAML.
- Every configurable item (table, column, metric, note, join hint, example) has a text description that is used for semantic retrieval (section 5.9). Support writes these descriptions; retrieval quality depends on them.
- Config files load at app startup. Changes take effect on restart. No hot reload.
- A schema validator runs at startup and reports malformed YAML or missing required keys with file name and line.

### 4.2 Database engine

- **v1 supports SQLite only.** Other SQLAlchemy dialects are a later phase. The stack should still use SQLAlchemy as the access layer so that adding dialects later is a configuration and testing task, not a rewrite.

### 4.3 Startup connection check

- At startup, the app opens each configured database.
- A database that fails is shown as **unavailable** in the database picker, with the error reason. It is logged.
- Other databases keep working. The app does not refuse to start.

## 5. Question answering pipeline

1. **Database selection.** The user selects one database before asking questions. Switching databases ends the current conversation and starts a new one.
2. **Intent check.** A lightweight classifier decides one thing: is the user's message a question about the data, or something else (e.g. a prompt-injection attempt, small talk, a request to change data)?
   - About the data: pass the question to SQL generation (step 3).
   - Anything else: decline without generating SQL. See section 5.6.
   - The classifier does not judge whether the question fits the four supported types or whether it is ambiguous. Those are handled by SQL generation (see 5.2 and 5.6).
   - The classifier sees the question text only. It does not see schema, result rows or conversation history. Its prompt treats the question as data to classify, never as instructions.
3. **SQL generation.** The LLM receives the context retrieved for this question (section 5.9), plus:
   - The conversation context (section 5.3)
   - The user's question
   The retrieved context covers the relevant tables and columns, join hints among them, matching metrics and notes, and the most similar few-shot examples (section 5.8). The full configuration is not sent (see the fallback in 5.9).
   It returns a single SQL SELECT statement.
4. **Validation.** The statement is checked (section 6.2) before execution.
5. **Execution.** The statement runs under the global timeout (section 7.3) and the row cap (section 7.1).
6. **Repair.** If execution fails, the database error and the SQL are sent back to the LLM once for repair. If the repaired SQL also fails, the user sees a plain-English failure with the SQL shown.
7. **Explanation.** The LLM writes the plain-English explanation from the actual result rows, not from its own guess at the answer (section 5.7).
8. **Render and log.** The answer is shown (section 8) and the query is written to the audit log (section 7.4).

### 5.1 Date and time resolution

- Relative phrases ("last quarter", "recently", "this year") are resolved against today's date using the calendar in config.
- If the phrase is relative or non-standard, the app asks for confirmation (see 5.2) before running SQL.
- Every answer states the exact resolved date range, e.g. "Q2 2026 (Apr 1 to Jun 30, 2026)".
- The fiscal calendar, if different from the calendar year, is declared in config.

### 5.2 Ambiguity and clarification

- When the question could mean more than one thing (e.g. "revenue" with several defined meanings, or an unconfirmed date range), SQL generation returns a clarifying question with the options defined in config, instead of SQL.
- No SQL runs until the user picks an option.
- A clarification exchange counts as a conversation turn, and both the question and the answer are saved to history.

### 5.3 Follow-up context

- Context carried into a follow-up = previous user questions + the SQL that ran for each + each answer's plain-English explanation.
- Result rows are **not** carried forward. A follow-up that needs them re-runs the SQL.
- Context resets when the database changes (5, step 1).

### 5.4 Joins and relationships

- The LLM infers joins from the schema.
- Support may add optional **join hints** in config: for each relationship, the two tables and columns, and the cardinality (one-to-one, one-to-many, many-to-many). Join hints are retrieved with the tables they connect (section 5.9).
- When a query joins across a many-to-many path, or joins with no hint declared, the app shows a warning banner (section 8.3) that totals may be inflated by duplication.
- **Known risk:** LLM-inferred joins can silently double-count. Mitigation is hints plus the warning banner, not a guarantee. See section 11, risk R1.

### 5.5 Business context

- Metric definitions are structured in config: name, description, SQL expression, default filters, the tables the expression needs, and synonyms the user might type.
- Notes are free-text background (e.g. "revenue is net of returns"), each with a short description so it can be retrieved.
- Metrics and notes are retrieved by semantic match to the question. An exact match on a metric name or synonym always includes that metric (section 5.9).
- The generator is told to prefer defined metrics. Where a defined metric matches, the answer states which definition was used.

### 5.6 Non-data requests

- A message classified as not about the data gets a short decline, e.g. "I can only answer questions about the data in [database name]. For example: ...", with two or three example questions. No SQL is generated or run.
- The decline does not echo back or act on any instructions in the message.
- Declined messages are written to the audit log with outcome "declined: non-data" (section 7.4).
- Out-of-scope analytical questions (e.g. "why did sales drop?", "forecast next month") are not declined by the classifier. They pass to SQL generation like any data question. See section 11, risk R3.

### 5.7 Explanation grounding

- The explanation is generated from the result rows and the executed SQL.
- The SQL is always available in the "How was this calculated" section.
- Known limit: the LLM may still misstate numbers. Mitigation is the golden-question suite (section 10) and visible SQL.

### 5.8 Few-shot SQL examples

Support adds known-good question-to-SQL pairs for each database. At generation time, the most relevant pairs are included in the SQL generation prompt, so the model imitates proven patterns for that schema and its metric definitions.

**Authoring file**

- One file per database: `examples/<database>.yaml`, in the same config directory as the database's config (section 4.1).
- Each entry has:
  - `question`: a natural-language question, in the wording analysts actually use
  - `sql`: the SELECT that correctly answers it
  - `category`: one of `totals_trend`, `ranking`, `filter`, `comparison` (the four supported types, section 2.1)
  - `notes` (optional): a one-line explanation of why the SQL is shaped this way, e.g. "net of returns, see metric `net_revenue`"
- Examples are authored by support. Analysts cannot add or edit examples in the app in v1.

**Retrieval by query semantics**

- Examples are retrieved with the rest of the context (section 5.9): the **top 5** examples (default; configurable app-wide) whose `question` is most semantically similar to the retrieval query.
- Similarity is computed with a local embedding model, so example questions and user questions are never sent to a separate service for matching. Example embeddings are computed at startup.
- Optionally, examples in the same `category` as the user's question get a small score boost. The category of the user's question is inferred from the question only, not by the intent classifier (section 5, step 2).
- If the database has no examples, generation proceeds with schema and business context only. This is not an error and does not show a banner.

**Validation at startup**

- Every example's `sql` is checked against the same validator used for generated SQL (section 6.2), and executed against its database under the global timeout (section 7.3) with the read-only connection (section 6.1).
- An example that fails validation or execution is **skipped** and logged with file name, entry index and reason. It is not used in prompts.
- Examples that refer to tables or columns no longer in the schema are caught by this check. Support sees the log at restart.
- The app does not refuse to start because of a bad example.

**Rules**

- Examples are prompt context, not data. They must not contain result rows, personal data, or literal values copied from production data. Support is responsible for this (see section 9).
- Examples never override the read-only rules. The validator and read-only connection apply to any SQL the model produces, whether or not it resembles an example.
- Repair (section 5, step 6) reuses the same examples in its prompt as the first generation attempt.
- Examples are not automatically golden-suite tests. Support may copy an example into the golden suite (section 10, Q3) when it should be regression-tested.
- Limit: 200 examples per database in v1, to keep the prompt bounded.

### 5.9 Semantic context retrieval

The configuration for each database is large relative to what one question needs, so the app sends only the relevant part to SQL generation.

**What is embedded.** At startup, every configurable item is embedded from its name, description and synonyms: tables, columns, metrics, notes, join hints and few-shot examples (the example's `question`). Embeddings are computed with the local model (Q10).

**When it runs.** After the intent check (section 5, step 2) and before SQL generation (step 3). Input is the retrieval query:
- For a first question, the question itself.
- For a follow-up, the current question plus the previous user question, so short follow-ups such as "and by region?" still find the right tables.

**Selection steps.**
1. **Tables.** Take the top 4 tables (default) by similarity. Always include tables marked `always_include` in config (e.g. a core fact table that most questions join through).
2. **Join neighbours.** Add tables connected to a selected table by a join hint, one hop only.
3. **Columns.** For each selected table, include its key columns (primary keys and columns named in join hints), plus the top 10 columns by similarity (default). Wide tables stay bounded.
4. **Metrics.** Include metrics above the similarity threshold, and every metric whose name or synonym appears in the question (exact, case-insensitive). Including a metric also includes the tables its expression needs.
5. **Notes.** Include notes above the threshold.
6. **Examples.** Include the top 5 few-shot examples (section 5.8).
7. **Join hints.** Include hints between any two selected tables.

Sizes and the similarity threshold are configurable app-wide, with starting defaults to tune against the golden suite (Q11). An item below the threshold is dropped, but at least one table is always sent.

**Fallback on a miss.**
- If no table reaches the threshold, the top 4 tables are sent anyway and the audit log entry is marked **low retrieval confidence**.
- If the generated SQL references a table that is not in the retrieved context, the validator (section 6.2) flags it. Repair (section 5, step 6) then runs once with the **full schema**, not the retrieved subset. This is the only case where the full configuration is sent.

**Startup validation of config.** Each configured table and column is checked against the database catalog. Items that do not exist are excluded from retrieval and logged. Metric expressions are test-compiled against the read-only connection; failures are excluded and logged. Neither case stops the app from starting.

**Transparency.** The "How was this calculated" section lists the tables, metrics and examples used, and the retrieval confidence. The audit log stores the retrieved item IDs (section 7.4).

## 6. Read-only enforcement

Two independent layers. Either alone is insufficient.

### 6.1 Database-level

- SQLite connections open with the read-only URI flag (`mode=ro`). This is the SQLite equivalent of a read-only database role.
- The app never creates, alters or writes to a configured analytics database.

### 6.2 Application-level SQL validator

- Before execution, the statement must:
  - Be a single statement (no `;`-separated batches)
  - Start with `SELECT` or `WITH` ... `SELECT` (CTE)
  - Contain no DDL or DML keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `REPLACE`, `ATTACH`, `PRAGMA`, etc.)
- Every table referenced must exist in the database. A table that exists but is not in the retrieved context is flagged, and triggers the full-schema repair in section 5.9.
- A failed check rejects the statement, does not execute it, and shows the user a failure with the reason.
- The validator uses a real SQL parser, not string matching alone.

## 7. Limits and guardrails

### 7.1 Row cap

- Results are capped at **1,000 rows** (default; configurable app-wide).
- When truncated, the table shows "Showing first 1,000 of N rows" where N is obtained by a count query where feasible; otherwise "Showing first 1,000 rows (more available)".
- A truncation warning banner is shown (section 8.3).

### 7.2 Personal data

- Not enforced in v1 (section 9).

### 7.3 Query timeout

- A single global timeout applies to all queries. Default: 30 seconds.
- A timed-out query is cancelled. The user is told it timed out and suggested to narrow the question.

### 7.4 Audit log

- Every message is logged with: user, timestamp, database, question text, generated SQL (if any), row count returned, retrieved context item IDs and retrieval confidence (section 5.9), and outcome (success, declined: non-data, clarification requested, validation failure, execution error, timeout).
- Logs are stored in the app's SQLite store, separate from the analytics database.
- Support staff can read the log. Analysts cannot (see section 12, Q5).

## 8. User interface

### 8.1 Layout

- Chat-style interface. Each answer is one message bubble.
- Within an answer, order is: explanation text, then the result table, then a collapsible **"How was this calculated"** section containing: the executed SQL, the resolved date range, the metric definition(s) used, the tables and examples retrieved as context with the retrieval confidence, and the database name.
- Left sidebar: list of the user's conversations (by database, most recent first). Clicking one reopens it with history scrolled to the top.
- Database picker: shown before the first question in a new conversation. Unavailable databases are greyed out with the reason.

### 8.2 Table

- Columns as returned by the query. Numeric columns right-aligned.
- Scrolls horizontally for wide results; header row sticks on vertical scroll.
- Within the 1,000-row cap, client-side sort by column is supported. No filtering in v1.
- **Export:** "Download CSV" button under each table. Exports the rows shown in the table (the capped set).

### 8.3 Warnings and flags

Visible warning banner, placed above the explanation text. Warnings are:

| Condition | Banner text (summary) |
|---|---|
| Result truncated | "Showing first 1,000 of N rows." |
| Possible join duplication | "Totals may be inflated by duplicate rows from a many-to-many join." |
| Repair retry used | "The first query failed and was automatically corrected." |

Banners never auto-dismiss.

### 8.4 History

- Each user sees their own conversations only.
- History re-renders from stored data: the question, the SQL and the explanation. The result table is **re-run from the stored SQL** when the user reopens it. Re-run results may differ from the original if the data has changed. The app shows "Re-run at [time]" next to a reopened table.
- If a re-run fails, the explanation and SQL are still shown, with the failure reason.

### 8.5 Empty results

- A query that returns zero rows shows "No matching records" with the SQL and the filters applied. It is not treated as an error.

## 9. Personal data (amended rule)

- The one-pager states personal data must never be exposed in results. **For v1, the team has decided to keep PII out of scope.** Reason: the app is internal, runs on a single laptop for at most 5 users, and analysts already have direct database access.
- v1 does **not** block, mask or scan for PII.
- This is a deliberate change from the one-pager. `one-pager.md` still contains the original "never" rule and should be amended to match (see section 12, Q1).
- Because result rows are sent to a hosted LLM (section 10.1), the exposure risk is greater than for a direct-access analyst. See section 11, risk R2.

## 10. LLM integration

### 10.1 Provider

- Hosted LLM API (Anthropic Claude family). One model for the whole app, selected in app config. No per-database model choice in v1.
- Data sent per request:
  - Retrieved schema, metric definitions, notes, join hints and few-shot examples for the question (section 5.9). Only the retrieved subset is sent, except in the full-schema repair fallback.
  - Conversation context per section 5.3 (always)
  - **Result rows**, for explanation generation (5.7)
- The intent classifier call sends the question text only.
- Result rows leaving the machine is a decision that needs sign-off. See section 12, Q2.

### 10.2 Prompting

- Versioned prompt templates stored in the repo, one for the intent classifier and one for SQL generation. Each prompt version is recorded in the audit log.
- The intent classifier prompt asks for a single label: `data` or `other`. Its output is parsed strictly. Anything other than `data` is treated as `other`.
- The SQL-generation prompt requires output as a single SELECT statement with no commentary, which is then validated (section 6.2).

### 10.3 Failure handling

- API unavailable or rate-limited: the user sees "The AI service is unavailable. Try again shortly." Nothing is logged as an executed query.
- A malformed model response is treated as a generation failure and counts as the one repair attempt.

## 11. Risks and tradeoffs

| ID | Risk | Source | Mitigation in spec | Residual |
|---|---|---|---|---|
| R1 | LLM-inferred joins silently double-count totals, which the one-pager forbids | Interview decision: LLM infers joins, with optional hints | Join hints, banner on many-to-many or undeclared joins, visible SQL, golden tests | **High.** Silent wrong answers are still possible when no hint exists and no banner fires. The one-pager's "never silent" rule is not fully met. |
| R2 | Result rows sent to a third-party LLM, which may expose business or personal data | Interview decisions: hosted API, explanation from rows, PII out of scope | Flagged for sign-off (Q2). Rows could be limited to aggregates or redacted in a later version. | **High** until Q2 is decided. |
| R3 | Out-of-scope analytical questions (e.g. "why did sales drop?") get SQL-backed answers that don't really address them | Consequence of binary intent check: only non-data messages are declined | SQL generation prompt instructs the model to return a clarification or refusal when a question can't be answered with a single SELECT. Golden suite includes out-of-scope cases. | Medium. Users may still act on partial answers. |
| R9 | Prompt injection: text in a question, or in result rows, tries to change app behavior | New intent classifier, rows used for explanation | Classifier catches non-data messages. Prompts treat rows and questions as data. SQL validator and read-only connection limit the damage. Golden suite includes injection cases. | Medium. The classifier is an LLM and can be fooled. The validator and read-only layers are the real guarantee. |
| R10 | Classifier misroutes a data question as `other` (false decline) or lets non-data through | New intent classifier | Golden suite includes both cases; declines show example questions so users can rephrase | Medium. False declines are visible to users, which limits the harm. |
| R4 | Explanation misstates numbers | Generated text | Explanation from result rows, visible SQL, golden tests | Medium |
| R5 | Re-run history tables differ from original | Interview decision: store SQL and explanation only | "Re-run at" timestamp, failure shown | Low to medium. Expected behavior, but may confuse analysts. |
| R6 | Laptop hosting: no uptime or backup guarantees | Interview decision: local laptop | Document this. Backup the app's SQLite store manually or on a schedule. | Medium. The laptop going offline stops all users. |
| R7 | SQLite-only means v1 may not match real analytics databases | Interview decision | Clearly stated in section 4.2 | Medium. Confirm the target databases are SQLite (Q4). |
| R8 | Read-only depends on file-level SQLite read-only mode | Section 6.1 | Validator in section 6.2 is a second layer | Low |
| R12 | Retrieval misses a table, column or metric the question needs, so the model answers from partial context | New semantic retrieval (5.9) | Metric synonym matching always includes exact matches; always_include tables; table-reference check triggers full-schema repair; retrieved context shown in "How was this calculated"; golden suite covers retrieval | Medium. Quality depends on the descriptions and synonyms support writes. |
| R11 | Bad or stale few-shot examples teach the model wrong SQL, or copy sensitive values into prompts | New few-shot examples (5.8) | Startup validation skips examples that fail; examples must not contain data values; support review | Medium. Validation catches broken SQL, not wrong SQL that still runs. |

## 12. Open questions

These need an answer before implementation or before sign-off.

- **Q1.** Amend `one-pager.md` to reflect the v1 PII decision (section 9)? Currently it still says "never". Proposed wording: "For v1, personal data is out of scope; the app is internal, single-machine, and max 5 users."
- **Q2.** Are result rows allowed to leave the machine and go to the hosted LLM for explanations? If not, options are: (a) explanations from aggregates only, (b) a local model, or (c) explanations generated without row data. Blocks R2.
- **Q3.** What are the golden-question targets: how many questions, and what pass rate is required before a release? Proposed: 50 questions, 95% pass.
- **Q4.** Are the analytics databases SQLite files? The spec assumes yes (section 4.2). Where are they, and how many are there?
- **Q5.** Who can read the audit log? Proposed: support staff only, via a separate admin view or direct file access. Analysts cannot view it.
- **Q6.** Which front-end approach: a lightweight React app or server-rendered HTMX? The backend is Python (FastAPI). Decide during planning.
- **Q7.** How should a user's stored history be deleted, if at all? The one-pager says "kept indefinitely". Confirm there's no deletion requirement in v1.
- **Q8.** Out-of-scope analytical questions (5.6) pass to SQL generation with no banner. Should the one-pager's "Supported Questions" list be enforced as a boundary, with the SQL generator refusing questions outside the four types? Current decision is no boundary, see R3.
- **Q9.** Join hints: who validates that the declared cardinality is correct? Proposed: support, plus a check in the golden suite.
- **Q10.** Which local embedding model is used for semantic retrieval (section 5.9)? It must run on a laptop without sending questions to a remote service. Also decide whether support gets a preview tool that shows what a question retrieves (tables, columns, metrics, examples), which would make description-writing much easier to get right.
- **Q11.** The retrieval sizes and threshold (4 tables, 10 columns per table, 5 metrics, 5 examples) are starting defaults. How many tables and columns do the target databases have? That determines whether the full-schema fallback is ever affordable, and whether the defaults are enough.

## 13. Decision log (from interview)

| Topic | Decision |
|---|---|
| Read-only | DB-level read-only (SQLite `mode=ro`) plus SQL validator |
| Ambiguity | Ask a clarifying question before running SQL |
| LLM hosting | Hosted API |
| PII | Out of scope for v1; one-pager rule amended (Q1) |
| Large results | Hard cap of 1,000 rows with a warning |
| Explanation accuracy | Generated from result rows, SQL visible, no separate number check |
| Startup failure | Start, mark failing database unavailable |
| Identity | Username and password in app |
| History storage | Local SQLite |
| Stored results | SQL and explanation only; table re-run on reopen |
| Follow-up context | Prior questions, SQL and summaries |
| SQL failure | One automatic repair attempt, then flag |
| Timeouts | Single global timeout (30s default) |
| Audit log | Yes: user, timestamp, DB, question, SQL, row count, outcome |
| Joins | LLM-inferred, with optional config hints and a warning banner |
| Time | Resolved against config calendar, dates shown, clarify when ambiguous |
| Answer layout | Chat bubble: explanation, table, collapsible "How was this calculated" |
| Export | CSV only |
| Flagging | Visible banners, never auto-dismissed |
| Accuracy | Golden question suite |
| Intent check | Binary: data question vs other. Data goes to SQL generation; other is declined with examples. Declines are audit-logged. |
| Out-of-scope analytical questions | Not blocked by the classifier; SQL generation handles them (see R3) |
| Scale | Max 5 users, local laptop |
| Config format | YAML per database, validated at startup |
| Business rules | Structured metrics plus free-text notes |
| Few-shot examples | Support-authored YAML per database; top 5 retrieved by semantic similarity using a local embedding model; validated at startup; skipped if invalid |
| Context for SQL generation | Schema, columns, metrics, notes, join hints and examples are all configurable with descriptions; semantic retrieval sends only the relevant subset; full schema only on the repair fallback |
| Model | One model app-wide |
| Stack | Python backend (FastAPI), web UI (see Q6) |
| Dialects | SQLite only |
| Hosting | Local laptop |
| Secrets | Environment variable references in YAML |
