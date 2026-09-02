# Lilly AI Automations

This directory stores named automations created by the "Follow & Learn" skill.

## File format

Each automation is a single JSON file named after the automation slug.

Example: `deploy_app.json`

```json
{
  "name": "deploy_app",
  "description": "Build and deploy the Lilly AI app",
  "steps": [
    {"type": "shell_command", "content": "docker-compose build"},
    {"type": "shell_command", "content": "docker-compose up -d"},
    {"type": "delay", "content": "5"},
    {"type": "api_call", "content": "GET http://localhost:8098/api/health"}
  ],
  "created_at": 1724451123.45,
  "last_run": null,
  "run_count": 0
}
```

## Step types

| Type | Description | Example content |
|------|-------------|-----------------|
| `shell_command` | Run a shell command | `npm install` |
| `api_call` | HTTP request | `GET https://api.example.com/health` |
| `figranium_task` | Trigger Figranium browser task | `task_17051234` |
| `browser_action` | Scrape a URL | `https://example.com` |
| `delay` | Wait N seconds | `3` |
| `condition` | Conditional check | `if {status} == active` |

## Variable substitution

Use `{variable_name}` in any step content. Variables are passed at runtime:

```json
{
  "name": "deploy_to_env",
  "steps": [
    {"type": "shell_command", "content": "deploy.sh {environment} {version}"}
  ]
}
```

Run with:
```bash
curl -X POST http://localhost:8098/api/automation/run \
  -H "Content-Type: application/json" \
  -d '{"name": "deploy_to_env", "variables": {"environment": "prod", "version": "1.2.3"}}'
```

## Creating automations

### Via voice/chat
- "follow me and learn deploy steps"
- "record this as my morning routine"
- "create automation called backup_db"

### Via API
```bash
curl -X POST http://localhost:8098/api/automation/learn \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_routine",
    "description": "My morning checks",
    "steps": [
      {"type": "shell_command", "content": "date"},
      {"type": "api_call", "content": "GET http://localhost:8098/api/health"}
    ]
  }'
```

## Running automations

```bash
curl -X POST http://localhost:8098/api/automation/run \
  -H "Content-Type: application/json" \
  -d '{"name": "my_routine"}'
```

## Listing automations

```bash
curl http://localhost:8098/api/automation/list
```

## Deleting automations

```bash
curl -X DELETE http://localhost:8098/api/automation/delete \
  -H "Content-Type: application/json" \
  -d '{"name": "my_routine"}'
```
