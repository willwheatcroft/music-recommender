import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import httpx
from dotenv import load_dotenv
from collections import defaultdict

# Load environment variables
load_dotenv()
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_BASE_URL = "http://ws.audioscrobbler.com/2.0/"

app = FastAPI(title="Music Recommender API")

# Allow frontend to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Models for Input Validation
class TrackInput(BaseModel):
    artist: str
    track: str

class RecommendRequest(BaseModel):
    seed_tracks: List[TrackInput]
    limit: int = 20

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

# Search for Tracks
@app.get("/api/search")
async def search_track(query: str):
    async with httpx.AsyncClient() as client:
        data = await fetch_lastfm(client, "track.search", {"track": query, "limit": 10})
        
        try:
            tracks = data["results"]["trackmatches"]["track"]
            clean_results = [
                {
                    "track": t["name"],
                    "artist": t["artist"],
                } for t in tracks
            ]
            return {"results": clean_results}
        except KeyError:
            return {"results": []}

# Get Recommendations & Calculate Score
@app.post("/api/recommend")
async def get_recommendations(req: RecommendRequest):
    if not req.seed_tracks or len(req.seed_tracks) > 4:
        raise HTTPException(status_code=400, detail="Provide between 1 and 4 seed tracks.")

    recommendation_scores = defaultdict(lambda: {"score": 0, "artist": "", "matched_seeds": []})
    
    async with httpx.AsyncClient() as client:
        for seed in req.seed_tracks:
            data = await fetch_lastfm(client, "track.getsimilar", {"artist": seed.artist, "track": seed.track, "limit": 30})
            
            similar_tracks = data.get("similartracks", {}).get("track", [])
            
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
                return {"url": large_url}
            
            return {"url": None}
        except Exception as e:
            print(f"iTunes API Error: {e}")
            return {"url": None}