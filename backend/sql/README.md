# Backend SQL

When `DATABASE_URL` is configured, Backend executes the idempotent SQL files in
filename order to maintain the request/process ledger. Without `DATABASE_URL`,
the Direct generation runtime operates without database persistence.

`backend.sql` is an explicit wheel resource package. Initialization remains owned
by `backend/app/database/session.py`.
