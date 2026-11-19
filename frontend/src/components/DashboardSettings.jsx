import { useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import { SNAP_MODES, gridOptions } from "./Canvas.jsx";
import { COLS_SM_MAX, phoneOptions } from "../phone.js";

const FIELDS = [
  { key: "name", label: "Name", type: "text" },
  { key: "columns", label: "Grid columns", type: "number", help: "Width of the grid in cells. Widgets keep their cell coordinates; more columns = smaller cells." },
  { key: "row_height", label: "Row height (px)", type: "number" },
  { key: "gap", label: "Gap between widgets", type: "range", min: 0, max: 40, unit: "px" },
  { key: "snap", label: "Snap while dragging", type: "select", options: SNAP_MODES, help: "Finer snapping lets widgets sit between grid cells. Positions are kept in cells, so switching modes never moves anything." },
  { key: "show_grid", label: "Show grid lines in edit mode", type: "checkbox" },
  {
    key: "phone_layout", label: "Phone layout", type: "select",
    options: [{ value: "auto", label: "Automatic (widgets stack)" }, { value: "custom", label: "Custom (edit it in Phone mode)" }],
    help: "In edit mode, the Phone button next to Desktop edits the phone layout. Pages start out automatic.",
  },
  { key: "columns_sm", label: "Phone grid columns", type: "number", min: 1, max: COLS_SM_MAX, showIf: (v) => v.phone_layout === "custom", help: "Width of the phone grid in cells (1-12, default 4)." },
  { key: "background_image", label: "Background image (this page)", type: "text", placeholder: "(use global setting)" },
  { key: "background_dim", label: "Background dim", type: "range", min: 0, max: 90, unit: "%", showIf: (v) => !!v.background_image },
  { key: "background_blur", label: "Background blur", type: "range", min: 0, max: 30, unit: "px", showIf: (v) => !!v.background_image },
];

export default function DashboardSettings({ dashboard, canDelete, onSave, onDelete, onExport, onClose }) {
  const [value, setValue] = useState(() => {
    // Seed from the normalized settings so bad stored values can't crash the form; Save then repairs them.
    const o = gridOptions(dashboard);
    return {
      name: dashboard.name,
      columns: dashboard.columns,
      row_height: dashboard.row_height,
      gap: o.gap,
      snap: o.snap,
      show_grid: o.showGrid,
      phone_layout: phoneOptions(dashboard).custom ? "custom" : "auto",
      columns_sm: phoneOptions(dashboard).colsSm,
      background_image: o.background_image || "",
      background_dim: o.background_dim ?? 40,
      background_blur: o.background_blur ?? 0,
    };
  });
  return (
    <Modal
      title="Dashboard settings"
      onClose={onClose}
      footer={
        <>
          {canDelete && (
            <button
              className="btn-danger"
              onClick={() => confirm(`Delete dashboard "${dashboard.name}" and all its widgets?`) && onDelete()}
            >
              Delete dashboard
            </button>
          )}
          {onExport && <button onClick={onExport} title="Download this page as a shareable .startpage-page.json file">Export page</button>}
          <span className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button
            className="btn-primary"
            onClick={() =>
              onSave({
                name: value.name || dashboard.name,
                columns: Math.max(4, value.columns || 24),
                row_height: Math.max(10, value.row_height || 40),
                settings: {
                  ...(dashboard.settings || {}),
                  gap: Math.max(0, Number(value.gap) || 0),
                  snap: value.snap,
                  show_grid: !!value.show_grid,
                  phone_layout: value.phone_layout === "custom" ? "custom" : "auto",
                  columns_sm: Math.min(COLS_SM_MAX, Math.max(1, Math.round(Number(value.columns_sm)) || 4)),
                  background_image: value.background_image || "",
                  background_dim: value.background_dim,
                  background_blur: value.background_blur,
                },
              })
            }
          >
            Save
          </button>
        </>
      }
    >
      <ConfigForm fields={FIELDS} value={value} onChange={setValue} />
    </Modal>
  );
}
