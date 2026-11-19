import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ updateSettings: vi.fn(), notifyTest: vi.fn(), listBackups: vi.fn(async () => []) }));
vi.mock("../api.js", () => ({ api }));

import SettingsModal from "./SettingsModal.jsx";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Notifications: Send test", () => {
  it("reports the saved settings back so the app's own copy is not stale", async () => {
    const saved = { title: "T", notify_type: "ntfy", notify_url: "********", notify_url_set: true };
    api.updateSettings.mockResolvedValue(saved);
    api.notifyTest.mockResolvedValue({ ok: true });
    const onSettingsSaved = vi.fn();
    render(
      <SettingsModal
        settings={{ title: "T", notify_type: "ntfy", notify_url: "https://ntfy.example/t", notify_url_set: false }}
        auth={{ has_password: false }}
        onAuthChanged={vi.fn()}
        onSave={vi.fn()}
        onSettingsSaved={onSettingsSaved}
        onRestored={vi.fn()}
        onClose={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    fireEvent.click(screen.getByRole("button", { name: "Send test" }));
    await waitFor(() => expect(screen.getByText("Test sent.")).toBeTruthy());
    expect(api.updateSettings).toHaveBeenCalledTimes(1);
    expect(onSettingsSaved).toHaveBeenCalledExactlyOnceWith(saved); // the masked URL and notify_url_set, as the server stored them
  });
});
