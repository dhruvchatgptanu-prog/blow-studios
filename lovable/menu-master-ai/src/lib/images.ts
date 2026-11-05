/** Client-side image checks shared by the chat and the form studio. */

const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_IMAGE_BYTES = 12 * 1024 * 1024;

export async function validateImage(file: File) {
  if (!ALLOWED_IMAGE_TYPES.has(file.type)) throw new Error("Upload a JPG, PNG or WebP image.");
  if (file.size > MAX_IMAGE_BYTES) throw new Error("Images must be smaller than 12 MB.");
  const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const png = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
  const jpeg = bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  const webp = String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
  if (!png && !jpeg && !webp) throw new Error("That file is not a valid JPG, PNG or WebP image.");
}

export function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => (typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("The image could not be read.")));
    reader.onerror = () => reject(new Error("The image could not be read."));
    reader.readAsDataURL(file);
  });
}

/**
 * Shrinks a photo so eight menu pages still travel in one request: longest edge
 * `maxEdge` px, JPEG unless the source is a PNG with transparency worth keeping.
 * Falls back to the original data URL when the browser cannot decode it.
 */
export async function downscaleImage(file: File, maxEdge = 1800, quality = 0.86): Promise<string> {
  const original = await fileToDataUrl(file);
  if (typeof document === "undefined") return original;
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const element = new Image();
      element.onload = () => resolve(element);
      element.onerror = () => reject(new Error("decode failed"));
      element.src = original;
    });
    const scale = Math.min(1, maxEdge / Math.max(image.naturalWidth, image.naturalHeight));
    if (scale === 1 && file.size < 1.5 * 1024 * 1024) return original;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    if (!context) return original;
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", quality);
  } catch {
    return original;
  }
}

/** Extracts image files from a paste or drop event, in the order given. */
export function imagesFromTransfer(transfer: DataTransfer | null | undefined): File[] {
  if (!transfer) return [];
  const files: File[] = [];
  for (const item of Array.from(transfer.items ?? [])) {
    if (item.kind === "file") { const file = item.getAsFile(); if (file && file.type.startsWith("image/")) files.push(file); }
  }
  if (files.length === 0) for (const file of Array.from(transfer.files ?? [])) if (file.type.startsWith("image/")) files.push(file);
  return files;
}
