# Multi-Model Cascade for Ollama

Orchestrates three Qwen models to minimize token usage while maintaining quality:

- **Router (qwen2.5:1.5b)**: Fast query analysis and routing decisions
- **Synthesizer (qwen3:8b)**: Output formatting and quality polishing
- **Heavy Lifter (qwen3.8-27b)**: Complex reasoning tasks

## How It Works

```
Query → Router (1.5b) → Complexity Analysis
         ↓
    ┌────┴────┐
    ↓         ↓
Light       Heavy (27b)
(≤0.6)      (>0.6)
    ↓         ↓
Synthesizer (8b) → Final Output
```

### Token Savings

- **Simple queries**: Only router + synthesizer (minimal tokens)
- **Complex queries**: Full cascade but with token limits per model
- **Estimated savings**: 60-80% fewer tokens vs always using 27b model

## Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# Start the cascade service
docker-compose -f docker-compose.cascade.yml up -d

# Test it
python3 test_cascade.py
```

### Option 2: Direct Python

```bash
# Install dependencies
pip install -r requirements_cascade.txt

# Run the service
python3 model_cascade.py
```

## API Endpoints

### POST `/v1/chat/completions`
OpenAI-compatible endpoint for queries.

```bash
curl -X POST http://localhost:8099/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Explain quantum computing in simple terms",
    "force_heavy": false
  }'
```

**Response:**
```json
{
  "response": "Quantum computing uses...",
  "model_used": "qwen2.5:1.5b -> qwen3.8-27b-q4:latest -> qwen3:8b",
  "route_info": {
    "complexity": 0.7,
    "needs_heavy_reasoning": true,
    "suggested_approach": "cascade",
    "key_concepts": ["quantum computing", "qubits", "superposition"]
  },
  "stats": {
    "total_queries": 1,
    "router_calls": 1,
    "synthesizer_calls": 1,
    "heavy_calls": 1,
    "tokens_saved": 1500
  },
  "latency_ms": 4500.0
}
```

### GET `/health`
Health check endpoint.

### GET `/stats`
Get performance statistics.

### POST `/reset-stats`
Reset statistics.

## Configuration

Edit `model_cascade.py` to adjust:

```python
@dataclass
class CascadeConfig:
    ollama_url: str = "http://100.73.249.14:11434"
    router_model: str = "qwen2.5:1.5b"
    synthesizer_model: str = "qwen3:8b"
    heavy_model: str = "qwen3.8-27b-q4:latest"
    
    # Token limits
    router_max_tokens: int = 150
    synthesizer_max_tokens: int = 500
    heavy_max_tokens: int = 2000
    
    # Complexity threshold (0-1)
    complexity_threshold: float = 0.6
```

## Integration with Lilly AI

Replace direct Ollama calls in `lilly_ai.py`:

```python
# Before
response = await ollama_client.chat(query)

# After
import httpx

async def query_cascade(query: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8099/v1/chat/completions",
            json={"query": query}
        )
        return response.json()["response"]

response = await query_cascade(query)
```

## Performance Tuning

### Reduce Latency
- Lower `router_max_tokens` for faster routing
- Increase `complexity_threshold` to skip heavy model more often
- Pre-warm models on startup (already implemented)

### Improve Quality
- Increase `heavy_max_tokens` for more detailed reasoning
- Lower `complexity_threshold` to use heavy model more often
- Adjust temperature settings per model

### Monitor Stats
```bash
# Check token savings
curl http://localhost:8099/stats

# Reset stats
curl -X POST http://localhost:8099/reset-stats
```

## Troubleshooting

### "Connection refused"
- Verify Ollama is running on `100.73.249.14:11434`
- Check Tailscale connection

### "Slow responses"
- First request is slower (model loading)
- Check network latency to Ollama server
- Consider pre-warming more models

### "Poor quality responses"
- Adjust `complexity_threshold` to route more queries to heavy model
- Increase token limits for synthesizer
