import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";

// Resolves an icon string to something renderable:
//  - "" / undefined  -> the site's favicon when `url` is given (and auto favicons are on),
//                       otherwise the first letter of fallback
//  - "http(s)://..." -> image URL
//  - "/..."           -> local path
//  - an emoji        -> rendered as text
//  - "plex"          -> dashboard-icons name (https://github.com/walkxcode/dashboard-icons)
const DASHBOARD_ICONS = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/";

const isEmoji = (s) => /\p{Extended_Pictographic}/u.test(s) && s.length <= 4;

function resolveSrc(icon, url, autoFavicons) {
  if (!icon) return url && autoFavicons ? api.faviconUrl(url) : null;
  if (isEmoji(icon)) return null;
  if (/^(https?:)?\/\//.test(icon) || icon.startsWith("/") || icon.startsWith("data:")) return icon;
  return `${DASHBOARD_ICONS}${icon.toLowerCase().replace(/\s+/g, "-")}.png`;
}

export default function Icon({ icon, url, fallback = "?", size, className = "" }) {
  const { settings } = useApp();
  const src = resolveSrc(icon, url, settings?.auto_favicons !== false);
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);

  const style = size ? { width: size, height: size, fontSize: size * 0.8 } : undefined;
  if (icon && isEmoji(icon)) {
    return (
      <span className={`icon icon-emoji ${className}`} style={style}>
        {icon}
      </span>
    );
  }
  if (!src || failed) {
    return (
      <span className={`icon icon-letter ${className}`} style={style}>
        {(fallback || "?").charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      className={`icon icon-img ${className}`}
      style={style}
      src={src}
      alt=""
      loading="lazy"
      draggable={false}
      onError={() => setFailed(true)}
    />
  );
}
