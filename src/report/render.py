"""Render the five self-contained HTML reports with jinja2.

Everything (CSS + a tiny table-sort script) is inlined so the pages open offline
with no external/CDN runtime dependencies.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).parent / "templates"
ASSETS = Path(__file__).parent / "assets"

PAGES = [
    ("index.html", "Dashboard"),
    ("fantasy.html", "Fantasy"),
    ("predictions.html", "Predictions"),
    ("model.html", "Model"),
    ("changelog.html", "Changelog"),
]

SORT_JS = """
document.addEventListener('click', function(e){
  var th = e.target.closest('th.sortable'); if(!th) return;
  var table = th.closest('table'); var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
  var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
  Array.prototype.forEach.call(th.parentNode.children, function(x){x.removeAttribute('data-dir');});
  th.setAttribute('data-dir', dir);
  var rows = Array.prototype.slice.call(table.tBodies[0].rows);
  var num = function(v){ var n = parseFloat(String(v).replace(/[^0-9.\\-]/g,'')); return isNaN(n)?null:n; };
  rows.sort(function(a,b){
    var x=a.cells[idx].getAttribute('data-sort')||a.cells[idx].innerText;
    var y=b.cells[idx].getAttribute('data-sort')||b.cells[idx].innerText;
    var nx=num(x), ny=num(y), r;
    if(nx!==null&&ny!==null){ r=nx-ny; } else { r=String(x).localeCompare(String(y)); }
    return dir==='asc'? r : -r;
  });
  rows.forEach(function(r){ table.tBodies[0].appendChild(r); });
});
"""


def _pct(v, digits=1):
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _price(v):
    try:
        return f"${float(v):.1f}"
    except (TypeError, ValueError):
        return "—"


def _signed(v, digits=1):
    try:
        f = float(v)
        return f"+{f:.{digits}f}" if f >= 0 else f"{f:.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _round(v, digits=2):
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def build_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html", "xml"]))
    env.filters["pct"] = _pct
    env.filters["price"] = _price
    env.filters["signed"] = _signed
    env.filters["r"] = _round
    return env


def render_all(result: dict, output_dir: Path, log=print) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    env = build_env()
    css = (ASSETS / "style.css").read_text()
    ctx_base = dict(result)
    ctx_base.update({"css": css, "sort_js": SORT_JS, "pages": PAGES})
    for filename, _label in PAGES:
        tmpl = env.get_template(filename)
        html = tmpl.render(active=filename, **ctx_base)
        (output_dir / filename).write_text(html)
        log(f"  wrote {filename} ({len(html) // 1024} KB)")
