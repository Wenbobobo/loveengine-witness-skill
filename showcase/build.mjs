import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";

const output = new URL("./dist/", import.meta.url);

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(new URL("./index.html", import.meta.url), new URL("./index.html", output));
await cp(new URL("./assets/", import.meta.url), new URL("./assets/", output), {
  recursive: true,
});
await cp(new URL("./.openai/", import.meta.url), new URL("./.openai/", output), {
  recursive: true,
});

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const operatorImage = await readFile(
  new URL("./assets/operator-console.png", import.meta.url),
);
const viewerImage = await readFile(
  new URL("./assets/read-only-dashboard.png", import.meta.url),
);
const worker = `const html = ${JSON.stringify(html)};
const assets = {
  "/assets/operator-console.png": ${JSON.stringify(operatorImage.toString("base64"))},
  "/assets/read-only-dashboard.png": ${JSON.stringify(viewerImage.toString("base64"))},
};

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

export default {
  async fetch(request) {
    const { pathname } = new URL(request.url);
    if (pathname === "/" || pathname === "/index.html") {
      return new Response(html, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    const asset = assets[pathname];
    if (asset) {
      return new Response(decodeBase64(asset), {
        headers: { "content-type": "image/png", "cache-control": "public, max-age=3600" },
      });
    }
    return new Response("Not Found", { status: 404 });
  },
};
`;
await mkdir(new URL("./server/", output), { recursive: true });
await writeFile(new URL("./server/index.js", output), worker, "utf8");

console.log("Built static site in showcase/dist");
