FROM node:26-slim
RUN npm install -g pnpm@12.14.0
WORKDIR /web
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
CMD ["pnpm", "exec", "vite", "--host", "0.0.0.0", "--port", "5173"]
