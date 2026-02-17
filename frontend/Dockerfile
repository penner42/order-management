FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
EXPOSE 5173

# Vite dev server; proxy /api to backend (set in docker-compose)
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
