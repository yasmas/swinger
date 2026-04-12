/**
 * Human-readable bot title for the UI (tabs, headers).
 * API routes still use the qualified id `owner:trader_name` in `bot.name`.
 */
export function botDisplayLabel(bot) {
  if (!bot) return "";
  const dn = bot.displayName != null && String(bot.displayName).trim();
  if (dn) {
    const q = dn.indexOf(":");
    return q === -1 ? dn : dn.slice(q + 1).trim();
  }
  const n = String(bot.name || "");
  const i = n.indexOf(":");
  return i === -1 ? n : n.slice(i + 1).trim();
}

/** Safe filename fragment (no colons). */
export function botFileSlug(bot) {
  const s = botDisplayLabel(bot).replace(/[/\\?%*:|"<>]/g, "-").trim() || "bot";
  return s;
}
