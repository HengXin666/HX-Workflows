export type Role = "main" | "alt";

export type Account = {
  id: string;
  role: Role;
  name: string;
  username: string;
  telegram_id: string;
  avatar: string;
  source: string;
  masked_session: string;
};

export type QrJob = {
  id: string;
  role: Role;
  status: string;
  url?: string;
  qr_image?: string;
  account?: Account;
  error?: string;
};
