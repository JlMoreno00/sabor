---
name: sabor-import
description: Import a recipe into Sabor from a web page, pasted text, a photo or screenshot, or a video (YouTube, Vimeo, TikTok, or a file on the computer, which is the way for Instagram). Use when the user shares a recipe link, text, image or video and wants it saved. Sabor contains no AI model; you, the agent, read the source and save the recipe yourself.
---

# Sabor import: recipes from any source

Talk to the user in their language. Sabor itself never sends recipes, images, audio or
video to an AI service: it fetches or prepares the source, and **you** read it and write
the recipe. Nothing is saved until the user has seen a preview and agreed.

**Mealie's ingredient parser.** When the recipe store is Mealie (`recipe_store.adapters`
in `sabor_capabilities()` shows `mealie`), saving a recipe with ingredients through
`create_recipe`, or committing an `import_recipe`, also runs Mealie's ingredient parser
when Sabor's `SABOR_NORMALIZE_IMPORTS` setting is on (the default). Depending on how
Mealie is configured, it may send the ingredient lines to the AI provider set up in
Mealie. Tell the user this when you show the preview, before they approve. The parser
is chosen in Sabor's configuration (`sabor-mcp setup`), never from the chat.

## 1. Check what is available

Call `sabor_capabilities()`. Use only the tools in `data.registered_tools`; importing
needs `import_recipe` and `create_recipe`. `data.capabilities.import.adapters` lists the
importers: `url` (web pages) and `video` (`bundle`). The `local_files_require_stdio`
limitation means local video files are not accepted by this server.

## 2. Pick the route for the source

| Source | What to do |
|---|---|
| Web page with a recipe | `import_recipe(kind='url', source=URL, dry_run=True)`, then commit (below) |
| Pasted text | Read it yourself and go to step 3 |
| Photo or screenshot | Read it yourself and go to step 3 |
| Video link or file | `import_recipe(kind='video', source=URL or absolute path)`, then step 4 |

### Web pages

`import_recipe(kind='url', source=URL, dry_run=True)` returns `data.candidates`. Show the
first candidate (name, ingredients, steps) to the user. When they agree, call
`import_recipe(kind='url', source=URL, dry_run=False)`. `not_found` means the page has no
recipe Sabor can read: open the page yourself if you can and go to step 3.

## 3. Write the recipe yourself (text, images, videos)

Extract the name, servings, times, ingredients (one line each, with quantities) and steps
in the user's language. Do not invent quantities that are not in the source; say they
are missing. Show the preview and wait for the user's agreement, then call
`create_recipe(name=…, description=…, recipe_yield=…, total_time=…, ingredients=[…],
instructions=[…], tags=[…], source_url=…)`. Put the source (link or "photo from the
user") in the description; for videos also pass `source_url` and the tag `video-import`.

## 4. Videos

`import_recipe(kind='video', source=…)` returns a media bundle and writes no recipe:

- `metadata`: `title`, `uploader`, `duration`, `upload_date`, `source_url`, `platform`
  and `description` (the video's own description,
  which often lists the ingredients); local files have `source_path` instead of a URL.
- `transcript`: `{source, language, text}`. `source` is `subtitles` (the platform's,
  always preferred) or `local_asr` (Sabor's optional on-device transcriber, when the
  `sabor-mcp[transcribe]` extra is installed; `language` is the one it detected). It is
  `null` when neither is available.
- `frames`: up to 8 frames, attached as images, each with its `time`. If fewer than 8
  arrive, some could not be extracted: tell the user the visual check is partial. When
  Sabor runs on the computer (stdio), a frame may also have a `path` to its file.
- `audio_path`: on stdio, the soundtrack as a file (`audio.m4a`), otherwise `null`. Use
  it only if you can transcribe audio yourself; never send it to another service.
- `limitations` and `next_step`: read them, then follow step 3.

Combine the description, transcript and frames. When they disagree, prefer what is said
or written in the video over what is only seen, and tell the user what was unclear.

### When the bundle is incomplete

- `no_transcript`: rely on the description and frames. If they do not give the
  quantities, ask the user to read them out or type them. When the message names the
  `sabor-mcp[transcribe]` extra, mention that it would transcribe the audio on this
  computer; do not install it yourself.
- `no_frames`: read its `message`. If it says ffmpeg and ffprobe must be installed, tell
  the user frames need them; otherwise tell the user no frames could be extracted. In
  both cases continue with the description and transcript.
- Instagram links usually fail: without a logged-in session (Sabor sends none) Instagram
  gives no video duration, and Sabor refuses a video without one (`unsupported_operation`).
  Ask for the saved file instead, as below.
- A download error (`upstream_error` from `yt_dlp`), a TikTok short link or a platform
  Sabor does not fetch: ask the user to save the video on their computer and give you its
  absolute path. Sabor reads local files only when it runs on the same computer (stdio)
  and only under `SABOR_MEDIA_ROOTS` (their home folder by default).
- `invalid_argument` for a local file: the path is not an absolute path to a video file
  under `SABOR_MEDIA_ROOTS`. Ask for the right path; do not try other folders.
- `no_audio`, `audio_failed`, `audio_not_saved` or `no_media_paths` (stdio only): the audio or frame
  files are missing; the attached frames and the rest of the bundle are still usable.
- `provider_unconfigured` for a local file: ffmpeg or ffprobe is not installed where
  Sabor runs. Tell the user to install both; do not ask for another path.
- `unsupported_operation` for a local file: the video is over 100 MB or 10 minutes, or
  its duration cannot be read. Explain these limits; do not ask for another path.

## Never

- Never send the user's media to any other service to extract the recipe.
- Never save a recipe the user has not approved, and never overwrite one: if the name
  already exists, ask for another name.
