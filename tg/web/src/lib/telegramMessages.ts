import { API } from "./api";

export type TelegramMessageMedia = {
  kind: "image" | "video" | "file";
  mime_type: string;
  file_name: string;
  size: number;
  width: number;
  height: number;
  duration: number;
  has_thumbnail: boolean;
  media_id?: string;
  thumbnail_url?: string;
  content_url: string;
};

export type TelegramMessage = {
  id: string;
  date: string;
  out: boolean;
  sender: string;
  text: string;
  media?: TelegramMessageMedia | null;
};

export function apiAssetUrl(path?: string | null): string {
  const value = String(path || "").trim();
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  return `${API}${value.startsWith("/") ? value : `/${value}`}`;
}
