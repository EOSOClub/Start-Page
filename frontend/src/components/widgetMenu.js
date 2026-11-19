/* Pure helpers of the grouped "+ Widget" menu. */

/**
 * The widget types as menu sections: [{group, items: [{type, label}]}] in `groups` order, filtered by `query`
 * (case-insensitive, every word must appear in the label, the type key or the group name). Empty sections are dropped.
 */
export function groupWidgetTypes(types, groups, query = "") {
  const words = String(query).toLowerCase().split(/\s+/).filter(Boolean);
  const sections = groups.map((group) => ({ group, items: [] }));
  const other = { group: "Other", items: [] }; // an entry with a group the list does not know still shows up
  for (const [type, def] of Object.entries(types)) {
    const hay = `${def.label} ${type.replace(/_/g, " ")} ${def.group ?? ""}`.toLowerCase();
    if (!words.every((w) => hay.includes(w))) continue;
    (sections.find((s) => s.group === def.group) ?? other).items.push({ type, label: def.label });
  }
  return [...sections, other].filter((s) => s.items.length);
}
