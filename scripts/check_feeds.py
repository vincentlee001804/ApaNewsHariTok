"""One-off check: which RSS sources respond and how many items they return."""
import re
import urllib.request
import xml.etree.ElementTree as ET

SOURCES = [l.strip() for l in open("RSS_Sources.txt", encoding="utf-8")]
SOURCES = [l for l in SOURCES if l and not l.startswith("#")]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

for url in SOURCES:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            data = r.read()
        try:
            root = ET.fromstring(data)
            items = root.findall(".//item") + root.findall(".//{*}entry")
            titles = []
            for it in items[:3]:
                t = it.findtext("title") or it.findtext("{*}title") or ""
                titles.append(t.strip())
            print(f"OK   {status} items={len(items):3d}  {url}")
            for t in titles:
                print(f"       - {t[:90]}")
        except ET.ParseError:
            print(f"BADXML status={status} bytes={len(data)}  {url}")
    except Exception as e:
        print(f"FAIL {type(e).__name__}: {e}  {url}")
