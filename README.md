# clip-farming

Watches Twitch streams, notices when a moment is blowing up, and cuts it into a vertical 9:16
clip that waits in an approval queue.

It does not guess "viral" from one number. A spike in chat is only the gate: a clip is cut when
**independent signals agree** — many different chatters reacting, the same message from many
people, a jump in stream loudness.

```
Twitch chat ──► detector ──► clipper ──► approval queue ──► publish
                  ▲             ▲
   rolling buffer │             │
streamlink ─► ffmpeg ───────────┘   (the last ~2 minutes of every channel, kept on disk)
```

## Status

| Part | State |
|---|---|
| Rolling recorder, chat reader, multi-signal detector, 9:16 clipper | built, tested, run against live streams |
| Approval queue (`list` / `approve` / `reject`) | built, tested |
| Retention and disk guard (`cleanup`) | built, tested |
| Publishing pipeline: retries, daily quota, per-platform state, no double uploads | built, tested with fake adapters |
| **YouTube Shorts and TikTok adapters** | **not built yet.** Only a `dryrun` adapter exists. |
| Instagram | not planned |

Both platforms gate real public posting behind an app audit (until it passes, uploads are forced
to private), so the adapters wait for approved credentials. Check each platform's current
developer terms before relying on this.

## Responsible use

Only clip channels you own or have the streamer's permission to clip, and check the platform
rules before re-uploading anyone's content. The `channels` list in your config is the allowlist
the agent works from. The tool is made to put a human in the loop: nothing is published without
`approve`.

## Requirements

- Python 3.11 or newer
- [`ffmpeg`](https://ffmpeg.org) and `ffprobe` on your `PATH`
- [`uv`](https://docs.astral.sh/uv/) (or any way to install the package into a virtualenv)

## Quick start

```sh
git clone https://github.com/TheRealRoyalTS/clip-farming
cd clip-farming
uv sync
cp config.example.toml config.toml          # then edit the [[channels]] blocks
uv run clipfarm run https://www.twitch.tv/<channel>
```

Links or plain channel names both work, on the command line and in the config. Links given on the
command line replace the configured list for that run.

You will see these lines in the log:

| Line | Meaning |
|---|---|
| `started (quality …); first spike possible after 120s` | the channel was accepted |
| `warming up: N chat msgs total, M segments buffered` | chat is connected (N > 0) and video is being recorded (M > 0) |
| `spike: 44 vs baseline 17.0, signals: rate+unique (…)` | a moment was detected; the clip is ready about a minute later (23 s post-roll plus the cut) |
| `queued <channel>-<timestamp>` | the clip is ready in the queue |
| `N chat spike(s) rejected (single signal)` | chat spiked, but no second signal agreed |
| `recorder stopped (exit 1); offline?` | the channel is not live; it retries every 15 s |

No spike can fire in the first 120 s, because there is no baseline yet.

## Commands

```sh
clipfarm [-c config.toml] run [links…]       # watch channels; record, detect, cut
clipfarm list [pending|approved|rejected|published]
clipfarm approve <id>                        # pending → approved
clipfarm reject <id>
clipfarm publish                             # publish all approved clips
clipfarm cleanup [--dry-run]                 # apply the retention rules
```

Clips live in `data/queue/<state>/<id>/clip.mp4` next to a `meta.json` that records the spike
(rate, baseline, which signals fired). Open one with any video player, for example
`xdg-open data/queue/pending/<id>/clip.mp4`.

Run only one instance per channel: two instances write the same buffer and clip files.

## How a moment is detected

Everything is measured against **the channel's own recent chat**, so a big and a small channel are
judged on different scales.

1. **Gate — chat rate.** Messages in a 5 s window (messages with hype terms count double) must
   exceed the channel's baseline over the previous 120 s by `sigma` standard deviations *and* by
   `min_ratio` times, and reach `min_messages`.
2. **Signals.** On top of the gate, at least `min_signals` (default 2, the gate counts as one)
   must hold:
   - `unique` — enough different chatters, and not mostly one person spamming
   - `same` — the same message (case and spacing ignored) from several different chatters
   - `audio` — the loudest of the last three segments is `audio_jump_db` above the median of the
     ten before
3. **Cooldown.** After a clip, the channel is quiet for `cooldown_seconds`. A spike that lacks a
   second signal does not start the cooldown, because the signal may still arrive in the same
   burst.

The thresholds are starting points, not truths. Every clip's `meta.json` stores the numbers it
fired on, so you can compare them with your own judgement of the clips and tune from there.

## Configuration

`config.example.toml` documents every key. The main ones:

| Section | Keys | Purpose |
|---|---|---|
| `[stream]` | `quality` | streamlink quality list; first available wins (default `720p60,720p,best`) |
| `[clip]` | `pre_seconds`, `post_seconds`, `buffer_seconds` | clip window (default 15 s before, 20 s after) |
| `[detector]` | `sigma`, `min_ratio`, `min_messages`, `min_signals`, … | how sensitive detection is |
| `[retention]` | `rejected_hours`, `published_days`, `pending_days`, `min_free_gb` | what is deleted when |
| `[publish]` | `platforms`, `daily_limits`, `attempts` | where clips go and how often |
| `[[channels]]` | `url` or `name` | the channels to watch |

`config.toml` and the whole `data/` directory are git-ignored.

## Disk and bandwidth

- The recorder downloads the whole stream while a channel is live, whether or not a clip fires.
  Measured on one channel: about 3 Mbit/s at 720p60, roughly 1.5 GB per hour. 1080p60 is about
  twice that. Lower `quality` to save bandwidth.
- On disk, the rolling buffer stays at about two minutes of video per channel.
- A 35 s clip is roughly 9–17 MB (observed range).
- **Retention** deletes only `clip.mp4`; `meta.json` and `publish.json` stay. Rejected clips lose
  their video after 24 h, published ones after 7 days, undecided ones after 3 days (then they move
  to `rejected`, marked `expired`). **Approved clips are never deleted.** The running agent applies
  this hourly, `clipfarm cleanup --dry-run` shows what it would do.
- **Disk guard:** below `min_free_gb` (default 2) no new clip is cut.

## Design notes

Things that were measured on live streams and shaped the code:

- Segments are 6 s long. Twitch delivers video in blocks that arrive at once, and segment files are
  named by the wall-clock second, so shorter segments from one block overwrite each other (with
  2 s segments only 16 s of 45 s survived). A test pins this.
- Clip selection uses each segment's real length (the gap to the next one), not a constant,
  because `-c copy` only cuts at keyframes.
- The blurred background of the 9:16 layout is computed at a quarter of the size and scaled up;
  blurring at full size made encoding about five times slower.
- Chat is read anonymously over IRC (`justinfan…`), so no Twitch login is needed to detect.

## Publishing pipeline

Adapters implement `publish(request) -> result` and raise one of four errors, which the pipeline
handles differently:

| Error | Pipeline reaction |
|---|---|
| `TransientError` | retry with exponential backoff |
| `RateLimited` | stop using that platform for this run, clip stays unfinished |
| `AuthError` | record the failure, stop that platform for this run |
| `PermanentError` | record the failure; a later run tries again |

Per-clip results are kept in `publish.json`, so a re-run only retries the platforms that are not
done and never uploads the same clip twice to the same platform. Add an adapter by registering a
factory in `src/clipfarm/publishers/__init__.py`. Secrets come from environment variables
(`credentials.require_env`) and OAuth tokens from a `0600` file store (`credentials.TokenStore`).

## Development

```sh
uv sync --extra dev
uv run pytest -q          # includes tests that run real ffmpeg
uv run ruff check . && uv run ruff format --check .
```

Tests that need `ffmpeg` are skipped when it is not installed. CI runs lint and the full suite.

## License

[MIT](LICENSE)
