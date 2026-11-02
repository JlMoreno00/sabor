---
name: sabor-import
description: Import a recipe into Sabor from a web page, pasted text, a photo or screenshot, or a video (YouTube, Vimeo, Instagram, TikTok or a file on the computer). Use when the user shares a recipe link, text, image or video and wants it saved. Sabor contains no AI model; you, the agent, read the source and save the recipe yourself.
---

# Sabor import: recipes from any source

Talk to the user in their language. Sabor never sends recipes, images, audio or video to
an AI service: it fetches or prepares the source, and **you** read it and write the recipe.
Nothing is saved until the user has seen a preview and agreed.

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

- `metadata`: title, uploader, duration, date, platform and the video's description,
  which often lists the ingredients.
- `transcript`: `{source, language, text}`. `source` is `subtitles` (the platform's) or
  `local_asr` (Sabor's optional on-device transcriber), or the transcript is `null`.
- `frames`: 8 frames, attached as images (each with its `time`, and a `path` on the
  computer when Sabor runs there).
- `audio_path`: the soundtrack as a file, only when Sabor runs on the computer; use it
  only if you can transcribe audio yourself.
- `limitations` and `next_step`: read them, then follow step 3.

Combine the description, transcript and frames. When they disagree, prefer what is said
or written in the video over what is only seen, and tell the user what was unclear.

### When the bundle is incomplete

- `no_transcript`: rely on the description and frames. If they do not give the
  quantities, ask the user. Sabor can transcribe locally when the optional
  `sabor-mcp[transcribe]` extra is installed; mention it, do not install it yourself.
- `no_frames`: ffmpeg and ffprobe are missing where Sabor runs. Continue with the
  description and transcript, and tell the user frames need ffmpeg.
- A download error (`upstream_error` from `yt_dlp`), a TikTok short link or a platform
  Sabor does not fetch: ask the user to save the video on their computer and give you its
  absolute path. Sabor reads local files only when it runs on the same computer (stdio)
  and only under `SABOR_MEDIA_ROOTS` (their home folder by default).
- `invalid_argument` for a local file: the path is not an absolute path to a video file
  under `SABOR_MEDIA_ROOTS`. Ask for the right path; do not try other folders.

## Never

- Never send the user's media to any other service to extract the recipe.
- Never save a recipe the user has not approved, and never overwrite one: if the name
  already exists, ask for another name.
