# Habits agent instructions

Read `README.md` before changing this app. This checkout is an independent
repository. Use the published wheels in `vendor/`; do not import or copy core
implementation code or depend on a sibling platform checkout.

Keep Overview, Water, Budget, Study and Progress in one app, container and
SQLite database. Keep stored owner keys, the SQLite schema, money rules,
append-only audits and web assets unchanged unless a reviewed change says
otherwise. A schema change needs a migration plan and a state-probe comparison
on a disposable copy of old state.

Identity must resolve each opaque gateway assertion through the client. Never
add a raw proxy-header identity fallback, a caller-chosen user or a
request-time identity cache. Existing users' reads must not take the SQLite
write lock; `test_efficiency.py` guards that.

Use `apply_patch` for edits. Run `python -m tools.check` and the portable
checker on the actual image. Use fictional data for tests. A release publication
does not deploy itself; managed installation needs platform owner approval.

Keep credentials, private state and operational evidence out of Git. Public
documentation contains configuration contracts and reasoning, not deployment
accounts, hostnames or machine paths.
