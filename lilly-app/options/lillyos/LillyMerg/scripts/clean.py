import json
import os

PKG_PATH = "/home/labhrasd/LillyMerg/lar_frontend/package.json"

def sanitize_dna():
    if not os.path.exists(PKG_PATH):
        print("❌ package.json not found!")
        return

    with open(PKG_PATH, 'r') as f:
        data = json.load(f)

    # The "Safe List" - Common libraries for the Lilly UI
    safe_deps = {
        "react": "latest",
        "react-dom": "latest",
        "framer-motion": "latest",
        "lucide-react": "latest",
        "axios": "latest",
        "vite": "latest",
        "@vitejs/plugin-react": "latest",
        "tailwindcss": "latest",
        "postcss": "latest",
        "autoprefixer": "latest"
    }

    # Update the data while keeping any unique SPAWN/GENESIS logic
    data["dependencies"] = {**data.get("dependencies", {}), **safe_deps}
    
    # Ensure scripts are correct for the Phoenix
    data["scripts"] = {
        "dev": "vite --host --port 3000",
        "build": "vite build",
        "preview": "vite preview"
    }

    with open(PKG_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    print("✅ DNA Sanitized. Compatible versions set to 'latest'.")

if __name__ == "__main__":
    sanitize_dna()
