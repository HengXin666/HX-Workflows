export const API = import.meta.env.VITE_TG_WEB_API || "http://127.0.0.1:8765";

export class ApiError<T = unknown> extends Error {
  status: number;
  payload: T | null;

  constructor(message: string, status: number, payload: T | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    try {
      const payload = JSON.parse(text) as { error?: string };
      throw new ApiError(payload.error || text, response.status, payload);
    } catch (error) {
      if (error instanceof SyntaxError) throw new ApiError(text || `HTTP ${response.status}`, response.status, null);
      throw error;
    }
  }
  return response.json() as Promise<T>;
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  return parseResponse<T>(await fetch(`${API}${path}`, init));
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return parseResponse<T>(
    await fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function apiDelete<T>(path: string): Promise<T> {
  return parseResponse<T>(await fetch(`${API}${path}`, { method: "DELETE" }));
}
