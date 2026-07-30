export type ResourcePreviewMedia = {
  message_id?: number;
  kind?: "photo" | "video" | "media";
  url?: string;
};

export type ResourcePreviewOccurrence = {
  source_peer?: string;
  source_message_id?: number | null;
  message_link?: string;
  preview_message_ids?: number[];
  preview_message_links?: string[];
  preview_media?: ResourcePreviewMedia[];
  account_index?: number;
  account_id?: string;
  round?: number;
  seen_at?: string;
};

export type ResourcePreview = ResourcePreviewOccurrence & {
  resource_key: string;
  callback_data?: string;
  button_text?: string;
  description?: string;
  summary_text?: string;
  photos?: number;
  videos?: number;
  files?: number;
  unlock_cost?: number;
  seen_count?: number;
  is_new?: boolean;
  created_at?: string;
  last_seen_at?: string;
  provenance?: ResourcePreviewOccurrence[];
};

export function safeResourceHref(value?: string | null): string | null {
  const candidate = String(value || "").trim();
  if (!candidate) return null;
  try {
    const url = new URL(candidate);
    return ["http:", "https:", "tg:"].includes(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

export function telegramConversationHref(sourcePeer?: string): string | null {
  const peer = String(sourcePeer || "").trim();
  const username = peer.startsWith("@") ? peer.slice(1) : peer;
  if (!/^[A-Za-z0-9_]{5,}$/.test(username)) return null;
  return `https://t.me/${username}`;
}

export function messageHref(occurrence: ResourcePreviewOccurrence): string | null {
  const explicit = safeResourceHref(occurrence.message_link);
  if (explicit) return explicit;
  const conversation = telegramConversationHref(occurrence.source_peer);
  return conversation && occurrence.source_message_id
    ? `${conversation}/${occurrence.source_message_id}`
    : conversation;
}

export function previewMessageHref(occurrence: ResourcePreviewOccurrence, index: number): string | null {
  const mediaHref = safeResourceHref(occurrence.preview_media?.[index]?.url);
  if (mediaHref) return mediaHref;
  const explicit = safeResourceHref(occurrence.preview_message_links?.[index]);
  if (explicit) return explicit;
  const messageId = occurrence.preview_message_ids?.[index];
  if (!messageId) return null;
  const source = safeResourceHref(occurrence.message_link);
  if (source?.startsWith("tg:")) {
    const url = new URL(source);
    url.searchParams.set("message_id", String(messageId));
    return url.toString();
  }
  const conversation = telegramConversationHref(occurrence.source_peer);
  return conversation ? `${conversation}/${messageId}` : null;
}

export function occurrenceMediaLinks(occurrence: ResourcePreviewOccurrence): ResourcePreviewMedia[] {
  if (occurrence.preview_media?.length) {
    return occurrence.preview_media.map((item) => ({
      message_id: item.message_id,
      kind: item.kind === "photo" || item.kind === "video" ? item.kind : "media",
      url: safeResourceHref(item.url) || undefined,
    }));
  }
  const count = Math.max(
    occurrence.preview_message_links?.length || 0,
    occurrence.preview_message_ids?.length || 0,
  );
  return Array.from({ length: count }, (_, index) => ({
    message_id: occurrence.preview_message_ids?.[index],
    kind: "media" as const,
    url: previewMessageHref(occurrence, index) || undefined,
  }));
}

export function resourcePreviewOccurrences(preview: ResourcePreview): ResourcePreviewOccurrence[] {
  const candidates = preview.provenance?.length ? preview.provenance : [preview];
  const seen = new Set<string>();
  return candidates.filter((item) => {
    const identity = [
      item.account_id || item.account_index || "",
      item.source_peer || "",
      item.source_message_id || "",
      item.round || "",
      ...(item.preview_message_links || []),
      ...(item.preview_media || []).map((media) => `${media.kind || "media"}:${media.message_id || ""}:${media.url || ""}`),
    ].join("|");
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function resourcePreviewMatches(preview: ResourcePreview, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  const occurrenceValues = resourcePreviewOccurrences(preview).flatMap((item) => [
    item.source_peer,
    item.account_id,
    item.source_message_id,
    item.round,
  ]);
  return [
    preview.resource_key,
    preview.description,
    preview.summary_text,
    preview.source_peer,
    preview.account_id,
    preview.button_text,
    ...occurrenceValues,
  ].some((value) => String(value || "").toLowerCase().includes(normalized));
}

export function linkWindowTarget(href: string): "_blank" | undefined {
  return href.startsWith("tg:") ? undefined : "_blank";
}
