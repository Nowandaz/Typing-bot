# Notes — a small Flask app (GhostWriter sample project)

A multi-file demo project for GhostWriter's project mode: a notes manager with
models, a JSON-backed store, HTML and API routes, templates, static CSS/JS and
tests, spread across subfolders.

```
app/
  __init__.py        application factory
  config.py
  models/            Note and Tag dataclasses
  services/          JSON store + search
  routes/            HTML pages and JSON API
  templates/         Jinja templates
  static/            css + js
tests/               unit and route tests
run.py               entry point
```

Run it:

```
pip install -r requirements.txt
python run.py
```
