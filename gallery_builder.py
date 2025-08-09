
from jinja2 import Environment, FileSystemLoader, select_autoescape
import json, os

PUBLIC_DIR = "public"
TEMPLATES_DIR = "templates"

os.makedirs(PUBLIC_DIR, exist_ok=True)

with open(os.path.join(PUBLIC_DIR, "submissions.json"), "r", encoding="utf-8") as f:
    data = json.load(f)

env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape())
template = env.get_template("index.html.j2")
html = template.render(items=data)

with open(os.path.join(PUBLIC_DIR, "index.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("Static site generated at public/index.html")
