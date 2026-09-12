# /home/labhrasd/LillyMerg/frontend/Dockerfile
FROM node:20-slim as build
WORKDIR /app
COPY package.json ./
RUN npm install  --legacy-peer-deps
COPY . .
ENV VITE_API_URL=""
RUN npm run build && mkdir -p public/screenshots && node gen_screenshots.cjs

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=build /app/public/screenshots /usr/share/nginx/html/screenshots
RUN sed -i 's/ crossorigin//g' /usr/share/nginx/html/index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
