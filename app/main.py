import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from html import escape

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

metadata = MetaData()
notes = Table(
    "notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("body", String(280), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} | prenv</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; }}
body {{ padding: 0 1rem; color: #1f2937; }}
h1 {{ font-size: 1.6rem; margin-bottom: 0.2rem; }}
.meta {{ color: #6b7280; font-size: 0.9rem; }}
.meta span {{ display: inline-block; margin-right: 1rem; }}
form {{ display: flex; gap: 0.5rem; margin: 1.5rem 0; }}
input[type=text] {{ flex: 1; padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 4px; }}
button {{ padding: 0.5rem 1rem; border: 0; border-radius: 4px; background: #15803d; color: white; }}
ul {{ list-style: none; padding: 0; }}
li {{ padding: 0.6rem 0; border-top: 1px solid #e5e7eb; }}
li small {{ color: #6b7280; margin-left: 0.5rem; }}
</style>
</head>
<body>
<h1>{name}</h1>
<p class="meta">
<span>image {image_tag}</span><span>expires {expires_at}</span><span>up {uptime}s</span>
</p>
<form method="post" action="{root}/notes">
<input type="text" name="body" maxlength="280" placeholder="leave a note for the next person"
 required>
<button type="submit">add note</button>
</form>
<ul>{items}</ul>
</body>
</html>
"""


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=280)


class NoteOut(BaseModel):
    id: int
    body: str
    created_at: datetime


def database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    host = os.environ.get("DB_HOST")
    if not host:
        return "sqlite+pysqlite:///:memory:"
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DB_USER", "app"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=host,
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "app"),
    )


def make_engine(url):
    if str(url).startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)


def wait_for_database(engine, attempts=30, delay=1.0):
    for attempt in range(attempts):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def as_utc(stamp):
    if stamp.tzinfo is None:
        return stamp
    return stamp.astimezone(UTC)


def preview_info():
    return {
        "preview": os.environ.get("PREVIEW_NAME", "local"),
        "url": os.environ.get("PREVIEW_URL", "http://localhost:8000"),
        "image_tag": os.environ.get("IMAGE_TAG", "dev"),
        "expires_at": os.environ.get("EXPIRES_AT", "never"),
    }


def create_app(url=None, prefix=None):
    prefix = os.environ.get("PATH_PREFIX", "") if prefix is None else prefix

    @asynccontextmanager
    async def lifespan(running):
        engine = make_engine(url or database_url())
        wait_for_database(engine)
        metadata.create_all(engine)
        app.state.engine = engine
        app.state.started_at = time.monotonic()
        running.state.engine = engine
        yield
        engine.dispose()

    app = FastAPI(title="prenv preview app", lifespan=lifespan)

    def list_notes():
        with app.state.engine.connect() as conn:
            rows = conn.execute(select(notes).order_by(notes.c.id.desc())).mappings().all()
        return [NoteOut(**row) for row in rows]

    def add_note(body):
        with app.state.engine.begin() as conn:
            row = conn.execute(insert(notes).values(body=body).returning(notes)).mappings().one()
        return NoteOut(**row)

    @app.get("/healthz")
    def healthz():
        try:
            with app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except OperationalError as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ok"}

    @app.get("/api/info")
    def info():
        with app.state.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(notes)).scalar_one()
        return {
            **preview_info(),
            "notes": count,
            "uptime_seconds": round(time.monotonic() - app.state.started_at, 1),
            "now": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/notes", response_model=list[NoteOut])
    def get_notes():
        return list_notes()

    @app.post("/api/notes", response_model=NoteOut, status_code=201)
    def post_note(note: NoteIn):
        return add_note(note.body)

    @app.post("/notes")
    def post_note_form(request: Request, body: str = Form(min_length=1, max_length=280)):
        add_note(body)
        return RedirectResponse(f"{request.scope.get('root_path', '')}/", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        items = "".join(
            f"<li>{escape(note.body)}<small>{as_utc(note.created_at):%Y-%m-%d %H:%M} UTC</small>"
            "</li>"
            for note in list_notes()
        )
        details = preview_info()
        return PAGE.format(
            root=request.scope.get("root_path", ""),
            name=escape(details["preview"]),
            image_tag=escape(details["image_tag"]),
            expires_at=escape(details["expires_at"]),
            uptime=int(time.monotonic() - app.state.started_at),
            items=items or "<li>no notes yet</li>",
        )

    if not prefix:
        return app
    outer = FastAPI(lifespan=lifespan)
    outer.mount(prefix, app)
    return outer


app = create_app()
