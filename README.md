# Music recommender
-----
Full-stack web app that searches tracks via [Last.fm](https://www.last.fm/), lets you pick up to four  songs, and suggests similar tracks using a scored blend of Last.fm’s “similar” results across your selections.

**Stack:** FastAPI, Uvicorn, React (Vite), Tailwind CSS v4

## Features
-----
- **Search** - Autocomplete track search (minimum three characters) backed by Last.fm 'track.search'.
- **Seed picks** - Choose up to 4 tracks
- **Recommendations** - 'track.getsimilar' per seed, merged and ranked with a bonus when multiple seeds agree on a track.