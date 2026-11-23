import { copyFile, mkdir } from "node:fs/promises";

await mkdir(new URL("dist/assets/", import.meta.url), { recursive: true });
for (const [source, destination] of [
  ["index.html", "dist/index.html"],
  ["src/styles.css", "dist/assets/styles.css"],
]) {
  await copyFile(new URL(source, import.meta.url), new URL(destination, import.meta.url));
}
