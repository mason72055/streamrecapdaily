"""Research helper: for each YouTube URL, save title, length, chapters and a
compact timestamped transcript (one line per ~20s) to video_info.json."""
import json, subprocess, sys, re, pathlib, html

urls = [u.strip() for u in sys.argv[1].split(",") if u.strip()]
out = []
tmp = pathlib.Path("vi_tmp"); tmp.mkdir(exist_ok=True)
for u in urls:
    rec = {"url": u}
    try:
        r = subprocess.run(["yt-dlp", "-J", "--skip-download", "--no-warnings", u],
                           capture_output=True, text=True, timeout=120)
        j = json.loads(r.stdout or "null") or {}
        if not j:
            rec["error"] = r.stderr[-300:]
        rec.update(title=j.get("title"), channel=j.get("channel"), duration=j.get("duration"),
                   upload_date=j.get("upload_date"),
                   chapters=[(round(c["start_time"]), c["title"]) for c in (j.get("chapters") or [])],
                   desc=(j.get("description") or "")[:1500])
        vid = j.get("id") or u[-11:]
        subprocess.run(["yt-dlp", "--skip-download", "--write-auto-subs", "--sub-langs", "en.*,en",
                        "--sub-format", "vtt", "-o", str(tmp / "%(id)s"), u],
                       capture_output=True, text=True, timeout=120)
        vtts = list(tmp.glob(f"{vid}*.vtt"))
        lines, last_t, buf, seen = [], -99, [], set()
        if vtts:
            for blk in vtts[0].read_text(errors="ignore").split("\n\n"):
                m = re.search(r"(\d+):(\d\d):(\d\d)\.\d+ -->", blk)
                if not m:
                    continue
                t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                for ln in blk.split("\n")[1:]:
                    ln = html.unescape(re.sub(r"<[^>]+>", "", ln)).strip()
                    if ln and "-->" not in ln and ln not in seen:
                        seen.add(ln); buf.append(ln)
                if t - last_t >= 20 and buf:
                    lines.append(f"{t//3600}:{t%3600//60:02d}:{t%60:02d} " + " ".join(buf)[:220])
                    buf, last_t = [], t
        rec["transcript"] = lines
    except Exception as e:
        rec["error"] = str(e)
    out.append(rec)
    print(u, rec.get("title"), len(rec.get("chapters") or []), "chapters,", len(rec.get("transcript") or []), "lines", rec.get("error", ""))
pathlib.Path("video_info.json").write_text(json.dumps(out, indent=1))
