import datetime as dt
import json
import os
import shutil
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


def render(directory, content, filename, mode):
    page = directory / (filename + ".html")
    page.write_text(content, encoding="utf-8")
    dest = directory / filename
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("Chrome unavailable")
    command = [chrome, "--headless", "--disable-dev-shm-usage", "--disable-gpu",
               "--no-sandbox", "--no-pdf-header-footer", "--hide-scrollbars",
               "--window-size=1080,480", f"--user-data-dir={directory / (mode + '-profile')}",
               f"--{mode}={dest}", page.as_uri()]
    subprocess.run(command, check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dest.read_bytes()


def main():
    inputs = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["inputs"]
    date = dt.date.fromisoformat(inputs["issue_date"]).isoformat()
    paper = json.loads(inputs["digest"])
    fields = ["title", "citation", "source", "question", "method", "result", "implication"]
    if any(not isinstance(paper.get(k), str) or not paper[k].strip() for k in fields):
        raise ValueError("Missing issue text")
    if urlsplit(paper["source"]).scheme != "https" or not urlsplit(paper["source"]).hostname:
        raise ValueError("Expected HTTPS source")
    figure = paper.get("figure")
    if not isinstance(figure, list) or len(figure) != 3 or any(
        not isinstance(x, str) or not 1 <= len(x.strip()) <= 75 for x in figure
    ):
        raise ValueError("Expected three concise figure captions")

    sections = [("研究问题", "question"), ("数据与方法", "method"),
                ("主要发现", "result"), ("为什么值得读", "implication")]
    clean = {key: escape(paper[key]) for key in fields}
    paragraphs = "".join(f'<h3>{label}</h3><p>{clean[key]}</p>' for label, key in sections)
    style = """<meta charset="utf-8"><style>
        *{box-sizing:border-box} body{font:17px/1.65 'Noto Sans CJK SC',sans-serif;
        color:#192e3e;background:white;margin:0} main{max-width:900px;margin:auto;padding:30px}
        h1{font-size:27px;line-height:1.3} h3{color:#176b68;margin:16px 0 3px}
        p{margin:0 0 9px} a{color:#176b68;overflow-wrap:anywhere}
        @page{size:A4;margin:13mm} @media print{main{padding:0} body{font-size:12pt}}
        </style>"""
    cards = "".join(f'<article><b>{label}</b><div>{escape(item)}</div></article>'
                    for label, item in zip(["研究对象", "比较方法", "观察结果"], figure))
    chart = f"""<!doctype html>{style}<style>body{{background:#eff5f5;padding:32px 40px}}
        h2{{font-size:27px;margin:0 0 18px}} section{{display:flex;gap:16px}}
        article{{background:white;border-top:6px solid #207875;padding:18px;flex:1}}
        article b{{font-size:19px;color:#176b68}} article div{{margin-top:10px;font-size:20px}}
        </style><h2>研究设计与结果</h2><section>{cards}</section>
        <p style="font-size:14px;margin-top:14px">根据论文原文整理 · {date}</p>"""
    header = f'<p>神经编解码日报 · {date}</p><h1>{clean["title"]}</h1><p>{clean["citation"]}</p>'
    source = f'<p><a href="{clean["source"]}">论文原文</a> · {clean["source"]}</p>'
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        png = render(directory, chart, "figure.png", "screenshot")
        report = f'<!doctype html>{style}<main>{header}{paragraphs}<img style="width:100%" src="figure.png">{source}</main>'
        pdf = render(directory, report, "digest.pdf", "print-to-pdf")
    if not pdf.startswith(b"%PDF-") or not png.startswith(bytes.fromhex("89504e470d0a1a0a")):
        raise RuntimeError("Invalid rendered attachment")
    mail = EmailMessage()
    mail["Subject"] = f"神经编解码日报 · {date} · {paper['title']}"
    sender, recipient = os.environ["SENDER"], os.environ["RECEIVER"]
    mail["From"] = formataddr(("神经编解码日报", sender))
    mail["To"] = recipient
    mail["Date"] = formatdate(localtime=True)
    mail["Message-ID"] = make_msgid()
    plain = [paper["title"], paper["citation"]] + [label + "\n" + paper[key] for label, key in sections]
    mail.set_content("\n\n".join(plain + [paper["source"]]))
    cid = make_msgid()
    mail.add_alternative(f'<html><body>{header}{paragraphs}<img width="620" style="max-width:100%" src="cid:{cid[1:-1]}">{source}</body></html>', subtype="html")
    mail.get_payload()[-1].add_related(png, maintype="image", subtype="png", cid=cid, disposition="inline")
    mail.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"neural-digest-{date}.pdf")
    server, port = os.environ["SMTP_SERVER"], int(os.environ["SMTP_PORT"])
    ctx = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL(server, port, timeout=40, context=ctx) if port == 465 else smtplib.SMTP(server, port, timeout=40)
    with smtp:
        if port != 465:
            smtp.starttls(context=ctx)
        smtp.login(sender, os.environ["SENDER_PASSWORD"])
        if smtp.send_message(mail, from_addr=sender, to_addrs=[recipient]):
            raise RuntimeError("SMTP recipient rejected")
        print("DIGEST_WITH_ATTACHMENTS_SMTP_ACCEPTED", flush=True)


if __name__ == "__main__":
    main()
