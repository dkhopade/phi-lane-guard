# Upstream issue draft

Paste into `github.com/BerriAI/litellm/issues/new`. Read it through first — the
tone is deliberately neutral and evidence-first, which is what gets maintainer
attention rather than a "works for me" reply.

Consider commenting on **#23636** instead of opening a new issue if that one is
still open, since the column placement is the same. Note in your comment that
the UI symptom described there does **not** reproduce on 1.98.0 — only the
column mismatch does.

---

## Title

`[Bug]: store_prompts_in_spend_logs writes request content to proxy_server_request, not messages (v1.98.0)`

---

## What happened?

With `store_prompts_in_spend_logs` enabled via the Admin UI, request and
response content **is** persisted — but the `LiteLLM_SpendLogs.messages` column
remains `{}`. The prompt is written into `proxy_server_request` (nested under a
`messages` key inside that JSON), and the completion into `response`.

The Admin UI displays the content correctly, so this is not a UI regression.
The issue is that a direct database query against the column named `messages`
returns empty while the content is present in the row.

This matters for anyone verifying data handling at the database level — for
example confirming that no sensitive content is persisted. A query against
`messages` returns a false negative.

Related: #23636 reports the same column placement. The UI symptom described
there ("Request/Response Data Not Available") does **not** reproduce on 1.98.0;
the UI shows the content correctly. Only the column mismatch remains.

## Relevant log output

```
                 req          | in_messages | in_response | in_proxy_req | in_metadata
----------------------+-------------+-------------+--------------+-------------
 chatcmpl-4d6f8ad3-6f | f           | t           | t            | f
 chatcmpl-0c542f79-bf | f           | f           | f            | f
```

Row 1 is after enabling the setting; row 2 before. Booleans are
`column::text ILIKE '%<identifier from the prompt>%'`.

Content of `proxy_server_request` on the affected row, truncated:

```json
{"model": "claude-sonnet-4-5",
 "messages": [{"role": "user", "content": "<the prompt text>"}],
 "metadata": {"headers": {"host": "localhost:4000", "accept": "*/*",
   "user-agent": "curl/8.7.1", "content-type": "application/json"},
   "endpoint": "http://localhost:4000/v1/..."}}
```

## Steps to reproduce

1. Start the quickstart stack (gateway + Postgres) on v1.98.0.
2. Add any chat model and send a request. Confirm the baseline:

```sql
SELECT request_id, messages::text, response::text, proxy_server_request::text
FROM "LiteLLM_SpendLogs" ORDER BY "startTime" DESC LIMIT 1;
```

All three are `{}`.

3. Enable **Store Prompts in Spend Logs** under Admin Settings and save.
4. Send a fresh request with a distinctive string in the prompt.
5. Query again:

```sql
SELECT request_id,
  messages::text             ILIKE '%DISTINCTIVE%' AS in_messages,
  response::text             ILIKE '%DISTINCTIVE%' AS in_response,
  proxy_server_request::text ILIKE '%DISTINCTIVE%' AS in_proxy_req
FROM "LiteLLM_SpendLogs" ORDER BY "startTime" DESC LIMIT 1;
```

Observed: `in_messages = f`, `in_response = t`, `in_proxy_req = t`.
Expected: request content in `messages`, or the placement documented.

## Expected behaviour

Either the request messages are written to `LiteLLM_SpendLogs.messages`, or the
documentation states that request content lives in `proxy_server_request` so
that database-level verification targets the right column.

## Environment

- LiteLLM `v1.98.0`, image `docker.litellm.ai/berriai/litellm-database:latest`,
  digest `sha256:653fd100d630`
- Postgres 16, quickstart `docker-compose.yml`
- Setting enabled via Admin UI (not config file); confirmed persisted in
  `LiteLLM_Config`
- Single worker, no Redis
