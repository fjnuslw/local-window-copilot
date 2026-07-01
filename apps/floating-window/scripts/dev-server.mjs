import { createServer } from "node:http";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";

const port = Number(process.env.PORT ?? 4173);
const repoRoot = resolve(import.meta.dirname, "../../..");
const appIndex = join(repoRoot, "apps/floating-window/index.html");

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml"
};

function resolveRequestPath(url, res) {
  const parsed = new URL(url, `http://localhost:${port}`);
  const pathname = decodeURIComponent(parsed.pathname);

  if (pathname === "/" || pathname === "/floating") {
    res.writeHead(302, { Location: "/apps/floating-window/index.html" });
    res.end();
    return "redirected";
  }

  if (pathname === "/apps/floating-window/") {
    return appIndex;
  }

  const resolved = normalize(join(repoRoot, pathname));
  if (!resolved.startsWith(repoRoot)) {
    return null;
  }
  return resolved;
}

const server = createServer(async (req, res) => {
  const filePath = resolveRequestPath(req.url ?? "/", res);
  if (filePath === "redirected") {
    return;
  }

  if (!filePath) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  try {
    const fileStat = await stat(filePath);
    if (!fileStat.isFile()) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }

    const contentType = mimeTypes[extname(filePath)] ?? "application/octet-stream";
    res.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": "no-store"
    });
    createReadStream(filePath).pipe(res);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`floating-window dev server: http://127.0.0.1:${port}/floating`);
  console.log(`canonical app url: http://127.0.0.1:${port}/apps/floating-window/index.html`);
});
