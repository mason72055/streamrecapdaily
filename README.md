# Stream Recap Daily
Every morning this picks the most-watched Twitch clips from the last 24 hours, turns them into 9:16 videos with subtitles, and schedules them on TikTok, YouTube Shorts and Instagram through Buffer.

- Settings (post times, how many clips, streamers to prefer or block): `config.json`
- Run a test by hand: Actions tab -> "Daily clips" -> Run workflow (leave "Test only" checked).

## GTA 6 page
Same code, settings in `config_gta.json` (5 GTA 6-only clips a day, posted through its own Buffer).
- Runs every morning from the "GTA 6 clips" workflow.
- Shares Stream Recap Daily's Buffer. Each page only posts to its own channels: `buffer_channel_names` (only these) / `buffer_exclude_channel_names` (all but these) in its config.

## Hook text
Every clip gets a short white line at the top (from the clip's title, or a rotating fallback). Turn off per page with `"hook_text": false`.
