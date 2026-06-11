import { useState } from 'react'
import axios from 'axios'
import { Search, X, Music, Loader2 } from 'lucide-react'

const API_BASE_URL = 'http://localhost:8000/api'

const CoverArt = ({ url, sizeClass = "w-10 h-10", iconSize = 16 }) => {
  const [errored, setErrored] = useState(false);

  if (url && !errored) {
    return (
      <img
        src={url}
        alt="cover art"
        className={`${sizeClass} rounded-md object-cover shadow-md`}
        onError={() => setErrored(true)}
      />
    );
  }

  return (
    <div className={`${sizeClass} bg-gray-800 rounded-md flex items-center justify-center shadow-md`}>
      <Music size={iconSize} className="text-gray-500" />
    </div>
  );
};

function App() {
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [selectedTracks, setSelectedTracks] = useState([])
  const [isSearching, setIsSearching] = useState(false)
  const [recommendations, setRecommendations] = useState([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [recArtMap, setRecArtMap] = useState({})

  // Update query state only
  const handleQueryChange = (e) => {
    const value = e.target.value
    setQuery(value)
    if (!value) setSearchResults([])
  }

  // Fetch search results from backend
  const handleSearch = async () => {
    if (!query.trim()) return

    setIsSearching(true)
    try {
      const response = await axios.get(`${API_BASE_URL}/search?query=${query}`)
      setSearchResults(response.data.results)
    } catch (error) {
      console.error("Error searching tracks:", error)
    } finally {
      setIsSearching(false)
    }
  }

  // Trigger search on Enter
  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch()
  }

  // Add/remove tracks
  const handleSelectTrack = (track) => {
    if (selectedTracks.length >= 4) {
      alert("You can only select up to 4 tracks!")
      return
    }
    // Prevent duplicates
    if (!selectedTracks.find(t => t.track === track.track && t.artist === track.artist)) {
      setSelectedTracks([...selectedTracks, track])
    }
    setQuery('')
    setSearchResults([])
  }

  const handleRemoveTrack = (indexToRemove) => {
    setSelectedTracks(selectedTracks.filter((_, index) => index !== indexToRemove))
  }

  // Fetch recommendations
  const handleGenerate = async () => {
    if (selectedTracks.length === 0) return
    
    setIsGenerating(true)
    setRecArtMap({})
    try {
      const payload = {
        seed_tracks: selectedTracks.map(t => ({ artist: t.artist, track: t.track })),
        limit: 20
      }
      
      const response = await axios.post(`${API_BASE_URL}/recommend`, payload)
      const recs = response.data.recommendations
      setRecommendations(recs)

      // Fetch all cover art in the background at once
      Promise.all(
        recs.map(async (rec) => {
          try {
            const res = await axios.get(`${API_BASE_URL}/cover-art`, {
              params: { artist: rec.artist, track: rec.track }
            })
            return [rec.track + '||' + rec.artist, res.data.url ?? null]
          } catch {
            return [rec.track + '||' + rec.artist, null]
          }
        })
      ).then(entries => setRecArtMap(Object.fromEntries(entries)))
        .catch(() => {})

    } catch (error) {
      console.error("Error generating recommendations:", error)
      alert("Failed to generate recommendations. Is Python backend running?")
    } finally {
      setIsGenerating(false)
    }
  }

  // Reset the app
  const handleReset = () => {
    setSelectedTracks([])
    setRecommendations([])
    setRecArtMap({})
    setQuery('')
  }

  return (
    <div className="min-h-screen p-8 max-w-4xl mx-auto font-sans">
      
      {/* Header */}
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-bold mb-4 tracking-tight">Music Recommender</h1>
        <p className="text-gray-400">Input 1 to 4 songs to generate a recommendation list.</p>
      </header>

      {/* Main Container */}
      <main className="space-y-8">
        
        {/* Search Section */}
        <div className="relative z-10">
          <div className="relative">
            <Search className="absolute left-4 top-3.5 text-gray-400" size={20} />
            <input
              type="text"
              value={query}
              onChange={handleQueryChange}
              onKeyDown={handleKeyDown}
              placeholder="Search for a song or artist..."
              className="w-full bg-gray-900 border border-gray-800 rounded-xl py-3 pl-12 pr-4 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
            />
            {isSearching && (
              <Loader2 className="absolute right-4 top-3.5 text-gray-400 animate-spin" size={20} />
            )}
          </div>

          {/* Autocomplete Dropdown */}
          {searchResults.length > 0 && (
            <div className="absolute w-full mt-2 bg-gray-900 border border-gray-800 rounded-xl shadow-2xl overflow-hidden">
              {searchResults.map((result, idx) => (
                <div 
                  key={idx}
                  onClick={() => handleSelectTrack(result)}
                  className="flex items-center gap-4 p-3 hover:bg-gray-800 cursor-pointer transition-colors"
                >

                  <CoverArt url={result.image} sizeClass="w-10 h-10" iconSize={16} />

                  <div>
                    <p className="font-medium text-white">{result.track}</p>
                    <p className="text-sm text-gray-400">{result.artist}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Selected Tracks UI */}
        {selectedTracks.length > 0 && (
          <div className="bg-gray-900/50 p-6 rounded-2xl border border-gray-800">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
              Tracks ({selectedTracks.length}/4)
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {selectedTracks.map((track, idx) => (
                <div key={idx} className="flex items-center justify-between bg-gray-800 p-3 rounded-xl">
                  <div className="flex items-center gap-3 truncate pr-4">
                    
                    <CoverArt url={track.image} sizeClass="w-10 h-10" iconSize={16} />

                    <div className="truncate">
                      <p className="font-medium text-white truncate">{track.track}</p>
                      <p className="text-sm text-gray-400 truncate">{track.artist}</p>
                    </div>
                  </div>
                  <button 
                    onClick={() => handleRemoveTrack(idx)}
                    className="p-2 hover:bg-gray-700 rounded-full text-gray-400 hover:text-white transition-colors"
                  >
                    <X size={18} />
                  </button>
                </div>
              ))}
            </div>
            
            {/* Generate Button */}
            {!recommendations.length > 0 && (
              <div className="mt-6 flex justify-end">
                <button 
                  onClick={handleGenerate}
                  disabled={isGenerating}
                  className="bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium py-2.5 px-6 rounded-lg transition-colors shadow-lg shadow-blue-900/20 flex items-center gap-2"
                >
                  {isGenerating ? (
                    <>
                      <Loader2 className="animate-spin" size={18} />
                      Loading...
                    </>
                  ) : (
                    "Generate Recommendations"
                  )}
                </button>
              </div>
            )}
          </div>
        )}
        
        {/* Results Section */}
        {recommendations.length > 0 && (
          <div className="mt-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex justify-between items-end mb-6">
              <div>
                <h2 className="text-2xl font-bold text-white">Recommendations</h2>
                <p className="text-gray-400 text-sm mt-1">Sorted by relatability score</p>
              </div>
              <button 
                onClick={handleReset}
                className="text-sm text-gray-400 hover:text-white transition-colors"
              >
                Start Over
              </button>
            </div>

            <div className="space-y-4">
              {recommendations.map((rec, idx) => (
                <div key={idx} className="bg-gray-900 border border-gray-800 p-4 rounded-xl flex flex-col sm:flex-row gap-4 items-start sm:items-center hover:bg-gray-800/50 transition-colors">
                  
                  {/* Rank & Album Art */}
                  <div className="flex items-center gap-4 w-full sm:w-auto">
                    <span className="text-gray-500 font-mono text-lg w-6 text-center">
                      #{idx + 1}
                    </span>
                    
                    <CoverArt url={recArtMap[rec.track + '||' + rec.artist] ?? null} sizeClass="w-16 h-16" iconSize={24} />

                  </div>

                  {/* Track Info */}
                  <div className="flex-1 min-w-0">
                    <h3 className="text-lg font-bold text-white truncate">{rec.track}</h3>
                    <p className="text-gray-400 truncate">{rec.artist}</p>
                    
                    {/* Match Reasons */}
                    <div className="flex flex-wrap gap-2 mt-2">
                      {rec.matched_because_of.map((match, mIdx) => (
                        <span key={mIdx} className="text-xs px-2 py-1 bg-gray-800 text-gray-300 rounded-md border border-gray-700">
                          Matches: {match}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Score */}
                  <div className="flex flex-col items-end shrink-0 bg-gray-950 px-4 py-2 rounded-lg border border-gray-800">
                    <span className="text-xs text-gray-500 uppercase font-semibold tracking-wider">Score</span>
                    <span className="text-2xl font-bold text-blue-400">{rec.relatability_score}</span>
                  </div>

                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default App
