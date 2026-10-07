---
name: http-client
description: "Make HTTP requests from python_exec with the http_get / http_post builtins. Use for API calls and data fetching."
internal: true
---

# HTTP Client

Inside `python_exec`, HTTP goes through two builtins. Raw clients (`httpx`, `urllib`,
`requests`) are not importable there: the builtins are the only way.

## GET
```
python_exec(function="http_get", args=["https://api.example.com/data"])
python_exec(function="http_get", kwargs={"url": "https://api.example.com/data",
                                         "headers": {"Authorization": "Bearer TOKEN"}})
```

## POST
```
python_exec(function="http_post", kwargs={"url": "https://api.example.com/data", "json_data": {"key": "value"}})
```

## Tips

- The response comes back as text: parse JSON with `json.loads` (or the `json_parse` builtin).
- Default timeout is 30 s; pass `timeout` for a slow API.
- A web page meant for people (cookies, JavaScript) is a job for `web_fetch` or the browser
  tools, not for these builtins.
- An app's reader is different: it uses `ctx.http` from `run.py`, not these builtins.
