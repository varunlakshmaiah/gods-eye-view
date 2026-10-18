FROM node:22-slim

WORKDIR /app

COPY package.json ./
RUN npm install

COPY tsconfig.json ./
COPY src ./src

RUN npm run build

ENV PORT=8080
EXPOSE 8080

CMD ["node", "dist/index.js"]
