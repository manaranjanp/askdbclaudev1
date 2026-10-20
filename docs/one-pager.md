# Natural-Language Database Query App: Requirements One-Pager

## Purpose

A web app where business users ask questions of a database in plain English and get back an explanation and the underlying data. Users don't need to know SQL.

## Users

- **Primary:** Business analysts who query data regularly, are comfortable with numbers, and don't write SQL.
- **Secondary:** Support staff, who maintain the configuration files (see Configuration).

## Interaction

- Conversational, like a chatbot.
- Follow-up questions build on the current conversation's context only.
- Switching databases always starts a new conversation.
- Each user has a private history of past interactions, kept indefinitely.
- Users can scroll their history. Search is not required.

## Database Selection

- The app can be configured with multiple databases.
- Before asking questions, the user selects one database.
- Each question runs against that single database. Multi-database questions are out of scope.

## Supported Questions

- **Totals and trends:** e.g. quarter-over-quarter sales.
- **Rankings and top-N:** e.g. the five products with the highest return rate.
- **Filtering to specific records:** e.g. open orders over a threshold for one region.
- **Comparisons across segments:** e.g. churn for new versus existing customers.

## Output

- A plain-English explanation of the answer.
- A table of the underlying results.

## Configuration

- Support maintains configuration files covering:
  - Database connection URLs
  - Schema information (tables, columns, relationships)
  - Business context (definitions and metric rules), used when generating SQL
- Files load at app startup. Changes take effect on restart.
- At startup, the app checks that each configured database connects using the URL in its config file.

## Access Control

- None for v1. Every user can see every configured database.

## Must Never Happen

- Any change to or deletion of data. The app is read-only.
- A wrong answer given silently. Ambiguous questions or incomplete data must be flagged to the user.
- Personal data being exposed in results.

## Out of Scope (v1)

- Multi-database questions
- Scheduled reports and alerts
- Writing data back to any database
- Forecasting and predictions
- Access control by role, team, or region
- Searching user history
