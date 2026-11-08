const BASE = import.meta.env.VITE_API_URL || "";

// Each visitor gets a random private id kept in this browser. It scopes their chat history,
// so people sharing the same link never see each other's chats (and no login is needed).
const fallbackId = crypto.randomUUID();
function clientId() {
  try {
    let id = localStorage.getItem("df_client_id");
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem("df_client_id", id);
    }
    return id;
  } catch {
    return fallbackId; // storage blocked: history lasts until the tab closes
  }
}

async function req(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      ...options,
      headers: { "Content-Type": "application/json", "X-Client-Id": clientId(), ...options.headers },
    });
  } catch {
    throw new Error("Cannot reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(typeof detail === "string" && detail ? detail : `Request failed (${res.status})`);
  }
  return res.json();
}

export const listChats = () => req("/api/chats");
export const createChat = () => req("/api/chats", { method: "POST" });
export const getChat = (id) => req(`/api/chats/${id}`);
export const deleteChat = (id) => req(`/api/chats/${id}`, { method: "DELETE" });
export const renameChat = (id, title) =>
  req(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
export const sendMessage = (id, content) =>
  req(`/api/chats/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) });
