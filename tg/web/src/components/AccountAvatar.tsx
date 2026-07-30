import type { Account } from "../lib/accounts";

export function AccountAvatar({ account }: { account: Account }) {
  if (account.avatar) return <img className="avatar" src={account.avatar} alt="" />;
  return <div className="avatar fallback">{account.role === "main" ? "M" : "A"}</div>;
}
