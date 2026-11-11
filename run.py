"""Stream Recap Daily - picks yesterday's top Twitch clips, makes them 9:16 with
subtitles, hosts them, and schedules them on TikTok / YouTube Shorts / Instagram
through Buffer.

Run:  python run.py            (real run: posts to Buffer)
      python run.py --dry-run  (makes the clips only, posts nothing)
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
WORK = ROOT / "work"
MEDIA = ROOT / "media_branch"
CFG = json.loads((ROOT / "config.json").read_text())

TWITCH_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
BUFFER_KEY = os.environ.get("BUFFER_API_KEY", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


# ----------------------------------------------------------------- Twitch ---
def twitch_token():
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": TWITCH_ID, "client_secret": TWITCH_SECRET,
        "grant_type": "client_credentials"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Twitch login failed ({r.status_code}): {r.text[:200]} "
                           f"[client id length {len(TWITCH_ID)}, secret length {len(TWITCH_SECRET)}]")
    return r.json()["access_token"]


def helix(token, path, params):
    for attempt in range(3):
        r = requests.get("https://api.twitch.tv/helix/" + path, params=params, headers={
            "Client-Id": TWITCH_ID, "Authorization": f"Bearer {token}"}, timeout=30)
        if r.status_code == 429:
            time.sleep(5)
            continue
        r.raise_for_status()
        return r.json().get("data", [])
    return []


def find_candidates(token, posted):
    now = dt.datetime.now(dt.timezone.utc)
    window = {"started_at": (now - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    langs = set(CFG["languages"])

    broadcaster_ids = set()
    for s in helix(token, "streams", {"first": CFG["top_streams_to_scan"], "language": CFG["languages"][0]}):
        broadcaster_ids.add(s["user_id"])
    logins = CFG["seed_streamers"] + CFG["campaign_streamers"]
    for i in range(0, len(logins), 100):
        for u in helix(token, "users", [("login", l) for l in logins[i:i + 100]]):
            broadcaster_ids.add(u["id"])

    clips = {}
    for bid in broadcaster_ids:
        for c in helix(token, "clips", {"broadcaster_id": bid, "first": 5, **window}):
            clips[c["id"]] = c
    for g in helix(token, "games/top", {"first": CFG["top_games_to_scan"]}):
        for c in helix(token, "clips", {"game_id": g["id"], "first": 20, **window}):
            clips[c["id"]] = c

    campaign = {s.lower() for s in CFG["campaign_streamers"]}
    blocked = {s.lower() for s in CFG["blocked_streamers"]}
    good = []
    for c in clips.values():
        name = c["broadcaster_name"].lower()
        if c["id"] in posted or name in blocked:
            continue
        if c.get("language") and c["language"].split("-")[0] not in langs:
            continue
        if not (CFG["min_seconds"] <= c["duration"] <= CFG["max_seconds"]):
            continue
        c["score"] = c["view_count"] * (3 if name in campaign else 1)
        good.append(c)
    good.sort(key=lambda c: c["score"], reverse=True)

    picked, per = [], {}
    for c in good:
        b = c["broadcaster_id"]
        if per.get(b, 0) >= CFG["max_clips_per_streamer"]:
            continue
        per[b] = per.get(b, 0) + 1
        picked.append(c)
        if len(picked) >= CFG["clips_per_day"] + 2:  # spares in case a download fails
            break
    log(f"Scanned {len(clips)} clips from {len(broadcaster_ids)} streamers; {len(good)} usable.")
    return picked


# ------------------------------------------------------------------ Editing ---
_whisper = None


def transcribe(path):
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    segs, _ = _whisper.transcribe(str(path), word_timestamps=True, vad_filter=True)
    words = []
    for s in segs:
        for w in (s.words or []):
            if w.word.strip():
                words.append((w.start, w.end, w.word.strip()))
    return words


def ass_time(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words, header, duration, path):
    lines = []
    chunk = []
    for w in words:
        if chunk and (len(chunk) >= 3 or w[0] - chunk[-1][1] > 0.6):
            lines.append(chunk)
            chunk = []
        chunk.append(w)
    if chunk:
        lines.append(chunk)

    ev = [f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Header,,0,0,0,,{ass_escape(header)}"]
    for i, ch in enumerate(lines):
        start = ch[0][0]
        end = ch[-1][1]
        if i + 1 < len(lines):
            end = min(max(end, start + 0.4), lines[i + 1][0][0])
        text = ass_escape(" ".join(w[2] for w in ch)).upper()
        ev.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{text}")

    path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Noto Sans,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,8,60,60,1330,1\n"
        "Style: Header,Noto Sans,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,60,60,1300,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(ev) + "\n", encoding="utf-8")


def make_vertical(src, ass, dst, duration):
    vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          "gblur=sigma=40,eq=brightness=-0.18[bg];"
          "[0:v]scale=1080:-2[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass={ass}[v]")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-t", str(min(duration, CFG["max_seconds"])),
                    "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                    "-r", "30", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    str(dst)], check=True)


def produce(clip):
    cid = clip["id"]
    raw = WORK / f"{cid}.mp4"
    subprocess.run([sys.executable, "-m", "yt_dlp", "-q", "-f", "best[ext=mp4]/best",
                    "-o", str(raw), clip["url"]], check=True, timeout=300)
    words = transcribe(raw)
    ass = WORK / f"{cid}.ass"
    build_ass(words, clip["broadcaster_name"], clip["duration"], ass)
    safe = re.sub(r"[^A-Za-z0-9_-]", "", cid)[:60]
    out = OUT / f"{dt.date.today():%Y%m%d}-{safe}.mp4"
    make_vertical(raw, ass, out, clip["duration"])
    clip["transcript"] = " ".join(w[2] for w in words)
    return out


# ------------------------------------------------------------------ Hosting ---
def load_media_branch():
    if MEDIA.exists():
        shutil.rmtree(MEDIA)
    url = f"https://x-access-token:{GH_TOKEN}@github.com/{REPO}.git"
    ok = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "media", url, str(MEDIA)],
                        capture_output=True).returncode == 0
    if not ok:
        MEDIA.mkdir(parents=True)
    (MEDIA / "clips").mkdir(exist_ok=True)
    state = MEDIA / "posted.json"
    return json.loads(state.read_text()) if state.exists() else {}


def publish_media(files, posted):
    cutoff = time.time() - 4 * 86400
    for f in (MEDIA / "clips").glob("*.mp4"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
    for f in files:
        shutil.copy(f, MEDIA / "clips" / f.name)
    (MEDIA / "posted.json").write_text(json.dumps(posted, indent=1))
    shutil.rmtree(MEDIA / ".git", ignore_errors=True)
    g = lambda *a: subprocess.run(["git", "-C", str(MEDIA), *a], check=True, capture_output=True)
    g("init", "-q", "-b", "media")
    g("config", "user.name", "streamrecap-bot")
    g("config", "user.email", "bot@users.noreply.github.com")
    g("add", "-A")
    g("commit", "-q", "-m", f"clips {dt.date.today()}")
    g("push", "-q", "--force", f"https://x-access-token:{GH_TOKEN}@github.com/{REPO}.git", "media")
    urls = {}
    for f in files:
        u = f"https://raw.githubusercontent.com/{REPO}/media/clips/{f.name}"
        for _ in range(30):
            if requests.head(u, timeout=20).status_code == 200:
                break
            time.sleep(10)
        urls[f.name] = u
    return urls


# ------------------------------------------------------------------- Buffer ---
def buffer_q(query, variables=None):
    r = requests.post("https://api.buffer.com", json={"query": query, "variables": variables or {}},
                      headers={"Authorization": f"Bearer {BUFFER_KEY}"}, timeout=60)
    data = r.json()
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"])[:500])
    return data["data"]


def buffer_channels():
    orgs = buffer_q("query { account { organizations { id name } } }")["account"]["organizations"]
    chans = []
    for o in orgs:
        chans += buffer_q("query($o: OrganizationId!) { channels(input: {organizationId: $o}) "
                          "{ id name service isQueuePaused } }", {"o": o["id"]})["channels"]
    return chans


CREATE = """mutation($input: CreatePostInput!) { createPost(input: $input) {
  ... on PostActionSuccess { post { id dueAt } }
  ... on MutationError { message } } }"""


def schedule_times(n):
    tz = ZoneInfo(CFG["timezone"])
    now = dt.datetime.now(tz)
    times = []
    for i in range(n):
        hh, mm = map(int, CFG["post_times_central"][i % len(CFG["post_times_central"])].split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t < now + dt.timedelta(minutes=20):
            t = now + dt.timedelta(minutes=30 + 60 * i)
        times.append(t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    return times


def post_clip(clip, url, due, chans):
    name = clip["broadcaster_name"]
    title = clip["title"].strip() or f"{name} moment"
    text = f"{title} 😭\n\n🎥 {name} on Twitch\n{CFG['hashtags']} #{clip['broadcaster_name'].lower()}"
    results = []
    for ch in chans:
        svc = ch["service"]
        meta = {}
        if svc == "youtube":
            meta = {"youtube": {"title": f"{title} | {name} #shorts"[:100], "privacy": "public",
                                "categoryId": "20", "madeForKids": False, "notifySubscribers": True}}
        elif svc == "instagram":
            meta = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
        elif svc == "tiktok":
            meta = {"tiktok": {"title": title[:90]}}
        else:
            continue
        inp = {"channelId": ch["id"], "text": text, "schedulingType": "automatic",
               "mode": "customScheduled", "dueAt": due,
               "assets": [{"video": {"url": url, "metadata": {"thumbnailOffset": 1000}}}],
               "metadata": meta}
        try:
            res = buffer_q(CREATE, {"input": inp})["createPost"]
            ok = "post" in res
            results.append(f"{svc}: {'scheduled ' + due if ok else 'ERROR ' + res.get('message', '')}")
        except Exception as e:  # keep going for the other platforms
            results.append(f"{svc}: ERROR {e}")
    return results


# --------------------------------------------------------------------- Main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)

    chans = []
    try:
        chans = [c for c in buffer_channels() if c["service"] in ("tiktok", "youtube", "instagram")]
        log("Buffer channels: " + ", ".join(f"{c['service']}={c['name']}" for c in chans))
    except Exception as e:
        log(f"Buffer check FAILED: {e} [key length {len(BUFFER_KEY)}]")
        if not args.dry_run:
            raise

    posted = {} if args.dry_run else load_media_branch()
    token = twitch_token()
    log("Twitch login OK")
    picks = find_candidates(token, posted)

    made = []
    for clip in picks:
        if len(made) >= CFG["clips_per_day"]:
            break
        try:
            f = produce(clip)
            made.append((clip, f))
            log(f"Made: {clip['broadcaster_name']} - {clip['title']} ({clip['view_count']} views) -> {f.name}")
        except Exception as e:
            log(f"Skipped {clip['id']}: {e}")

    summary = [{"streamer": c["broadcaster_name"], "title": c["title"], "views": c["view_count"],
                "twitch_url": c["url"], "file": f.name, "transcript": c.get("transcript", "")}
               for c, f in made]

    if not args.dry_run and made:
        for c, _ in made:
            posted[c["id"]] = dt.date.today().isoformat()
        urls = publish_media([f for _, f in made], posted)
        for (c, f), due, s in zip(made, schedule_times(len(made)), summary):
            s["posts"] = post_clip(c, urls[f.name], due, chans)
            log(f"{c['broadcaster_name']}: " + "; ".join(s["posts"]))

    (OUT / "summary.json").write_text(json.dumps({"log": log_lines, "clips": summary}, indent=1))
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        Path(step).write_text("## Stream Recap Daily\n\n" + "\n".join("- " + l for l in log_lines))
    if not made:
        sys.exit("No clips were made today.")


if __name__ == "__main__":
    main()
