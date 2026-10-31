"""Tiny markdown -> standalone HTML converter (headings, tables, code, lists).

Good enough for docs/learn.md; no third-party dependency.
"""
import html
import re
import sys


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def convert(md: str) -> str:
    lines = md.split("\n")
    out = []
    i = 0
    in_code = False
    code_buf = []
    list_open = False
    table_buf = []

    def flush_list():
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    def flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows = [r for r in table_buf if not re.match(r"^\s*\|[\s:|-]+\|\s*$", r)]
        html_rows = []
        for idx, row in enumerate(rows):
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            tag = "th" if idx == 0 else "td"
            html_rows.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
        out.append("<table>" + "".join(html_rows) + "</table>")
        table_buf = []

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
                code_buf = []
                in_code = False
            else:
                flush_list()
                flush_table()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_list()
            table_buf.append(stripped)
            i += 1
            continue
        flush_table()

        if not stripped:
            flush_list()
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            flush_list()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_list()
            out.append(f"<blockquote>{inline(stripped.lstrip('> ').strip())}</blockquote>")
            i += 1
            continue

        if re.match(r"^[-*]\s+", stripped):
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append("<li>" + inline(re.sub(r"^[-*]\s+", "", stripped)) + "</li>")
            i += 1
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_list()
            out.append("<p class='step'>" + inline(stripped) + "</p>")
            i += 1
            continue

        flush_list()
        out.append("<p>" + inline(stripped) + "</p>")
        i += 1

    flush_list()
    flush_table()
    if in_code and code_buf:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
    return "\n".join(out)


CSS = """
:root{--bg:#f7f8fb;--card:#fff;--tx:#1f2733;--dim:#5b6675;--line:#e6eaf2;--accent:#4f7cff;--code:#f2f5fb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
     font:15px/1.8 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
main{max-width:880px;margin:0 auto;padding:48px 28px 96px}
h1{font-size:30px;line-height:1.3;margin:0 0 6px}
h2{font-size:21px;margin:44px 0 12px;padding-top:20px;border-top:1px solid var(--line)}
h3{font-size:17px;margin:28px 0 10px;color:#2a3444}
h4{font-size:15px;margin:20px 0 8px}
p{margin:10px 0;color:#26303d}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
blockquote{margin:14px 0;padding:10px 16px;background:#eef3ff;border-left:3px solid var(--accent);
           border-radius:0 8px 8px 0;color:#33415c}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:13px;
     font-family:ui-monospace,Consolas,monospace}
pre{background:#0f1622;color:#d6deeb;padding:16px 18px;border-radius:10px;overflow:auto;font-size:13px;line-height:1.6}
pre code{background:none;padding:0;color:inherit}
table{border-collapse:collapse;width:100%;margin:16px 0;background:var(--card);
      border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px}
th,td{border-bottom:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top}
th{background:#f0f4fd;font-weight:600}
tr:last-child td{border-bottom:none}
ul{margin:10px 0;padding-left:22px}
li{margin:4px 0}
hr{border:none;border-top:1px solid var(--line);margin:32px 0}
.step{margin:6px 0;padding-left:6px}
footer{margin-top:60px;color:var(--dim);font-size:12.5px;text-align:center}
"""


def main(src: str, dst: str, title: str) -> None:
    md = open(src, encoding="utf-8").read()
    body = convert(md)
    page = (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"</head><body><main>{body}"
        "<footer>QDLS · 本地 AI 翻唱引擎 · 本文档随仓库更新</footer>"
        "</main></body></html>"
    )
    open(dst, "w", encoding="utf-8").write(page)
    print("wrote", dst, len(page), "bytes")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "QDLS")
