import asyncio
import math
import os
import json
import re
import unicodedata
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, List
import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv
from collections import defaultdict

# Load environment variables
load_dotenv()
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_BASE_URL = "http://ws.audioscrobbler.com/2.0/"

# Redis setup
CACHE_TTL_SIMILAR   = 60 * 60 * 6   # Similar tracks:  6 hours 
CACHE_TTL_COVER_ART = 60 * 60 * 24  # Cover art URLs:  24 hours
 
redis_client = aioredis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

app = FastAPI(title="Music Recommender API")

# Allow frontend to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown():
    await redis_client.aclose()

# Pydantic Models for Input Validation
class TrackInput(BaseModel):
    artist: str
    track: str

class RecommendRequest(BaseModel):
    seed_tracks: List[TrackInput]
    limit: int = 20

# Cache get/set
async def cache_get(key: str) -> dict | None:
    """
    Try to read a value from Redis.
    Returns parsed dict if found, None if not found or Redis is down.
    Failures are silently ignored so the app keeps working without Redis.
    """
    try:
        raw = await redis_client.get(key)
        if raw:
            return json.loads(raw)
    except Exception as e:
        print(f"[Cache] GET failed (Redis down?): {e}")
    return None
 
async def cache_set(key: str, data: dict, ttl: int) -> None:
    """
    Write a value to Redis with an expiry time (ttl = seconds).
    Failures are silently ignored.
    """
    try:
        await redis_client.setex(key, ttl, json.dumps(data))
    except Exception as e:
        print(f"[Cache] SET failed (Redis down?): {e}")

# Helper Function for External API Calls
async def fetch_lastfm(client: httpx.AsyncClient, method: str, params: dict):
    params.update({
        "method": method,
        "api_key": LASTFM_API_KEY,
        "format": "json"
    })
    response = await client.get(LASTFM_BASE_URL, params=params)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Last.fm API error")
    return response.json()

# Cached track.getsimilar
async def get_similar_tracks(client: httpx.AsyncClient, artist: str, track: str) -> list:
    """
    Returns the list of similar tracks for a given seed.
    Checks Redis first; only calls Last.fm if the result isn't cached.
 
    Cache key format:  similar::<artist>::<track>
    Example:           similar::radiohead::creep
    """
    cache_key = f"similar::{artist.lower()}::{track.lower()}"
 
    cached = await cache_get(cache_key)
    if cached is not None:
        print(f"[Cache] HIT  - {artist} / {track}")
        return cached 
 
    print(f"[Cache] MISS - {artist} / {track}  ->  calling Last.fm")
    data = await fetch_lastfm(client, "track.getsimilar", {
        "artist": artist,
        "track": track,
        "limit": 30
    })
    similar = data.get("similartracks", {}).get("track", [])
 
    await cache_set(cache_key, similar, CACHE_TTL_SIMILAR)
    return similar

# Search relevance tuning knobs
MAX_SEARCH_RESULTS = 10
EXACT_TRACK_BOOST = 120
TRACK_PREFIX_BOOST = 45
FULL_TEXT_CONTAINS_BOOST = 25
TOKEN_OVERLAP_MULTIPLIER = 10
ARTIST_HINT_BOOST = 35
TRACK_HINT_BOOST = 35
LASTFM_RANK_DECAY = 1.5
GETINFO_CONFIDENCE_BOOST = 200


def listeners_score(listeners_str: str) -> float:
    """
    Log-normalise a Last.fm listeners count into a comparable score bonus.
    10M listeners = ~56 pts  |  1M = ~48  |  100K = ~40  |  1K = ~24
    """
    try:
        return math.log10(int(listeners_str) + 1) * 8
    except (ValueError, TypeError):
        return 0.0


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def tokenize(value: str) -> list[str]:
    return [token for token in normalize_text(value).split(" ") if token]


def parse_query_hints(query: str) -> tuple[str | None, str | None]:
    lowered = query.strip()
    if not lowered:
        return None, None

    if " - " in lowered:
        artist_hint, track_hint = lowered.split(" - ", 1)
        return normalize_text(artist_hint), normalize_text(track_hint)

    by_match = re.match(r"(.+)\s+by\s+(.+)", lowered, flags=re.IGNORECASE)
    if by_match:
        track_hint = normalize_text(by_match.group(1))
        artist_hint = normalize_text(by_match.group(2))
        return artist_hint, track_hint

    return None, None


def parse_raw_hints(query: str) -> tuple[str | None, str | None]:
    """
    Like parse_query_hints but returns (artist, track) with original casing and
    punctuation intact. This is used for direct Last.fm API calls where normalisation
    would mangle names like 'AC/DC' or 'Sigur Rós'.
    """
    q = query.strip()
    if " - " in q:
        artist_part, track_part = q.split(" - ", 1)
        return artist_part.strip(), track_part.strip()
    by_match = re.match(r"(.+)\s+by\s+(.+)", q, flags=re.IGNORECASE)
    if by_match:
        return by_match.group(2).strip(), by_match.group(1).strip()
    return None, None


def score_candidate(
    query: str,
    track_name: str,
    artist_name: str,
    index: int,
    artist_hint: str | None,
    track_hint: str | None,
    listeners: str = "0",
) -> int:
    normalized_query = normalize_text(query)
    normalized_track = normalize_text(track_name)
    normalized_artist = normalize_text(artist_name)
    combined_text = f"{normalized_track} {normalized_artist}".strip()

    score = 0

    if normalized_track == normalized_query:
        score += EXACT_TRACK_BOOST

    if normalized_query and normalized_track.startswith(normalized_query):
        score += TRACK_PREFIX_BOOST

    if normalized_query and normalized_query in combined_text:
        score += FULL_TEXT_CONTAINS_BOOST

    query_tokens = set(tokenize(query))
    candidate_tokens = set(tokenize(f"{track_name} {artist_name}"))
    if query_tokens:
        overlap = query_tokens.intersection(candidate_tokens)
        score += len(overlap) * TOKEN_OVERLAP_MULTIPLIER

    if artist_hint and artist_hint in normalized_artist:
        score += ARTIST_HINT_BOOST
    if track_hint and track_hint in normalized_track:
        score += TRACK_HINT_BOOST

    # Popularity: log-normalised listener count from Last.fm
    score += int(listeners_score(listeners))

    # Bias
    score -= int(index * LASTFM_RANK_DECAY)
    return score


def extract_image(track_entry: dict[str, Any]) -> str | None:
    images = track_entry.get("image", [])
    if isinstance(images, list):
        for image in reversed(images):
            if isinstance(image, dict) and image.get("#text"):
                return image["#text"]
    return None


# Search for Tracks
@app.get("/api/search")
async def search_track(query: str):
    query = query.strip()
    if not query:
        return {"results": []}

    artist_hint, track_hint = parse_query_hints(query)
    raw_artist, raw_track   = parse_raw_hints(query)

    async with httpx.AsyncClient() as client:
        tasks: list = [
            fetch_lastfm(client, "track.search", {"track": query, "limit": 30})
        ]

        if raw_artist and raw_track:
            tasks.append(
                fetch_lastfm(client, "track.getInfo", {
                    "artist": raw_artist,
                    "track":  raw_track,
                })
            )

        gathered = await asyncio.gather(*tasks, return_exceptions=True)

    search_data  = gathered[0] if not isinstance(gathered[0], Exception) else {}
    getinfo_data = (
        gathered[1]
        if len(gathered) > 1 and not isinstance(gathered[1], Exception)
        else None
    )

    tracks = search_data.get("results", {}).get("trackmatches", {}).get("track", [])
    if isinstance(tracks, dict):
        tracks = [tracks]
    if not isinstance(tracks, list):
        tracks = []

    deduped: dict[tuple[str, str], dict[str, Any]] = {}

    if getinfo_data and "track" in getinfo_data:
        info        = getinfo_data["track"]
        track_name  = str(info.get("name", "")).strip()
        artist_obj  = info.get("artist", {})
        artist_name = str(
            artist_obj.get("name", "") if isinstance(artist_obj, dict) else artist_obj
        ).strip()

        if track_name and artist_name:
            dedupe_key = (normalize_text(track_name), normalize_text(artist_name))
            deduped[dedupe_key] = {
                "track":          track_name,
                "artist":         artist_name,
                "image":          extract_image(info),
                "score":          GETINFO_CONFIDENCE_BOOST + int(listeners_score(info.get("listeners", "0"))),
                "original_index": -1,
            }
            print(f"[Search] track.getInfo hit - {artist_name} / {track_name}")

    for index, track_entry in enumerate(tracks):
        if not isinstance(track_entry, dict):
            continue

        track_name  = str(track_entry.get("name", "")).strip()
        artist_name = str(track_entry.get("artist", "")).strip()
        if not track_name or not artist_name:
            continue

        candidate = {
            "track":          track_name,
            "artist":         artist_name,
            "image":          extract_image(track_entry),
            "score":          score_candidate(
                query=query,
                track_name=track_name,
                artist_name=artist_name,
                index=index,
                artist_hint=artist_hint,
                track_hint=track_hint,
                listeners=track_entry.get("listeners", "0"),
            ),
            "original_index": index,
        }

        dedupe_key = (normalize_text(track_name), normalize_text(artist_name))
        existing   = deduped.get(dedupe_key)
        if not existing or (
            candidate["score"], -candidate["original_index"]
        ) > (existing["score"], -existing["original_index"]):
            deduped[dedupe_key] = candidate

    ranked_results = sorted(
        deduped.values(),
        key=lambda c: (c["score"], -c["original_index"]),
        reverse=True,
    )

    clean_results = [
        {
            "track":  c["track"],
            "artist": c["artist"],
            "image":  c["image"],
        }
        for c in ranked_results[:MAX_SEARCH_RESULTS]
    ]
    return {"results": clean_results}

# Get Recommendations & Calculate Score
@app.post("/api/recommend")
async def get_recommendations(req: RecommendRequest):
    if not req.seed_tracks or len(req.seed_tracks) > 4:
        raise HTTPException(status_code=400, detail="Provide between 1 and 4 seed tracks.")

    recommendation_scores = defaultdict(lambda: {"score": 0, "artist": "", "matched_seeds": []})
    
    async with httpx.AsyncClient() as client:
        for seed in req.seed_tracks:
            
            similar_tracks = await get_similar_tracks(client, seed.artist, seed.track)
            
            for index, sim_track in enumerate(similar_tracks):
                name = sim_track["name"]
                artist = sim_track["artist"]["name"]
                key = f"{name}||{artist}"
                
                points = 30 - index 
                
                recommendation_scores[key]["score"] += points
                recommendation_scores[key]["artist"] = artist
                recommendation_scores[key]["track"] = name
                recommendation_scores[key]["matched_seeds"].append(seed.track)

    formatted_results = []
    for key, data in recommendation_scores.items():
        if any(seed.track.lower() == data["track"].lower() for seed in req.seed_tracks):
            continue
            
        match_count = len(data["matched_seeds"])
        if match_count > 1:
            data["score"] = int(data["score"] * (1.5 * match_count))
            
        formatted_results.append({
            "track": data["track"],
            "artist": data["artist"],
            "relatability_score": data["score"],
            "matched_because_of": data["matched_seeds"]
        })

    formatted_results.sort(key=lambda x: x["relatability_score"], reverse=True)
    
    return {"recommendations": formatted_results[:req.limit]}

# Get Cover Art Using Itunes
@app.get("/api/cover-art")
async def get_itunes_cover(artist: str, track: str):
    """
    Cover art URLs are permanent so safe to cache for 24 hours.
    Cache key format:  coverart::<artist>::<track>
    """
    cache_key = f"coverart::{artist.lower()}::{track.lower()}"
 
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached 
 
    async with httpx.AsyncClient() as client:
        try:
            search_term = f"{artist} {track}"
            params = {
                "term": search_term,
                "entity": "song",
                "limit": 1
            }
            
            response = await client.get("https://itunes.apple.com/search", params=params)
            data = response.json()
            
            if data["resultCount"] > 0:
                thumb_url = data["results"][0]["artworkUrl100"]
                large_url = thumb_url.replace("100x100bb.jpg", "600x600bb.jpg")
                result = {"url": large_url}
            else:
                result = {"url": None}
            
            await cache_set(cache_key, result, CACHE_TTL_COVER_ART)
            return result
 
        except Exception as e:
            print(f"iTunes API Error: {e}")
            return {"url": None}