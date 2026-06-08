export type StoredUser = {
  id: string;
  username: string;
  level: string;
  must_change_password?: boolean;
};

export function getStoredUser(): StoredUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem("user");
    if (!raw) return null;
    return JSON.parse(raw) as StoredUser;
  } catch {
    return null;
  }
}

export function clearAuth() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem("access_token");
  window.localStorage.removeItem("refresh_token");
  window.localStorage.removeItem("user");
}

export function persistAuth(tokens: {
  access_token: string;
  refresh_token: string;
  user: StoredUser;
}) {
  window.localStorage.setItem("access_token", tokens.access_token);
  window.localStorage.setItem("refresh_token", tokens.refresh_token);
  window.localStorage.setItem("user", JSON.stringify(tokens.user));
}
