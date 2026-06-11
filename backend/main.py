import os
import json
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
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
CACHE_TTL_SEARCH    = 60 * 5        # Search results:  5 minutes
 
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

MAX_SEARCH_RESULTS = 10

# Recommendation scoring
SEED_RANK_POINTS = 30   # Points awarded to the #1 similar track; decreases by 1 per rank
CROSS_SEED_BONUS = 15   # Flat bonus added per extra seed that also recommends this track


def sanitise_artist(name: str) -> str:
    """Strip Last.fm metadata noise appended to artist names (e.g. '• Recommended for you')."""
    return re.split(r"\s*[•·]\s*", name)[0].strip()


# Search for Tracks
@app.get("/api/search")
async def search_track(query: str):
    query = query.strip()
    if not query:
        return {"results": []}

    cache_key = f"search::{query.lower()}"
    cached = await cache_get(cache_key)
    if cached is not None:
        print(f"[Cache] HIT  - search: {query}")
        return {"results": cached}
    print(f"[Cache] MISS - search: {query}  ->  calling iTunes")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://itunes.apple.com/search",
                params={"term": query, "entity": "song", "limit": MAX_SEARCH_RESULTS}
            )
            if not response.text.strip():
                return {"results": []}
            data = response.json()
        except Exception as e:
            print(f"[Search] iTunes error: {e}")
            return {"results": []}

    results = []
    for item in data.get("results", []):
        track_name  = item.get("trackName", "").strip()
        artist_name = item.get("artistName", "").strip()
        artwork     = item.get("artworkUrl100", "") or ""
        if artwork:
            artwork = artwork.replace("100x100bb.jpg", "600x600bb.jpg")

        if not track_name or not artist_name:
            continue

        results.append({
            "track":  track_name,
            "artist": artist_name,
            "image":  artwork or None,
        })

    await cache_set(cache_key, results, CACHE_TTL_SEARCH)
    return {"results": results}

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
                artist = sanitise_artist(sim_track["artist"]["name"])
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
            data["score"] += CROSS_SEED_BONUS * (match_count - 1)
            
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
            if not response.text.strip():
                return {"url": None}
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