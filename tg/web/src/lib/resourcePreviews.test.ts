import { describe, expect, it } from "vitest";
import {
  linkWindowTarget,
  messageHref,
  occurrenceMediaLinks,
  previewMessageHref,
  resourcePreviewMatches,
  resourcePreviewOccurrences,
  safeResourceHref,
  telegramConversationHref,
  type ResourcePreview,
} from "./resourcePreviews";
import { apiAssetUrl } from "./telegramMessages";

const preview: ResourcePreview = {
  resource_key: "resource-a",
  description: "多批次资源",
  provenance: [
    {
      account_id: "main-1",
      source_peer: "@preview_bot",
      source_message_id: 20,
      round: 1,
      preview_message_links: ["tg://openmessage?user_id=1&message_id=18"],
    },
    {
      account_id: "alt-1",
      source_peer: "@preview_bot",
      source_message_id: 40,
      round: 2,
      preview_message_links: ["tg://openmessage?user_id=1&message_id=38"],
    },
  ],
};

describe("resource preview helpers", () => {
  it("keeps multiple preview occurrences for one resource", () => {
    expect(resourcePreviewOccurrences(preview)).toHaveLength(2);
  });

  it("searches occurrence account and source metadata", () => {
    expect(resourcePreviewMatches(preview, "alt-1")).toBe(true);
    expect(resourcePreviewMatches(preview, "preview_bot")).toBe(true);
  });

  it("builds a browser conversation fallback", () => {
    expect(telegramConversationHref("@preview_bot")).toBe("https://t.me/preview_bot");
    expect(messageHref({ source_peer: "@preview_bot", source_message_id: 52 })).toBe(
      "https://t.me/preview_bot/52",
    );
  });

  it("builds every preview link from stored message ids", () => {
    expect(previewMessageHref({
      message_link: "tg://openmessage?user_id=1&message_id=20",
      preview_message_ids: [17, 18, 19],
    }, 1)).toBe("tg://openmessage?user_id=1&message_id=18");
  });

  it("keeps typed photo and video links and supports legacy exports", () => {
    expect(occurrenceMediaLinks({
      preview_media: [
        { message_id: 17, kind: "photo", url: "https://t.me/preview_bot/17" },
        { message_id: 18, kind: "video", url: "tg://openmessage?user_id=1&message_id=18" },
      ],
    })).toEqual([
      { message_id: 17, kind: "photo", url: "https://t.me/preview_bot/17" },
      { message_id: 18, kind: "video", url: "tg://openmessage?user_id=1&message_id=18" },
    ]);
    expect(occurrenceMediaLinks({
      message_link: "tg://openmessage?user_id=1&message_id=20",
      preview_message_ids: [18, 19],
    })).toEqual([
      { message_id: 18, kind: "media", url: "tg://openmessage?user_id=1&message_id=18" },
      { message_id: 19, kind: "media", url: "tg://openmessage?user_id=1&message_id=19" },
    ]);
  });

  it("allows only supported links and keeps tg links in the current window", () => {
    const deepLink = "tg://openmessage?user_id=1&message_id=2";
    expect(safeResourceHref(deepLink)).toBe(deepLink);
    expect(safeResourceHref("javascript:alert(1)")).toBeNull();
    expect(linkWindowTarget(deepLink)).toBeUndefined();
    expect(linkWindowTarget("https://t.me/preview_bot")).toBe("_blank");
  });

  it("resolves signed media paths against the configured API", () => {
    expect(apiAssetUrl("/api/message-media?token=signed")).toMatch(
      /^http:\/\/127\.0\.0\.1:\d+\/api\/message-media\?token=signed$/,
    );
    expect(apiAssetUrl("https://cdn.example.test/image.jpg")).toBe("https://cdn.example.test/image.jpg");
  });
});
