import { Bot, BotMessageSquare, Database, GitBranch, Home, MessagesSquare, Play, Workflow } from "lucide-react";

export type Page = "home" | "chat" | "flow" | "run" | "resources" | "bots" | "github";

const items = [
  { page: "home" as const, label: "账号中心", icon: Home },
  { page: "chat" as const, label: "对话调试", icon: MessagesSquare },
  { page: "flow" as const, label: "工作流编排", icon: Workflow },
  { page: "run" as const, label: "本地执行", icon: Play },
  { page: "resources" as const, label: "资源预览", icon: Database },
  { page: "bots" as const, label: "机器人编排", icon: BotMessageSquare },
  { page: "github" as const, label: "GitHub 管理", icon: GitBranch },
];

export function AppSidebar({ page, onNavigate }: { page: Page; onNavigate: (page: Page) => void }) {
  return (
    <aside className="sidebar app-sidebar">
      <div className="brand">
        <div className="mark"><Bot aria-hidden="true" /></div>
      </div>
      <nav aria-label="主导航">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <a
              className={page === item.page ? "active" : ""}
              href={`/${item.page}`}
              key={item.page}
              onClick={(event) => { event.preventDefault(); onNavigate(item.page); }}
            >
              <Icon aria-hidden="true" />
              <span>{item.label}</span>
            </a>
          );
        })}
      </nav>
      <div className="sidebar-note">
        <strong>凭证仅存本地</strong>
        <span>只有在 GitHub 页面确认后才会通过 gh 写入 Secrets。</span>
      </div>
    </aside>
  );
}
