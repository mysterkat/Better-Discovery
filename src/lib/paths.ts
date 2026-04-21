/** Thin file-path helpers. */

/** Extract the filename (with extension) from an absolute or relative path. */
export function basename(p: string): string {
  return p.replace(/.*[\\/]/, "");
}

/** Extract the file extension, lower-cased, including the dot (e.g. ".csv"). */
export function extname(p: string): string {
  const m = basename(p).match(/\.[^.]+$/);
  return m ? m[0].toLowerCase() : "";
}
