"""Static site generator — renders content to HTML."""

import shutil
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

from convocation.config import settings
from convocation.content.store import ContentStore


def render_site(store: ContentStore | None = None):
    """Render all content to static HTML in the output directory."""

    if store is None:
        store = ContentStore()

    output = settings.output_abs_path
    output.mkdir(parents=True, exist_ok=True)

    # Clean output (except .git if present)
    for item in output.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Set up Jinja2
    template_dir = Path(__file__).parent.parent / "templates" / "site"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    md = markdown.Markdown(extensions=["extra", "meta", "toc", "nl2br"])

    site_ctx = {
        "site_title": settings.site_title,
        "site_description": settings.site_description,
        "site_url": settings.site_url,
        "vapid_public_key": settings.vapid_public_key,
    }

    # Gather all content
    announcements = store.list_content("announcements")
    events = store.list_content("events")
    pages = store.list_content("pages")
    members = store.list_content("members")

    # Sort pages by nav_order
    pages.sort(key=lambda p: p["metadata"].get("nav_order", 99))

    # Render markdown bodies
    def render_md(item):
        md.reset()
        item["html"] = md.convert(item["body"])
        return item

    announcements = [render_md(a) for a in announcements]
    events = [render_md(e) for e in events]
    pages = [render_md(p) for p in pages]
    members = [render_md(m) for m in members]

    nav_items = [{"title": p["metadata"].get("title", p["slug"]), "slug": p["slug"]} for p in pages]

    common = {**site_ctx, "nav_items": nav_items}

    # Index page
    _render_template(env, "index.html", output / "index.html", {
        **common,
        "announcements": announcements[:10],
        "events": events[:10],
    })

    # Announcement pages
    ann_dir = output / "announcements"
    ann_dir.mkdir(exist_ok=True)
    for a in announcements:
        _render_template(env, "announcement.html", ann_dir / f"{a['slug']}.html", {**common, "item": a})
    _render_template(env, "announcements.html", ann_dir / "index.html", {**common, "announcements": announcements})

    # Event pages
    evt_dir = output / "events"
    evt_dir.mkdir(exist_ok=True)
    for e in events:
        _render_template(env, "event.html", evt_dir / f"{e['slug']}.html", {**common, "item": e})
    _render_template(env, "events.html", evt_dir / "index.html", {**common, "events": events})

    # Static pages
    for p in pages:
        _render_template(env, "page.html", output / f"{p['slug']}.html", {**common, "page": p})

    # Members page
    mem_dir = output / "members"
    mem_dir.mkdir(exist_ok=True)
    _render_template(env, "members.html", mem_dir / "index.html", {**common, "members": members})

    # Copy static assets
    static_src = Path(__file__).parent.parent / "static"
    if static_src.exists():
        static_dst = output / "static"
        if static_dst.exists():
            shutil.rmtree(static_dst)
        shutil.copytree(static_src, static_dst)


def _render_template(env: Environment, template_name: str, output_path: Path, context: dict):
    """Render a single template to a file."""
    try:
        template = env.get_template(template_name)
        html = template.render(**context)
        output_path.write_text(html, encoding="utf-8")
    except Exception as e:
        # If template doesn't exist yet, write a placeholder
        output_path.write_text(f"<!-- Template {template_name} not found: {e} -->", encoding="utf-8")
