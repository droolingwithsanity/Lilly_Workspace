import os
import shutil

# Ensure we are starting fresh
base_dir = "/home/labhrasd/LillyMerg/frontend"
if os.path.islink(base_dir):
    os.unlink(base_dir)

# Create physical directories
os.makedirs(f"{base_dir}/src", exist_ok=True)
os.makedirs(f"{base_dir}/public", exist_ok=True)

# 1. The Nginx Config (Set to 3001)
nginx_content = """
server {
    listen 3001;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
"""

# 2. A Basic index.html for Vite
html_content = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Lilly OS - Warroom</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

with open(f"{base_dir}/nginx.conf", "w") as f: f.write(nginx_content.strip())
with open(f"{base_dir}/index.html", "w") as f: f.write(html_content.strip())

print(f"✅ Physical directory created at {base_dir}. Loop broken.")
