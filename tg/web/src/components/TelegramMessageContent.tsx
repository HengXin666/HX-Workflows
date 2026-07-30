import { useState } from "react";
import { Download, FileText, ImageOff, Play, Video } from "lucide-react";
import { apiAssetUrl, type TelegramMessage } from "../lib/telegramMessages";

function formatBytes(value: number) {
  if (!value) return "";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(value: number) {
  if (!value) return "";
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function TelegramMessageContent({ message, compact = false }: {
  message: TelegramMessage;
  compact?: boolean;
}) {
  const [playVideo, setPlayVideo] = useState(false);
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const media = message.media;
  const thumbnail = apiAssetUrl(media?.thumbnail_url);
  const content = apiAssetUrl(media?.content_url);
  const mediaStyle = media?.width && media.height
    ? { aspectRatio: `${media.width} / ${media.height}` }
    : { aspectRatio: "4 / 3" };

  return (
    <div className={compact ? "telegram-message-content compact" : "telegram-message-content"}>
      {message.text ? <p>{message.text}</p> : null}
      {media?.kind === "image" ? (
        <a className="telegram-image" href={content} target="_blank" rel="noreferrer" title="查看原图" style={mediaStyle}>
          {thumbnail && !thumbnailFailed ? (
            <img
              src={thumbnail}
              alt={message.text || media.file_name || "Telegram 图片"}
              loading="lazy"
              decoding="async"
              fetchPriority="low"
              onError={() => setThumbnailFailed(true)}
            />
          ) : (
            <span className="telegram-media-fallback"><ImageOff aria-hidden="true" />图片预览不可用</span>
          )}
        </a>
      ) : null}
      {media?.kind === "video" ? (
        <div className="telegram-video" style={mediaStyle}>
          {playVideo ? (
            <video controls autoPlay preload="metadata" poster={thumbnail || undefined} src={content} />
          ) : (
            <button className="telegram-video-poster" onClick={() => setPlayVideo(true)} title="加载并播放视频">
              {thumbnail && !thumbnailFailed ? (
                <img
                  src={thumbnail}
                  alt={message.text || media.file_name || "Telegram 视频封面"}
                  loading="lazy"
                  decoding="async"
                  fetchPriority="low"
                  onError={() => setThumbnailFailed(true)}
                />
              ) : (
                <span className="telegram-media-fallback"><Video aria-hidden="true" />视频封面不可用</span>
              )}
              <span className="telegram-play"><Play aria-hidden="true" /></span>
              {media.duration ? <small>{formatDuration(media.duration)}</small> : null}
            </button>
          )}
        </div>
      ) : null}
      {media?.kind === "file" ? (
        <a className="telegram-file" href={content} target="_blank" rel="noreferrer">
          <FileText aria-hidden="true" />
          <span><strong>{media.file_name || "Telegram 文件"}</strong><small>{formatBytes(media.size)}</small></span>
          <Download aria-hidden="true" />
        </a>
      ) : null}
    </div>
  );
}
