import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Clock3,
  Coins,
  Copy,
  ExternalLink,
  FileText,
  FileJson2,
  Image as ImageIcon,
  Layers3,
  LocateFixed,
  RefreshCw,
  Search,
  UserRound,
  Video,
} from "lucide-react";
import { TelegramMessageContent } from "./TelegramMessageContent";
import { apiGet } from "../lib/api";
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
  type ResourcePreviewOccurrence,
} from "../lib/resourcePreviews";
import type { TelegramMessage } from "../lib/telegramMessages";

type PreviewAccount = { id: string; name: string };

export type ResourcePreviewFile = {
  id: string;
  path: string;
  label: string;
  exists: boolean;
  resource_count: number | null;
  updated_at: number | null;
  error?: string;
};

type ResourcePreviewPageProps = {
  previews: ResourcePreview[];
  accounts: PreviewAccount[];
  status: string;
  updatedAt: number | null;
  sourcePath: string;
  sourceFiles: ResourcePreviewFile[];
  selectedSource: string;
  proxy: string;
  onRefresh: () => void;
  onSourceChange: (path: string) => void;
  onLocate: (occurrence: ResourcePreviewOccurrence, messageId: number) => void;
};

function formatDate(value?: string | number | null) {
  if (!value) return "";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ResourceAnchor({ href, className, title, children }: {
  href?: string | null;
  className?: string;
  title: string;
  children: ReactNode;
}) {
  const safeHref = safeResourceHref(href);
  if (!safeHref) return null;
  const target = linkWindowTarget(safeHref);
  return (
    <a
      className={className}
      href={safeHref}
      target={target}
      rel={target ? "noreferrer" : undefined}
      title={title}
    >
      {children}
    </a>
  );
}

function accountName(occurrence: ResourcePreviewOccurrence, accounts: PreviewAccount[]) {
  if (occurrence.account_id) {
    return accounts.find((account) => account.id === occurrence.account_id)?.name || occurrence.account_id;
  }
  return occurrence.account_index ? `账号 #${occurrence.account_index}` : "未知账号";
}

function occurrencePreviewCount(occurrence: ResourcePreviewOccurrence) {
  return occurrenceMediaLinks(occurrence).length;
}

const messageContextCache = new Map<string, TelegramMessage[]>();
const messageContextRequests = new Map<string, Promise<TelegramMessage[]>>();

function loadMessageContext(cacheKey: string, path: string) {
  const current = messageContextRequests.get(cacheKey);
  if (current) return current;
  const request = apiGet<{ messages: TelegramMessage[] }>(path)
    .then((payload) => {
      messageContextCache.set(cacheKey, payload.messages);
      return payload.messages;
    })
    .finally(() => {
      if (messageContextRequests.get(cacheKey) === request) {
        messageContextRequests.delete(cacheKey);
      }
    });
  messageContextRequests.set(cacheKey, request);
  return request;
}

function PreviewMessageContext({ occurrence, proxy }: {
  occurrence: ResourcePreviewOccurrence;
  proxy: string;
}) {
  const messageIds = useMemo(
    () => Array.from(new Set([
      ...(occurrence.preview_message_ids || []),
      ...(occurrence.preview_media || []).map((item) => Number(item.message_id || 0)),
      Number(occurrence.source_message_id || 0),
    ].filter((messageId) => messageId > 0))).sort((left, right) => left - right),
    [occurrence],
  );
  const accountId = String(occurrence.account_id || "");
  const peer = String(occurrence.source_peer || "");
  const cacheKey = `${accountId}|${peer}|${messageIds.join(",")}|${proxy}`;
  const [messages, setMessages] = useState<TelegramMessage[]>(() => messageContextCache.get(cacheKey) || []);
  const [status, setStatus] = useState(messageContextCache.has(cacheKey) ? "" : "正在读取消息");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!accountId || !peer || !messageIds.length) {
      setMessages([]);
      setStatus("索引缺少账号或消息 ID");
      return;
    }
    const cached = messageContextCache.get(cacheKey);
    if (cached && !reload) {
      setMessages(cached);
      setStatus("");
      return;
    }
    let active = true;
    setStatus("正在读取消息");
    void loadMessageContext(
      cacheKey,
      `/api/messages?account=${encodeURIComponent(accountId)}&peer=${encodeURIComponent(peer)}&proxy=${encodeURIComponent(proxy)}&ids=${encodeURIComponent(messageIds.join(","))}`,
    ).then((nextMessages) => {
      if (!active) return;
      setMessages(nextMessages);
      setStatus(nextMessages.length ? "" : "没有读取到索引中的消息");
    }).catch((error) => {
      if (!active) return;
      setMessages([]);
      setStatus(error instanceof Error ? error.message : "消息读取失败");
    });
    return () => {
      active = false;
    };
  }, [accountId, cacheKey, messageIds, peer, proxy, reload]);

  if (status) {
    return (
      <div className="resource-context-status">
        <span>{status}</span>
        {status !== "正在读取消息" && accountId && peer && messageIds.length ? (
          <button onClick={() => setReload((value) => value + 1)}>重试</button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="resource-message-context">
      {messages.map((message) => (
        <article
          className={Number(message.id) === occurrence.source_message_id ? "resource-context-message source" : "resource-context-message"}
          key={message.id}
        >
          <header>
            <strong>{message.sender || peer}</strong>
            <span>#{message.id} · {formatDate(message.date)}</span>
          </header>
          <TelegramMessageContent message={message} compact />
        </article>
      ))}
    </div>
  );
}

function DeferredPreviewMessageContext({ occurrence, proxy }: {
  occurrence: ResourcePreviewOccurrence;
  proxy: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setReady(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      setReady(true);
      observer.disconnect();
    }, { rootMargin: "0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="resource-context-deferred" ref={containerRef}>
      {ready ? (
        <PreviewMessageContext occurrence={occurrence} proxy={proxy} />
      ) : (
        <div className="resource-context-placeholder" aria-label="媒体预览等待加载">
          <span /><span /><span />
        </div>
      )}
    </div>
  );
}

function PreviewOccurrence({ occurrence, index, accounts, proxy, onLocate }: {
  occurrence: ResourcePreviewOccurrence;
  index: number;
  accounts: PreviewAccount[];
  proxy: string;
  onLocate: (occurrence: ResourcePreviewOccurrence, messageId: number) => void;
}) {
  const mediaLinks = occurrenceMediaLinks(occurrence);
  const sourceHref = messageHref(occurrence);
  const conversationHref = telegramConversationHref(occurrence.source_peer);
  const canLocate = Boolean(
    occurrence.account_id
      && accounts.some((account) => account.id === occurrence.account_id)
      && occurrence.source_peer
      && occurrence.source_message_id,
  );
  return (
    <section className="resource-occurrence">
      <header className="resource-occurrence-head">
        <div className="resource-occurrence-title">
          <span>{index + 1}</span>
          <div>
            <strong>{accountName(occurrence, accounts)}</strong>
            <small>
              {occurrence.round ? `第 ${occurrence.round} 轮` : "轮次未知"}
              {occurrence.seen_at ? ` · ${formatDate(occurrence.seen_at)}` : ""}
            </small>
          </div>
        </div>
        <div className="resource-occurrence-actions">
          <button
            className="resource-text-link"
            disabled={!canLocate}
            onClick={() => occurrence.source_message_id && onLocate(occurrence, occurrence.source_message_id)}
            title={canLocate ? "使用记录账号在站内定位来源消息" : "旧索引未记录稳定账号 ID，无法精准定位"}
          >
            <LocateFixed aria-hidden="true" />
            站内定位
          </button>
          <ResourceAnchor href={sourceHref} className="resource-text-link secondary" title="使用 Telegram 打开来源消息">
            <ExternalLink aria-hidden="true" />
            Telegram
          </ResourceAnchor>
          {conversationHref && conversationHref !== sourceHref ? (
            <ResourceAnchor href={conversationHref} className="resource-text-link secondary" title="打开来源会话">
              <ExternalLink aria-hidden="true" />
              打开会话
            </ResourceAnchor>
          ) : null}
        </div>
      </header>
      {canLocate ? <DeferredPreviewMessageContext occurrence={occurrence} proxy={proxy} /> : null}
      {mediaLinks.length ? (
        <details className="resource-raw-links">
          <summary>原始消息链接</summary>
          <div className="resource-media-links" aria-label={`第 ${index + 1} 批图片和视频链接`}>
            {mediaLinks.map((media, previewIndex) => {
              const href = media.url || previewMessageHref(occurrence, previewIndex);
              const messageId = media.message_id || occurrence.preview_message_ids?.[previewIndex];
              const label = media.kind === "photo" ? "图片" : media.kind === "video" ? "视频" : "媒体";
              return (
                <div
                  key={`${href || messageId || "preview"}-${previewIndex}`}
                  className="resource-media-link"
                >
                  {media.kind === "photo" ? <ImageIcon aria-hidden="true" /> : <Video aria-hidden="true" />}
                  <div>
                    <strong>{label} {previewIndex + 1}{messageId ? ` · 消息 #${messageId}` : ""}</strong>
                    <code title={href || "未记录链接"}>{href || "未记录链接"}</code>
                  </div>
                  {href ? (
                    <button onClick={() => void navigator.clipboard.writeText(href)} title={`复制${label}链接`}>
                      <Copy aria-hidden="true" />
                    </button>
                  ) : null}
                  <ResourceAnchor href={href} className="resource-icon-link" title={`打开${label}消息`}>
                    <ExternalLink aria-hidden="true" />
                  </ResourceAnchor>
                  {canLocate && messageId ? (
                    <button onClick={() => onLocate(occurrence, messageId)} title={`使用记录账号定位${label}消息`}>
                      <LocateFixed aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </details>
      ) : (
        <div className="resource-occurrence-empty">本批次没有记录预览消息</div>
      )}
    </section>
  );
}

export function ResourcePreviewPage({
  previews,
  accounts,
  status,
  updatedAt,
  sourcePath,
  sourceFiles,
  selectedSource,
  proxy,
  onRefresh,
  onSourceChange,
  onLocate,
}: ResourcePreviewPageProps) {
  const [query, setQuery] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const filtered = useMemo(
    () => previews.filter((preview) => resourcePreviewMatches(preview, query)),
    [previews, query],
  );
  const selected = filtered.find((preview) => preview.resource_key === selectedKey) || filtered[0];
  const occurrences = useMemo(
    () => selected ? resourcePreviewOccurrences(selected) : [],
    [selected],
  );
  const previewCount = useMemo(
    () => previews.reduce((total, preview) => (
      total + resourcePreviewOccurrences(preview).reduce(
        (subtotal, occurrence) => subtotal + occurrencePreviewCount(occurrence),
        0,
      )
    ), 0),
    [previews],
  );

  useEffect(() => {
    if (selected && selected.resource_key !== selectedKey) setSelectedKey(selected.resource_key);
    if (!selected && selectedKey) setSelectedKey("");
  }, [selected, selectedKey]);

  return (
    <>
      <header className="topbar resource-page-header">
        <div>
          <h1>资源预览</h1>
          <p>按资源浏览全部预览批次，并直接定位来源或任意预览消息。</p>
        </div>
        <div className="resource-page-actions">
          <label className="resource-index-picker">
            <span><FileJson2 aria-hidden="true" />索引文件</span>
            <select value={selectedSource} onChange={(event) => onSourceChange(event.target.value)}>
              {sourceFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.label}{file.exists ? ` · ${file.resource_count ?? "?"} 项` : " · 尚未生成"}
                </option>
              ))}
            </select>
          </label>
          <div className="resource-page-stat"><strong>{previews.length}</strong><span>资源</span></div>
          <div className="resource-page-stat"><strong>{previewCount}</strong><span>预览消息</span></div>
          <button onClick={onRefresh} title="刷新资源预览">
            <RefreshCw aria-hidden="true" />
            刷新
          </button>
        </div>
      </header>

      <section className="resource-browser" aria-label="资源预览浏览器">
        <aside className="resource-catalog">
          <div className="resource-catalog-tools">
            <label className="resource-search">
              <Search aria-hidden="true" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索名称、资源 key、账号或来源"
              />
            </label>
            <div className="resource-catalog-meta">
              <span>显示 {filtered.length} / {previews.length}</span>
              <span>{updatedAt ? `更新于 ${formatDate(updatedAt)}` : status}</span>
            </div>
          </div>
          <div className="resource-catalog-list">
            {filtered.map((preview) => {
              const batches = resourcePreviewOccurrences(preview);
              const count = batches.reduce((total, occurrence) => total + occurrencePreviewCount(occurrence), 0);
              return (
                <button
                  className={preview.resource_key === selected?.resource_key ? "resource-catalog-item active" : "resource-catalog-item"}
                  key={preview.resource_key}
                  onClick={() => setSelectedKey(preview.resource_key)}
                >
                  <span className="resource-catalog-item-head">
                    <strong>{preview.description || preview.button_text || preview.resource_key}</strong>
                    <small>{preview.unlock_cost ? `${preview.unlock_cost} 积分` : "免费"}</small>
                  </span>
                  <code>{preview.resource_key}</code>
                  <span className="resource-catalog-item-foot">
                    <small>{batches.length} 批 · {count} 条预览</small>
                    <small>{preview.source_peer || "未知来源"}</small>
                  </span>
                </button>
              );
            })}
            {!filtered.length ? (
              <div className="resource-browser-empty">
                <Layers3 aria-hidden="true" />
                <strong>{query ? "没有匹配的资源" : "暂无资源预览"}</strong>
                <span>{query ? "尝试搜索资源 key、描述或账号" : status}</span>
              </div>
            ) : null}
          </div>
        </aside>

        <main className="resource-detail">
          {selected ? (
            <>
              <header className="resource-detail-head">
                <div>
                  <span className="resource-detail-eyebrow">资源详情</span>
                  <h2>{selected.description || selected.button_text || selected.resource_key}</h2>
                  <code>{selected.resource_key}</code>
                </div>
                <div className="resource-detail-actions">
                  <button onClick={() => void navigator.clipboard.writeText(selected.resource_key)} title="复制资源 key">
                    <Copy aria-hidden="true" />
                    复制 key
                  </button>
                  <button
                    className="resource-primary-link"
                    disabled={!occurrences[0]?.account_id || !accounts.some((account) => account.id === occurrences[0]?.account_id) || !occurrences[0]?.source_peer || !occurrences[0]?.source_message_id}
                    onClick={() => occurrences[0]?.source_message_id && onLocate(occurrences[0], occurrences[0].source_message_id)}
                    title={occurrences[0]?.account_id ? "使用首次记录账号精准定位来源" : "旧索引未记录稳定账号 ID"}
                  >
                    <LocateFixed aria-hidden="true" />
                    站内定位
                  </button>
                </div>
              </header>

              <div className="resource-detail-scroll">
                <section className="resource-facts" aria-label="资源统计">
                  <div><ImageIcon aria-hidden="true" /><span><strong>{selected.photos || 0}</strong>图片</span></div>
                  <div><Video aria-hidden="true" /><span><strong>{selected.videos || 0}</strong>视频</span></div>
                  <div><FileText aria-hidden="true" /><span><strong>{selected.files || 0}</strong>文件</span></div>
                  <div><Coins aria-hidden="true" /><span><strong>{selected.unlock_cost || 0}</strong>积分</span></div>
                  <div><Layers3 aria-hidden="true" /><span><strong>{occurrences.length}</strong>预览批次</span></div>
                  <div><Clock3 aria-hidden="true" /><span><strong>{selected.seen_count || occurrences.length}</strong>命中次数</span></div>
                </section>

                {selected.summary_text ? (
                  <section className="resource-description">
                    <h3>资源摘要</h3>
                    <p>{selected.summary_text}</p>
                  </section>
                ) : null}

                <section className="resource-batches">
                  <div className="resource-section-title">
                    <div>
                      <h3>全部预览批次</h3>
                      <p>同一资源由不同账号或轮次捕获时，所有批次都会保留。</p>
                    </div>
                    <span><UserRound aria-hidden="true" />{occurrences.length} 批</span>
                  </div>
                  {occurrences.map((occurrence, index) => (
                    <PreviewOccurrence
                      key={`${occurrence.account_id || occurrence.account_index || "account"}-${occurrence.source_message_id || "message"}-${occurrence.round || index}`}
                      occurrence={occurrence}
                      index={index}
                      accounts={accounts}
                      proxy={proxy}
                      onLocate={onLocate}
                    />
                  ))}
                </section>

                {sourcePath ? <div className="resource-source-path" title={sourcePath}>索引文件：{sourcePath}</div> : null}
              </div>
            </>
          ) : (
            <div className="resource-browser-empty detail">
              <Layers3 aria-hidden="true" />
              <strong>选择一个资源查看预览</strong>
              <span>左侧会显示索引中的所有资源。</span>
            </div>
          )}
        </main>
      </section>
    </>
  );
}
